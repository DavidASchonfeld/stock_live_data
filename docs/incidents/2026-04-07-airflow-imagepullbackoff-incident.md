# Airflow ImagePullBackOff Incident — April 7, 2026

## What Went Wrong

After upgrading Airflow from version 2.x to 3.x, all Airflow pods got stuck in an `ImagePullBackOff` state. This meant Kubernetes was unable to pull the Docker images needed to run Airflow, so the pods could not start. Meanwhile, the Flask dashboard pod (in the `default` namespace) was running fine, which initially made the problem confusing.

The Airflow UI was completely unreachable, and no Airflow jobs could run.

---

## Root Causes

The incident had **three separate but interconnected causes** that all needed to be fixed:

### 1. Obsolete Docker Images

The Helm chart for Airflow 1.20.0 was defaulting to old Bitnami-maintained images:
- `docker.io/bitnami/airflow:3.0.5-debian-12-r0`
- `docker.io/bitnami/redis:8.2.1-debian-12-r0`

These specific versions no longer exist on Docker Hub (Bitnami had removed them), so Kubernetes would fail to pull them with a "404 Not Found" error. The pod would enter `ImagePullBackOff` and retry indefinitely.

**Why this happened:** The Bitnami images are third-party maintained and get deprecated over time. When you upgrade the Helm chart, it doesn't automatically upgrade the image references — the new chart version just tries to pull whatever the old default was, which no longer exists.

### 2. Invalid Livenessprobe Configuration

The `airflow/helm/values.yaml` file had a `scheduler/livenessProbe` section with incorrect Kubernetes syntax:

```yaml
livenessProbe:
  command:  # ❌ WRONG — Kubernetes doesn't recognize "command" at this level
    - sh
    - -c
    - "..."
```

The correct Kubernetes syntax requires an `exec` wrapper:

```yaml
livenessProbe:
  exec:  # ✓ CORRECT
    command:
      - sh
      - -c
      - "..."
```

**Why this happened:** The YAML syntax was copied from an older Airflow deployment template that predated the current Kubernetes API. Over time, Kubernetes tightened its validation, and this invalid syntax was no longer accepted.

### 3. Kubernetes Resource Caching

Even after fixing the `values.yaml` file, the StatefulSet and Deployment objects in Kubernetes had already been created with the old (broken) image specifications. Helm doesn't automatically delete and recreate these objects — it only patches them. The patching was not sufficient to force a re-pull of the new images.

**Why this happened:** Kubernetes is designed to be conservative with changes. Once a resource is created, subsequent updates are applied as patches, not full replacements. This is usually good (prevents accidental data loss), but in this case it meant the old pod templates persisted.

---

## How the Errors Were Fixed

### Fix #1: Override with Official Images

In `airflow/helm/values.yaml`, added explicit image overrides to use the official Apache Airflow and Redis images:

```yaml
images:
  airflow: apache/airflow:3.1.8          # Official Apache image
  redis: redis:7.2-bookworm              # Official Redis image
```

These official images are maintained by the Apache Airflow and Redis projects respectively, have long-term support, and are guaranteed to exist on Docker Hub.

### Fix #2: Correct the Livenessprobe Syntax

In `airflow/helm/values.yaml`, removed the invalid `command` section and replaced it with proper Kubernetes `exec` syntax:

```yaml
livenessProbe:
  exec:
    command:
      - sh
      - -c
      - "/usr/bin/env bash -c 'airflow jobs check --job-type SchedulerJob --hostname \"$(hostname)\"'"
```

This tells Kubernetes the right way to run the health check.

### Fix #3: Force Kubernetes to Rebuild Pods

In `scripts/deploy.sh`, added the `--force` flag to the Helm upgrade command:

```bash
helm upgrade airflow oci://registry-1.docker.io/bitnamicharts/airflow \
  -n airflow-my-namespace \
  -f /home/ubuntu/airflow/helm/values.yaml \
  --force \  # ← This forces Helm to delete and recreate all pods
  --wait
```

The `--force` flag tells Helm: "Don't just patch the existing resources — delete them and recreate them from scratch." This ensures that the new image specifications are actually used.

---

## Why These Fixes Work

### Why Fix #1 Works (Official Images)

The official Apache Airflow and Redis Docker images are maintained by their respective projects and are published to Docker Hub under stable version tags. Unlike the Bitnami versions, they have:

- **Long-term availability**: Official images stay available for years, whereas vendor-specific versions get rotated out
- **Guaranteed compatibility**: Apache's own Airflow image is guaranteed to match the Airflow software version
- **Regular updates**: Security patches and bug fixes are applied promptly

When Kubernetes tries to pull `apache/airflow:3.1.8`, it will always find the image and succeed.

### Why Fix #2 Works (Corrected Syntax)

The Kubernetes API specification requires that pod health checks be structured with explicit `exec` wrappers when running shell commands. By fixing the syntax to match the API specification, the livenessProbe configuration becomes valid, and Kubernetes accepts it without validation errors.

This is important because invalid YAML causes Helm to fail during the template rendering phase, preventing the entire Helm upgrade from completing.

### Why Fix #3 Works (Force Flag)

The `--force` flag solves the resource caching problem by telling Helm to:
1. Delete all existing Airflow pods (StatefulSets, Deployments, etc.)
2. Wait for them to fully terminate
3. Recreate them from scratch with the new specifications

Once the pods are recreated, they are created with the correct image references and updated configuration. Kubernetes' scheduler can now successfully pull the official Apache images and start the pods.

The `--wait` flag ensures the script doesn't finish until all pods are actually running, so we know the deployment succeeded.

---

## The Chain of Dependencies

These three fixes had to work together:

1. **Fix #1 alone wouldn't work**: Even with correct image names, the old pods would still be trying to use the old (broken) Bitnami images due to caching.

2. **Fix #2 alone wouldn't work**: Even with valid YAML syntax, the image pull would still fail because the Bitnami images don't exist.

3. **Fix #3 alone wouldn't work**: Without the image override, Helm would recreate pods with the old Bitnami image references, which still don't exist.

All three fixes were necessary, and when combined, they completely solved the problem.

---

## Verification

After deploying the fixes, all Airflow pods transitioned to `Running` state:

```
NAME                         READY   STATUS    RESTARTS   AGE
airflow-scheduler-0          2/2     Running   0          2m
airflow-triggerer-0          2/2     Running   0          2m
airflow-dag-processor        2/2     Running   0          2m
airflow-api-server           1/1     Running   0          2m
```

The Airflow UI became accessible at `http://localhost:30080`, and DAGs were visible and executable.

---

## Lessons Learned

1. **Prefer official images over vendor-specific ones**: Third-party Docker images (like Bitnami) can be deprecated. Official project images (Apache, Redis, etc.) have long-term support.

2. **Version pinning vs. automatic updates**: By pinning the Helm chart to version 1.20.0, we avoided breaking changes, but this meant we also inherited its defaults. Explicit image overrides in `values.yaml` insulate us from those defaults.

3. **Validate infrastructure as code**: The invalid livenessProbe YAML should have been caught during local testing before deployment. Adding schema validation to the CI/CD pipeline would catch this.

4. **Use `--force` carefully but definitively**: The `--force` flag is reserved for situations where you know the old state is broken and need a clean slate. It should always be paired with `--wait` to ensure the new state is actually healthy.

5. **Test upgrade paths**: Before upgrading major versions (Airflow 2.x → 3.x), test the full upgrade locally to catch configuration issues early.

---

## Timeline

| Time | Event |
|------|-------|
| T+0 | Airflow Helm upgrade initiated |
| T+2m | Helm upgrade appears successful, but pods enter `ImagePullBackOff` |
| T+5m | Dashboard pod works, but Airflow UI unreachable → indicates namespace-specific issue |
| T+15m | Identified missing `imagePullSecrets` for ECR credentials |
| T+25m | Added ECR credentials fix, but `ImagePullBackOff` persists → indicates image availability issue |
| T+40m | Identified obsolete Bitnami images and invalid YAML syntax |
| T+50m | Applied all three fixes (image overrides, syntax correction, force flag) |
| T+55m | All Airflow pods Running, UI accessible |

---

## Files Modified

- `airflow/helm/values.yaml` — Added image overrides, fixed livenessProbe syntax, added imagePullSecrets
- `scripts/deploy.sh` — Added `--force` flag to Helm upgrade, fixed Python validation for nested DAG directories
