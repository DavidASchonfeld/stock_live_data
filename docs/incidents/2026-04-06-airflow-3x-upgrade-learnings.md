# What I Learned: The Airflow 3.x Upgrade Disaster (and Recovery)
**Date:** 2026-04-06
**Duration:** ~6 hours across two sessions
**Outcome:** All pods running on Airflow 3.1.8 ✅

---

## The Short Version

A single `helm upgrade` command without a version pin accidentally upgraded Airflow from 2.9.3 to 3.1.8. The database got upgraded to the new format before the upgrade failed, making it impossible to roll back. Then four more attempts to complete the upgrade all timed out for reasons that had nothing to do with the upgrade itself — they all failed because of a single missing secret that wasn't obvious at first. Once the root cause was found, it took about 10 minutes to fix. Three small config changes and everything worked.

---

## Part 1: How It Started — The Accidental Upgrade

### What happened

This command was run:

```bash
helm upgrade airflow apache-airflow/airflow \
  -n airflow-my-namespace \
  -f ~/airflow/helm/values.yaml \
  --timeout 10m --wait
```

Notice what's missing: `--version`.

Without a version number, Helm grabbed the **latest available version** of the chart. The latest happened to be Airflow 3.1.8 (chart version 1.20.0). The cluster was running Airflow 2.9.3 (chart version 1.15.0). That's a **major version jump** that normally takes careful planning.

### What Helm does during an upgrade

When you run `helm upgrade`, Helm doesn't just swap config files. It runs a sequence:

1. **Before anything starts**: runs a "pre-upgrade hook" — a one-time job that prepares the system for the new version. For Airflow, this is a **database migration job** that upgrades the internal database schema to match the new software.
2. **Then**: applies all the new Kubernetes manifests (creates/updates pods, services, secrets, etc.)
3. **Then waits**: keeps watching until all the new pods are healthy and ready
4. **If the wait times out**: marks the upgrade as "failed" and stops

The migration job ran and **succeeded** — it upgraded the database from the Airflow 2.x format to the Airflow 3.x format. But then the pods never became ready within the 10-minute window, so the upgrade was marked as "failed."

### Why rolling back was impossible

The database had already been upgraded to the Airflow 3.x schema. Airflow has a rule: **it can upgrade its database, but it cannot downgrade it.** The migration that ran is a one-way street.

When we tried rolling back to Airflow 2.9.3 (chart 1.15.0), Airflow 2.9.3 looked at the database and said: "This schema is newer than what I understand. I refuse to start." The upgrade revision was marked as failed, but so was the rollback.

**The situation:** database is in Airflow 3.x format. Can't run 2.9.3. Must run 3.x. No choice.

### The lesson

Always pin the version:

```bash
# Dangerous — grabs whatever is latest today:
helm upgrade airflow apache-airflow/airflow -f values.yaml

# Safe — explicit about what you're upgrading to:
helm upgrade airflow apache-airflow/airflow --version 1.20.0 -f values.yaml
```

---

## Part 2: Four Failed Upgrade Attempts (All the Same Root Cause)

After accepting that we had to go to Airflow 3.x, four attempts to complete the upgrade were made over several hours. All four timed out. Looking at the error messages, the symptoms looked different each time — some pods were in `CreateContainerConfigError`, some were in `Init:CrashLoopBackOff`, the migration job never appeared, and nothing ever got healthy.

**The actual root cause of all four failures was the same thing: a single missing Kubernetes secret.**

Here's what was really happening, and how we found it.

---

## Part 3: The Root Cause — The Missing Secret That Broke Everything

### What a Kubernetes Secret is

In Kubernetes, a **Secret** is a locked box that stores sensitive values like passwords and encryption keys. Pods read from these boxes to get their configuration. When you define a pod, you can say "this environment variable should come from Secret X, key Y."

If the Secret you're pointing to doesn't exist, the pod can't start. Kubernetes calls this a `CreateContainerConfigError` — "I tried to configure this container but something I needed wasn't there."

### What the missing secret was

Every Airflow pod (including the migration job pod) had this in its configuration:

```
AIRFLOW__WEBSERVER__SECRET_KEY → from secret 'airflow-webserver-secret-key'
```

This env var is Airflow's session encryption key — used to keep people from forging login sessions on the web UI. In Airflow **2.x**, the Helm chart automatically created a secret named `airflow-webserver-secret-key` to hold this value.

In Airflow **3.x**, the webserver was redesigned. The equivalent secret is now called `airflow-api-secret-key`. The 3.x Helm chart creates `airflow-api-secret-key` — but it does **not** create `airflow-webserver-secret-key` anymore. That old secret name is gone.

But there's a catch. The chart has a setting called `enableBuiltInSecretEnvVars` that controls which environment variables get injected into pods. One of the entries is:

```yaml
enableBuiltInSecretEnvVars:
  AIRFLOW__WEBSERVER__SECRET_KEY: true   ← this is the DEFAULT
```

Because this defaults to `true`, every pod spec was generated with:

```
"Please put AIRFLOW__WEBSERVER__SECRET_KEY into this pod's environment.
Fetch the value from the secret named 'airflow-webserver-secret-key'."
```

But that secret doesn't exist in Airflow 3.x. Result: every pod had `CreateContainerConfigError` and could never start.

### Why this broke the migration job too

You might think: "OK, the webserver pods are broken. But the database migration job runs first — shouldn't it be unaffected?"

No. The migration job is also an Airflow pod. It gets the same environment variable injection. So the migration job pod also had `CreateContainerConfigError`. It also couldn't start.

No migration job → database never migrated → init containers (which wait for migration to complete) wait 60 seconds and crash → `Init:CrashLoopBackOff` for every pod.

### The cascade

One wrong default setting caused a complete cascade:

```
enableBuiltInSecretEnvVars.AIRFLOW__WEBSERVER__SECRET_KEY = true (default)
    ↓
Every pod spec says: "get AIRFLOW__WEBSERVER__SECRET_KEY from 'airflow-webserver-secret-key'"
    ↓
'airflow-webserver-secret-key' doesn't exist in Airflow 3.x
    ↓
Every pod gets CreateContainerConfigError (can't start at all)
    ↓
Migration job also gets CreateContainerConfigError (can't start)
    ↓
Database is never migrated
    ↓
All other pods' init containers wait 60s for migration → give up → crash
    ↓
All pods crash-loop indefinitely
    ↓
helm upgrade waits 15 minutes for pods to get healthy → times out → "UPGRADE FAILED"
```

Four attempts over several hours, all hitting this same wall.

### How it was found

The breakthrough was running `kubectl describe pod airflow-scheduler-0` and reading the events at the bottom of the output:

```
Warning  Failed  3m30s (x135 over 33m)  kubelet  Error: secret "airflow-webserver-secret-key" not found
```

And from the init container logs:

```
TimeoutError: There are still unapplied migrations after 60 seconds.
MigrationHead(s) in DB: {'686269002441'} | Migration Head(s) in Source Code: {'509b94a1042d'}
```

The first message showed what was blocking the scheduler. The second showed that the migration was never running (the database was still at the old version). Connecting the dots: migration job also blocked by missing secret → migration never runs → init container timeout.

### The fix

One line added to `airflow/helm/values.yaml`:

```yaml
enableBuiltInSecretEnvVars:
  AIRFLOW__WEBSERVER__SECRET_KEY: false
```

This tells the chart: "don't inject that env var into pod specs." Airflow 3.x uses `AIRFLOW__API__SECRET_KEY` from `airflow-api-secret-key` instead — that was being created correctly and working fine. The 2.x env var was a leftover that served no purpose in 3.x but broke everything by pointing to a nonexistent secret.

With this one change:
- Migration job could start → ran `airflow db migrate` → database migrated to 3.x format (`686269002441` → `509b94a1042d`)
- Init containers detected migration complete → passed → main containers started
- Scheduler, api-server, dag-processor, triggerer all came up

---

## Part 4: The Scheduler Kept Dying — OOMKilled

### What happened

After fixing the missing secret, the scheduler started for the first time. But about 3 minutes later it crashed. Then started again. Crashed again. Looking at the events:

```
Reason: OOMKilled
Exit Code: 137
```

**OOMKilled** = "Out Of Memory Killed." The Linux kernel force-killed the process because it used more RAM than allowed.

### Why Airflow 3.x uses more memory than 2.x

Airflow 3.x changed how the scheduler works internally:

**Airflow 2.x scheduler:** one process. It runs all its work in a single Python process. Low memory, simple model.

**Airflow 3.x scheduler:** a "supervisor" model. One main process spawns approximately 15 worker subprocesses. Each worker process handles individual task runs. Each subprocess loads the entire Airflow codebase plus all provider packages (the add-ons for AWS, Snowflake, databases, etc.) into memory when it starts.

So at startup:
- Airflow 2.x: ~200-400 MB total
- Airflow 3.x: ~15 workers × ~80-120 MB each = 1.2-1.8 GB just for the scheduler

The memory limit in `values.yaml` was `1Gi` — sized for the old 2.x single-process model. With 3.x spawning 15 workers, it was almost guaranteed to exceed 1 GB.

### The fix

Changed the scheduler memory limit in `values.yaml`:

```yaml
scheduler:
  resources:
    limits:
      memory: "2Gi"   # was 1Gi
```

2 Gi gives headroom for the worker startup spike. After settling in, the scheduler uses much less than 2 Gi in steady state — but needs it during that initial burst.

---

## Part 5: The Health Check That Kept Killing the Scheduler

### What health probes are

Kubernetes runs regular health checks on every pod. These are called **probes**. There are two kinds relevant here:

- **Startup probe**: runs while the pod is first starting up. If the pod fails these checks too many times in a row, Kubernetes kills and restarts it. This exists to give slow-starting pods a window to get ready without being killed prematurely.
- **Liveness probe**: runs periodically on a running pod forever. If the pod fails these checks too many times in a row, Kubernetes kills and restarts it. This exists to restart pods that have gotten stuck or frozen.

For the Airflow scheduler, both probes run this command:

```bash
airflow jobs check --job-type SchedulerJob --local
```

This command asks: "Is there a healthy scheduler job registered in the database?" If yes, exit with success. If no (or if it crashes, or if it times out), exit with failure.

### Why the probe kept timing out in Airflow 3.x

The probe had a **timeout of 20 seconds**. If the command doesn't complete within 20 seconds, it counts as a failure.

In Airflow 2.x, `airflow jobs check` ran quickly — it connected to the database, made a simple query, and returned in ~5 seconds.

In Airflow 3.x, the same command loads the full Airflow codebase and all provider packages before it does anything. On a t3.large, this takes **30-45 seconds**.

So the probe ran, loaded providers for 30 seconds, then got killed by the 20-second timeout. The probe reported "failure" even though the scheduler itself was working fine. After 5 consecutive failures, Kubernetes killed the scheduler pod and restarted it. Then the same thing happened again.

This is why even after fixing the OOMKill (with 2 Gi memory), the scheduler kept restarting — different cause, same symptom.

### The fix

Increased the timeout for both probes in `values.yaml`:

```yaml
scheduler:
  startupProbe:
    failureThreshold: 10
    periodSeconds: 30
    timeoutSeconds: 45   # was 20 — Airflow 3.x provider loading takes 30-45s

  livenessProbe:
    timeoutSeconds: 45   # was 20 — same reason
```

With 45 seconds, the probe completes reliably before the timeout. The scheduler has been stable since.

---

## Part 6: Other Things That Went Wrong During the Process

### The `--reuse-values` trap

One of the early failed upgrade attempts used `--reuse-values`:

```bash
helm upgrade airflow apache-airflow/airflow \
  --version 1.20.0 \
  --reuse-values \          ← this caused extra problems
  -f ~/airflow/helm/values.yaml \
  --timeout 15m --wait
```

`--reuse-values` tells Helm: "keep all the settings from the last successful deployment, and only override what's in my values file."

The problem: the "last successful deployment" was the old 1.15.0 / Airflow 2.9.3 release. That release had internal settings like `enableBuiltInSecretEnvVars`, `ports._rpcServer`, and `workers.containerLifecycleHooks` that existed in 2.9.3 but were removed in 3.x. When `--reuse-values` injected those old settings, the 3.x chart rejected them with schema validation errors:

```
additional properties 'AIRFLOW__CORE__SQL_ALCHEMY_CONN' not allowed
additional properties '_rpcServer' not allowed
```

**The fix:** drop `--reuse-values`. All configuration lives in `values.yaml`. There's nothing in the old release state worth preserving that isn't already in the file.

### The pending-upgrade lock

One of the failed upgrade sessions left Helm in a locked state. When we tried to run a new upgrade:

```
Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress
```

What happened: the previous `helm upgrade` process was still running on EC2 from an earlier session (it had a 20-minute timeout and was still waiting). When we tried to run a new upgrade, Helm saw the lock and refused.

**Two things needed to happen:**

1. Kill the old helm process that was still running on EC2:
   ```bash
   ssh ec2-stock "ps aux | grep helm | grep -v grep"
   # Found PID 348735 still running
   ssh ec2-stock "kill 348735"
   ```

2. Delete the "pending-upgrade" Helm state secret that held the lock:
   ```bash
   ssh ec2-stock "kubectl delete secret sh.helm.release.v1.airflow.v22 -n airflow-my-namespace"
   ```

After both steps, the next upgrade attempt ran cleanly.

### StatefulSet pods not automatically recycling

When we ran `helm upgrade`, it updated the **StatefulSet** (the Kubernetes object that manages the scheduler and triggerer pods). The StatefulSet's template was updated with the new config — no more `AIRFLOW__WEBSERVER__SECRET_KEY` reference.

But the old pods were still running with their old spec. Normally, Kubernetes performs a rolling update — it kills the old pod and creates a new one. But if the old pod is stuck in `CreateContainerConfigError` or `CrashLoopBackOff`, the rolling update can get stuck waiting for the current pod to be healthy before proceeding.

The fix was to manually delete the pods, which forces Kubernetes to immediately recreate them with the new spec:

```bash
kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace
kubectl delete pod airflow-triggerer-0 -n airflow-my-namespace
```

StatefulSets automatically recreate deleted pods. The new pods used the updated spec from the helm upgrade.

### The 2-minute timeout vs. the migration job startup time

Even after fixing the missing secret, several `helm upgrade --timeout 2m --wait` attempts showed `UPGRADE FAILED: post-upgrade hooks failed`. This was because the migration job (a pre-upgrade hook) takes more than 2 minutes to start — it has to `pip install pymysql` before doing anything else (from the `_PIP_ADDITIONAL_REQUIREMENTS` setting in `values.yaml`).

The fix: use `--atomic=false` along with a short timeout. `--atomic=false` means Helm won't automatically roll back if the wait times out — it just marks the release as "failed" but leaves all the resources in place. Since the resources themselves were correct, this worked fine: the migration job ran to completion in the background, the pods started up, and the cluster settled into a healthy state.

---

## Part 7: The Full Timeline of What Was Fixed

| Problem | Root Cause | Fix |
|---------|-----------|-----|
| Accidental 2.x → 3.x upgrade | `helm upgrade` without `--version` pin | Nothing to undo; moved forward to 3.x |
| Every pod `CreateContainerConfigError` | `enableBuiltInSecretEnvVars.AIRFLOW__WEBSERVER__SECRET_KEY: true` default references a 3.x-removed secret | Added `AIRFLOW__WEBSERVER__SECRET_KEY: false` to `values.yaml` |
| Migration job never ran | Same as above — migration job pod also blocked by missing secret | Fixed by same change |
| Init containers crash-looping | Migration never ran, so they waited forever | Fixed by same change (migration ran once pods could start) |
| Scheduler OOMKilled every 3 minutes | Airflow 3.x spawns 15 worker subprocesses; 1 Gi limit sized for 2.x single-process model | Raised scheduler memory limit to `2Gi` |
| Scheduler killed by health probe | `airflow jobs check` takes 30-45s in 3.x; probe timeout was 20s | Raised `timeoutSeconds` from 20 → 45 for startup and liveness probes |
| `--reuse-values` schema errors | Old 2.x release state injected removed 3.x settings | Dropped `--reuse-values` from upgrade command |
| Helm pending-upgrade lock | Previous upgrade process still running from old session | Killed old process + deleted pending-upgrade Helm secret |
| StatefulSet pods stuck with old spec | Rolling update stuck on unhealthy pods | Manually deleted pods to force recreate |

---

## Part 8: Key Lessons

**1. Always pin Helm chart versions.**
`helm upgrade` without `--version` is like `pip install <package>` without a version — you're trusting the latest release to not break you. Major version bumps in Helm charts (especially Airflow) are not backward compatible.

**2. When every pod fails at once, look for a shared dependency.**
Individual pods fail for individual reasons. When ALL pods fail with the same error at the same time, something they all depend on is broken — in this case, a secret they all referenced. Don't debug each pod in isolation; find the common thread.

**3. After a major version upgrade, read the migration guide.**
Airflow 2.x → 3.x changed a lot: new process model, renamed components, removed secrets, new settings. Limits and probes sized for 2.x need to be recalibrated for 3.x. What worked before is a starting point, not a guarantee.

**4. StatefulSet pods don't always self-heal after a config change.**
Deployments roll out changes automatically. StatefulSets are more cautious — they won't replace a pod until the current one is healthy. If a pod is stuck in an error state, you may need to delete it manually to get it recreated with the new config.

**5. `kubectl describe pod` events are the fastest path to root cause.**
When diagnosing pod failures, scroll to the `Events:` section at the bottom of `kubectl describe pod`. It shows exactly what Kubernetes tried and what failed, with timestamps. That's where we found `Error: secret "airflow-webserver-secret-key" not found` after hours of other diagnosis.

**6. The init container crash is usually a symptom, not the cause.**
`Init:CrashLoopBackOff` means "the init container failed repeatedly." In Airflow's case, the init container waits for a database migration. Debugging the init container itself is a dead end — the real question is why the migration job didn't run.

**7. `--atomic=false` is useful for unstable upgrades.**
The default `--atomic` mode rolls back automatically if the upgrade times out. This is usually good, but during a migration recovery where you're iterating on fixes, it just adds noise. `--atomic=false` lets you apply the changes and observe the result without triggering automatic rollback.
