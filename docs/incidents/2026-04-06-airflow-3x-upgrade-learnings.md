# What I Learned: The Airflow 3.x Upgrade Disaster (and Recovery)
**Date:** 2026-04-05 → 2026-04-06
**Duration:** ~6 hours (upgrade recovery) + follow-up session (UI fix)
**Outcome:** All pods running on Airflow 3.1.8, UI accessible ✅

---

## TL;DR

A single `helm upgrade` command without a version pin accidentally upgraded Airflow from 2.9.3 to 3.1.8. The database got upgraded to the new format before the upgrade failed, making it impossible to roll back. Then four more attempts to complete the upgrade all timed out for reasons that had nothing to do with the upgrade itself — they all failed because of a single missing secret that wasn't obvious at first. Once the root cause was found, it took about 10 minutes to fix. Three small config changes and all pods came up.

Then, in a follow-up session, a fourth problem appeared: all pods were running but the Airflow UI at port 30080 was still dropping connections. This turned out to be a service selector that was still pointing at a pod label from Airflow 2.x — a label that no longer exists in 3.x. One line changed, applied in seconds, and the UI came up. While investigating, a second issue was found: the api-server was crash-looping for the exact same probe timeout reason that had already been fixed for the scheduler but hadn't been applied to the api-server. Same fix, same result.

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

---

## Part 9: The UI Was Still Broken After All Pods Came Up

### What happened

Even after fixing the missing secret, the OOMKill, and the probe timeouts — all pods were showing `Running` — there was still one more problem. Opening `http://localhost:30080` in the browser gave:

> "Safari can't open the page because the server unexpectedly dropped the connection."

The Airflow UI was completely unreachable. The Flask dashboard on port 32147 was loading fine, which meant the SSH tunnel was working. The problem was somewhere inside Kubernetes.

---

### Part 9a: Why the UI Wasn't Reachable — The Service Selector

#### How Kubernetes routes traffic to pods

When you type `http://localhost:30080` in your browser, here's what happens:

1. Your browser connects through the SSH tunnel to port 30080 on EC2
2. On EC2, port 30080 belongs to a Kubernetes **Service** — a resource whose job is to receive traffic and forward it to the right pod
3. The Service finds the right pod using a **selector** — a set of labels that it looks for on pods
4. If a pod has matching labels, the Service sends traffic there. If no pod matches, the Service has no destination and drops the connection immediately

Think of the Service as a receptionist. When a call comes in, she checks her directory for someone with the right job title. If no one has that title, she hangs up — she doesn't ring anyone's desk.

#### What the selector said vs. what actually existed

The Kubernetes Service for the Airflow UI was defined in `airflow/manifests/service-airflow-ui.yaml`. It had:

```yaml
selector:
  component: webserver
  release: airflow
```

This means: "find a pod that has both `component=webserver` AND `release=airflow` in its labels."

Here's the problem: **in Airflow 3.x, there is no `webserver` pod.**

In Airflow 2.x, there was a pod called the "webserver" that served the UI. In Airflow 3.x, the team split and reorganized how the software works. The UI and the REST API are now both served by a single pod called the **api-server**. The old webserver was absorbed into it.

So when the Service looked for a pod with `component=webserver`, it found nothing. The service had no endpoints — no destination to send traffic to. Every connection attempt was immediately dropped.

You can see this with:

```bash
kubectl get endpoints airflow-service-expose-ui-port -n airflow-my-namespace
# Result: ENDPOINTS = <none>
```

`<none>` means the selector matched zero pods. And a note that was already in the file said exactly what needed to happen:

```yaml
component: webserver  # Airflow 2.x label; update to api-server if/when upgrading to Airflow 3.x
```

The TODO was written correctly. It just never got acted on during the upgrade.

#### Why helm upgrade didn't fix this automatically

You might wonder: "If the upgrade changed everything else, why didn't it fix the Service?"

The answer is that the Service was **not created by Helm**. It was created manually with `kubectl apply -f service-airflow-ui.yaml` when the cluster was first set up. Helm only manages resources that it created. It has no knowledge of this manually-created Service, so it never touches it.

After the Airflow 3.x upgrade changed the pod labels, the Service just sat there pointing at a label that no longer existed — and nobody noticed until the browser refused to connect.

#### The fix

One word changed in `service-airflow-ui.yaml`:

```yaml
selector:
  component: api-server  # Airflow 3.x label (was: webserver — 2.x only)
  release: airflow
```

Then the updated file was applied to the cluster:

```bash
kubectl apply -f service-airflow-ui.yaml -n airflow-my-namespace
```

Kubernetes processes the change instantly. Within a second, the Service re-evaluated its selector against all running pods, found the `api-server` pod, and added it as an endpoint. The next browser request went through immediately.

```bash
kubectl get endpoints airflow-service-expose-ui-port -n airflow-my-namespace
# Result: ENDPOINTS = 10.42.0.146:8080  ✅
```

---

### Part 9b: The api-server Was Also Crash-Looping — Same Root Cause as the Scheduler

While looking at the pods, the api-server showed `CrashLoopBackOff` — the same pattern the scheduler had before its probe timeout was fixed.

#### The same root cause, a different pod

Recall from Part 5: the scheduler's health probe had a 20-second timeout. In Airflow 3.x, loading all the providers before responding takes 30-45 seconds. So the probe timed out, Kubernetes declared the scheduler unhealthy, and killed it — even though the scheduler itself was working fine.

The same fix was applied to the scheduler in `values.yaml`:

```yaml
scheduler:
  startupProbe:
    timeoutSeconds: 45   # raised from 20
  livenessProbe:
    timeoutSeconds: 45   # raised from 20
```

But `apiServer` was left at the old values:

```yaml
apiServer:
  startupProbe:
    timeoutSeconds: 20   # ← still 20 — never updated
```

The api-server loads the exact same provider packages at startup. It takes the same 30-45 seconds to be ready to respond. With a 20-second timeout on its startup probe, Kubernetes would ask "are you ready?" and the api-server wouldn't finish answering in time. Kubernetes marked it as failed, killed it, and restarted it. This repeated in a loop.

The pod events showed the exact same fingerprint as the scheduler had:

```
Warning  Unhealthy  Startup probe failed: context deadline exceeded (Client.Timeout exceeded while awaiting headers)
Warning  BackOff    Back-off restarting failed container api-server
```

"Context deadline exceeded" is the technical way of saying "I asked, you didn't answer in time, I gave up."

#### The fix

The same timeout adjustment that was applied to the scheduler was applied to the api-server, plus a liveness probe configuration to prevent false-positive kills during normal operation:

```yaml
apiServer:
  startupProbe:
    failureThreshold: 18
    periodSeconds: 10
    timeoutSeconds: 45   # raised from 20 — provider loading takes 30-45s
  livenessProbe:
    initialDelaySeconds: 10
    timeoutSeconds: 45   # same reason
    failureThreshold: 5
    periodSeconds: 60
```

This was applied by syncing `values.yaml` to EC2 and running `helm upgrade`. The Helm upgrade triggered a rolling restart of the api-server pod with the new probe configuration. The new pod started, completed its provider loading in ~40 seconds, responded to the startup probe successfully, and stayed up with 0 restarts.

---

### Part 9c: Preventing This in Future Deploys

One more small problem was identified: `deploy.sh` was not applying the Airflow service manifest. It synced the file to EC2 but never ran `kubectl apply` on it. So even though the fix was committed to Git, a future redeploy wouldn't automatically push the corrected selector to the cluster.

A new step was added to `deploy.sh`:

```bash
echo "=== Step 2e: Applying Airflow service manifest ==="
ssh "$EC2_HOST" "kubectl apply -f $EC2_HOME/airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace"
```

Now every deploy keeps the service definition in sync with whatever is in Git.

---

## Part 10: Updated Full Timeline

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
| Airflow UI unreachable (connection dropped) | Service selector `component: webserver` matched nothing — webserver pod doesn't exist in 3.x | Updated selector to `component: api-server` in `service-airflow-ui.yaml` and applied to cluster |
| api-server CrashLoopBackOff | Same probe timeout issue as scheduler — 20s timeout, but 3.x provider loading takes 30-45s | Raised `timeoutSeconds` to 45 for api-server startup and liveness probes in `values.yaml` |
| Service selector fix not persisted through future deploys | `deploy.sh` synced the manifest file but never ran `kubectl apply` on it | Added Step 2e to `deploy.sh` to apply the Airflow service manifest on every deploy |

---

## Part 11: Additional Key Lessons

**8. Helm only manages what Helm created.**
Resources applied manually with `kubectl apply` are invisible to Helm. When an upgrade changes pod labels, services, or other cluster objects that were created outside of Helm — they won't be updated automatically. After any major version upgrade, audit your manually-applied manifests and check whether their selectors, ports, or labels still match the new pod structure.

**9. Fix one pod, check all similar pods.**
When the scheduler probe timeout was raised to 45 seconds, the api-server wasn't checked. Both pods load the same provider packages at startup, so both had the same problem. When you fix a timeout or resource limit for one Airflow component, look at whether all other components are configured the same way.

**10. A "connection dropped" error from a working SSH tunnel usually means no endpoints.**
If your SSH tunnel is confirmed working (another port on the same tunnel responds) but one port drops connections, the first thing to check is `kubectl get endpoints <service-name>`. An empty endpoint list means the service selector matched nothing — a label mismatch, not a networking problem.

---

## Part 12: Follow-Up — SDK Import Migration (Deprecation Warnings)

After all pods came up and the UI was accessible, the Airflow task logs and dag-processor logs showed a stream of `DeprecationWarning` messages on every DAG parse cycle:

```
WARNING - The `airflow.decorators.dag` attribute is deprecated. Please use `airflow.sdk.dag`.
WARNING - The `airflow.decorators.task` attribute is deprecated. Please use `airflow.sdk.task`.
WARNING - Using Variable.get from `airflow.models` is deprecated. Please use `airflow.sdk.Variable` instead.
WARNING - Using Variable.delete from `airflow.models` is deprecated. Please use `airflow.sdk.Variable` instead.
```

### What happened

In Airflow 3.x, the public DAG-authoring API was consolidated into `airflow.sdk`. The old import paths still work as compatibility shims but emit deprecation warnings on every parse, polluting the scheduler and dag-processor logs. The DAGs themselves continued running — these were warnings, not errors.

### Files changed

| File | Lines | Old import | New import |
|------|-------|------------|------------|
| `dag_stocks.py` | 10–11 | `from airflow.decorators import dag, task` + `from airflow.models.xcom_arg import XComArg` | `from airflow.sdk import dag, task, XComArg` |
| `dag_weather.py` | 9–10 | same as above | `from airflow.sdk import dag, task, XComArg` |
| `dag_staleness_check.py` | 7 | `from airflow.decorators import dag, task` | `from airflow.sdk import dag, task` |
| `dag_utils.py` | 5 | `from airflow.models import Variable` | `from airflow.sdk import Variable` |
| `alerting.py` | 5 local imports | `from airflow.models import Variable` | `from airflow.sdk import Variable` |

**Not changed:** `from airflow.exceptions import AirflowSkipException` — this stays in `airflow.exceptions` in 3.x and does not need updating.

### Lesson learned

**11. After a major version upgrade, scan your DAGs for deprecated import paths.**
The warnings won't break anything immediately, but they signal that the compatibility shims will eventually be removed. In Airflow 3.x, all DAG-authoring primitives (`dag`, `task`, `XComArg`, `Variable`) live under `airflow.sdk`. Run a quick `grep -r "airflow.decorators\|airflow.models" airflow/dags/` after any major Airflow upgrade to catch these early.
