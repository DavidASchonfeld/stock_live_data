# Incident: MLflow `pkg_resources` Fix Not Propagating to Scheduler Pod

**Date:** 2026-04-10
**Component:** Airflow scheduler pod — image rollout via K3S + Helm

---

## Error

```
ModuleNotFoundError: No module named 'pkg_resources'
```

Persisted in the scheduler pod **after** `setuptools` had already been added to the Dockerfile and `./scripts/deploy.sh` had been run.

## How It Was Encountered

During post-deploy verification:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- /opt/ml-venv/bin/mlflow --version
```

The Dockerfile already contained the `setuptools` fix and deploy had just run successfully — yet the same error appeared, indicating the running pod was still using the old image.

## Root Cause

Two compounding issues prevented the fix from reaching the live pod:

1. **Helm upgrade silently swallowed by `|| echo`**: The `helm upgrade` command in `deploy.sh` was followed by `|| echo "Note: Helm hook timed out..."`. This catch-all suppressed *any* non-zero exit from helm — not just hook timeouts. If helm failed to update the StatefulSet spec, the script continued without error, and the pod restart in Step 7 relaunched the scheduler using the **old image tag** (the one built before `setuptools` was added).

2. **K3S containerd snapshot cache**: The prior fix used a single `k3s ctr images rm <BUILD_TAG>` before import. Because `BUILD_TAG` is a fresh timestamp, that tag never existed yet — so the removal was a no-op. Older airflow-dbt images remained in K3S containerd. K3S resolves image layers by content hash; if any layer's hash matched an older cached snapshot, K3S could serve stale layer data even when pulling a "new" image.

## Fix

Three targeted changes:

**1. `airflow/docker/Dockerfile` — fail-fast build verification**

Added an import check immediately after the `pip install` step:
```dockerfile
&& /opt/ml-venv/bin/python -c "import pkg_resources" \
```
If `setuptools` is missing or broken, the Docker build now fails loudly at build time instead of producing a silently broken image.

**2. `scripts/deploy.sh` — wildcard K3S image purge before import**

Replaced the single-tag removal with a purge of *all* existing `airflow-dbt` images:
```bash
sudo k3s ctr images ls | grep 'airflow-dbt' | awk '{print $1}' | xargs -r sudo k3s ctr images rm 2>/dev/null || true
```
This eliminates any old snapshot that K3S could have reused during layer resolution.

**3. `scripts/deploy.sh` — StatefulSet image verification after helm upgrade**

After the helm upgrade, the script now reads the actual image tag set in the StatefulSet spec and compares it to `BUILD_TAG`. If they differ, it force-patches the StatefulSet directly:
```bash
kubectl set image statefulset/airflow-scheduler scheduler=airflow-dbt:$BUILD_TAG -n airflow-my-namespace
```
This guarantees the correct image tag is in the spec before the pod is restarted in Step 7, regardless of whether helm succeeded or timed out.

## Why This Fix

Each change addresses one distinct failure mode in the deploy pipeline:

- The build verification converts a silent runtime failure into a loud build failure — easier to diagnose.
- The wildcard purge eliminates any possibility of K3S serving cached layer snapshots from pre-fix images.
- The StatefulSet patch closes the gap where a silently-failing helm upgrade left the pod spec pointing at the wrong image.

Together they ensure that after `./scripts/deploy.sh` runs, the scheduler pod is guaranteed to be running the image that was just built.
