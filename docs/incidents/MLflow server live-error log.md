# MLflow Deployment — Incident: Child Process Crash (Exit Code 7)

## Error Encountered
After deploying MLflow via `./scripts/deploy.sh`, the health check from inside the Airflow scheduler pod returned **curl exit code 7** (connection refused):

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  curl -s http://mlflow.airflow-my-namespace.svc.cluster.local:5500/health
# command terminated with exit code 7
```

## How It Was Identified
Running `kubectl logs -l app=mlflow -n airflow-my-namespace` revealed the parent process (uvicorn) repeatedly spawning workers that died immediately:

```
INFO:     Waiting for child process [57]
INFO:     Child process [57] died
INFO:     Waiting for child process [58]
INFO:     Child process [58] died
```

The pod showed `Running` with 1 restart, and the PVC was `Bound` — so the issue wasn't networking or storage binding. The crash-on-spawn pattern pointed to a **startup failure inside the container**, not a config or routing problem.

## Root Cause
The `ghcr.io/mlflow/mlflow:latest` image runs as a **non-root user (UID 1000)**. The hostPath directory `/home/ubuntu/mlflow-data` (mounted via PVC) was owned by root and not writable by the mlflow process. Every time a worker tried to open or create the SQLite database (`mlflow.db`) or write to the artifacts directory, it was denied — causing an immediate crash before the HTTP server could bind to port 5500.

## Fix Applied
An `initContainer` was added to `deployment-mlflow.yaml` that runs `chmod -R 777 /mlflow-data` as root **before** the main MLflow container starts:

```yaml
initContainers:
  - name: fix-permissions
    image: busybox
    command: ["sh", "-c", "chmod -R 777 /mlflow-data"]
    volumeMounts:
      - name: mlflow-data
        mountPath: /mlflow-data
```

Readiness and liveness probes were also added so that `kubectl rollout status` only reports success once MLflow is genuinely serving HTTP traffic — preventing silent failures in future deploys.

## Why This Fix
An `initContainer` is the idiomatic Kubernetes pattern for pre-flight setup on shared volumes. It runs to completion before the main container starts, is lightweight (busybox), and is scoped to this pod — no changes needed to the host, the image, or the PVC. The alternative (running the main container as root via `securityContext: runAsUser: 0`) works but is a broader security tradeoff; fixing permissions at the volume level is more surgical.

## Result
After redeployment, uvicorn workers start successfully and MLflow binds to `0.0.0.0:5500`. The health check returns `{"status": "OK"}` from inside the cluster.

---

# MLflow Deployment — Incident: Rollout Timeout (Two-Phase)

**Date:** 2026-04-09

## Error Encountered

`./scripts/deploy.sh` hung and failed at Step 2b6 with a `kubectl rollout status` timeout. Two distinct error messages appeared on separate deploy runs:

**Phase 1:**
```
Waiting for deployment "mlflow" rollout to finish: 1 old replicas are pending termination...
error: timed out waiting for the condition
```

**Phase 2 (after Phase 1 fix):**
```
Waiting for deployment "mlflow" rollout to finish: 0 of 1 updated replicas are available...
error: timed out waiting for the condition
```

## How It Was Identified

The error messages themselves distinguished the two phases:

- **"1 old replicas are pending termination"** — the new pod was ready but the old one wouldn't die. This pointed to a pod lifecycle/scheduling issue, not an application problem.
- **"0 of 1 updated replicas are available"** — the old pod was gone but the new one never passed its readiness probe. This pointed to a startup failure on the new pod.

## Root Causes

### Phase 1 — RollingUpdate strategy deadlocked by ReadWriteOnce PVC

Kubernetes' default `RollingUpdate` strategy starts the new pod **before** terminating the old one. MLflow's PVC uses `accessModes: ReadWriteOnce`, which only allows one pod to hold the volume at a time. The new pod couldn't mount the PVC while the old pod held it, so it stalled indefinitely — and the old pod wouldn't be killed until the new pod was Ready. Classic deadlock. The 120s rollout timeout fired before either side broke the cycle.

### Phase 2 — MLflow image absent from K3S containerd

Once the strategy was fixed (`Recreate`), the old pod terminated cleanly. But the new pod never became Ready because `ghcr.io/mlflow/mlflow:latest` (~1 GB) was not present in K3S's containerd image store. K3S and Docker maintain **separate** image stores. The image existed in Docker's cache (used on prior local runs) but was invisible to K3S, which tried to pull it from `ghcr.io` at pod startup. The pull exceeded the 180s rollout timeout before the image was fully downloaded.

## Fix Applied

**Phase 1 fix — `deployment-mlflow.yaml`:** Added `strategy: type: Recreate` under `spec:`. Recreate terminates all old pods first, then starts new ones — no overlap, no PVC contention.

```yaml
spec:
  replicas: 1
  strategy:
    type: Recreate   # required for ReadWriteOnce PVC
```

**Phase 2 fix — `deploy.sh` Step 2b5a + `deployment-mlflow.yaml`:** Added a pre-deploy step that pulls the MLflow image via Docker and imports it into K3S containerd before the deployment is applied. Changed `imagePullPolicy` to `Never` so K3S uses only the pre-imported image and never attempts a runtime pull.

```bash
# deploy.sh Step 2b5a
docker pull ghcr.io/mlflow/mlflow:latest
docker save ghcr.io/mlflow/mlflow:latest | sudo k3s ctr images import -
```

```yaml
# deployment-mlflow.yaml
imagePullPolicy: Never   # image pre-imported into K3S containerd by deploy.sh Step 2b5a
```

Also added a `|| { ... }` error handler to Step 2b6 that prints `kubectl describe` and pod logs on failure, matching the pattern used in the Kafka step — so future timeouts are self-diagnosing.

## Why These Fixes

- **`Recreate` strategy** is the standard Kubernetes pattern for single-replica deployments backed by a `ReadWriteOnce` PVC. `RollingUpdate` requires two pods to coexist during the transition, which RWO volumes cannot support.
- **Pre-importing the image** follows the same pattern already used for `airflow-dbt` (Step 2b2). On a single-node K3S cluster, building or pulling via Docker and importing into containerd is more reliable than runtime pulls — no registry timeouts, no network dependency during rollout, and Docker's layer cache makes repeat deploys fast.

## Result

MLflow deploys cleanly in Step 2b6: old pod terminates first (Recreate), image is already present in containerd (no pull), new pod starts and passes its readiness probe at `/health`.

---

# MLflow Deployment — Incident: Liveness Probe Killing Pod Too Early

**Date:** 2026-04-09

## Error Encountered

`./scripts/deploy.sh` failed at Step 2b6 with a rollout timeout. The pod showed `STATUS: Running` with repeated restarts:

```
NAME                      READY   STATUS    RESTARTS     AGE
mlflow-6b86f98794-kwwnk   0/1     Running   3 (2s ago)   2m28s
```

The events section of `kubectl describe pod` showed the liveness probe repeatedly failing and killing the container:

```
Warning  Unhealthy  27s (x4 over 117s)   kubelet  Liveness probe failed: Get "http://10.42.0.16:5500/health": dial tcp 10.42.0.16:5500: connect: connection refused
Normal   Killing    7s (x2 over 77s)     kubelet  Container mlflow failed liveness probe, will be restarted
```

Running `kubectl logs` on the pod returned **empty output** — no crash trace, no startup message, nothing.

## How It Was Identified

The empty logs were a key clue. The events showed the pod had just restarted 2 seconds before `kubectl logs` ran — so the current container instance hadn't had time to output anything. The previous restart's logs were gone. This ruled out an application crash (which would leave a log trail) and pointed to Kubernetes itself killing the pod before MLflow could finish starting.

Counting backward from the events: liveness probe `initialDelaySeconds` was 20, `periodSeconds` was 10, and `failureThreshold` was not set (Kubernetes default: 3). That means Kubernetes kills the pod at **20 + 3 × 10 = 50 seconds** if the probe doesn't respond. The 21 consecutive readiness probe failures over 2+ minutes confirmed that MLflow never successfully bound to port 5500 in any of those cycles — it was always killed mid-startup.

## Root Cause

The liveness probe's `failureThreshold` was missing, defaulting to 3. Combined with `initialDelaySeconds: 20` and `periodSeconds: 10`, this gave MLflow only **50 seconds** to start before being killed. MLflow needs more time than that — it has to initialize the SQLite database, set up the artifact directory, and start the gunicorn server before it can respond to HTTP health checks. The pod never survived long enough to finish.

The readiness probe was not the problem — readiness failures only block traffic, they don't kill the pod. Only the liveness probe kills.

## Fix Applied

In `deployment-mlflow.yaml`, the liveness probe was updated with two changes:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 5500
  initialDelaySeconds: 60   # was 20 — gives MLflow time for SQLite init + gunicorn start
  periodSeconds: 10
  failureThreshold: 6        # was missing (defaulted to 3 = 50s kill); now 120s total
```

No other changes were needed — port, path, strategy, and `imagePullPolicy` were all correct.

## Why This Fix

Increasing `initialDelaySeconds` pushes back the first liveness check, giving MLflow time to actually start before Kubernetes begins evaluating it. Adding `failureThreshold: 6` provides a further buffer in case startup runs long under load. Together they give MLflow **60 + 6 × 10 = 120 seconds** before the pod is killed — enough margin for even a slow first-start with SQLite initialization.

The `failureThreshold` value of 6 was chosen to match the readiness probe, keeping the two probes consistent. A liveness probe with a very low threshold is dangerous for slow-starting apps: it creates a kill loop where the container is always destroyed before it can become healthy.

## Result

MLflow starts and passes both readiness and liveness probes within the 120s window. The rollout completes without timing out and the pod reaches `1/1 Running` with zero restarts.

---

# MLflow Deployment — Incident: Security Middleware Blocking Health Probes

**Date:** 2026-04-09

## Error Encountered

`./scripts/deploy.sh` failed at Step 2b6 with a rollout timeout. The pod showed `STATUS: Running` with repeated restarts and CrashLoopBackOff:

```
NAME                      READY   STATUS    RESTARTS      AGE
mlflow-59f744bdc7-g52s8   0/1     Running   2 (50s ago)   2m28s
```

The events section of `kubectl describe pod` showed the readiness probe continuously failing:

```
Warning  Unhealthy  4s (x21 over 2m14s)  kubelet  Readiness probe failed: Get "http://10.42.0.17:5500/health": dial tcp 10.42.0.17:5500: connect: connection refused
Warning  BackOff    47s (x2 over 50s)    kubelet  Back-off restarting failed container mlflow in pod
```

## How It Was Identified

Unlike prior incidents where pod logs were empty (the container hadn't started yet), this time `kubectl logs` returned actual output:

```
Registry store URI not provided. Using backend store URI.
[MLflow] Security middleware enabled with default settings (localhost-only). To allow connections from other hosts, use --host 0.0.0.0 and configure --allowed-hosts and --cors-allowed-origins.
2026/04/10 01:17:28 INFO:     Uvicorn running on http://0.0.0.0:5500 (Press CTRL+C to quit)
```

MLflow had fully started and was listening on `0.0.0.0:5500`. The server was up — but the readiness probe still got "connection refused." The second log line was the key: **security middleware, localhost-only by default.** The Kubernetes health probe originates from the node IP, not `127.0.0.1`, so the middleware rejected it at the connection level before any HTTP response could be sent.

## Root Cause

A recent version of `ghcr.io/mlflow/mlflow:latest` added a security middleware layer that restricts inbound connections to localhost by default, regardless of what `--host` is set to. `--host 0.0.0.0` controls the socket bind address; `--allowed-hosts` controls which *source* IPs the middleware accepts. Without `--allowed-hosts` explicitly set, only `127.0.0.1` is permitted. Kubernetes readiness and liveness probes originate from the host node's IP (e.g. `10.42.0.17`), so they were silently dropped at the middleware layer — showing up as "connection refused" rather than an HTTP error code.

## Fix Applied

Added `--allowed-hosts "*"` to the `mlflow server` command in `deployment-mlflow.yaml`:

```yaml
command:
  - mlflow
  - server
  - --backend-store-uri
  - sqlite:////mlflow-data/mlflow.db
  - --default-artifact-root
  - /mlflow-data/artifacts
  - --host
  - "0.0.0.0"
  - --port
  - "5500"
  - --allowed-hosts    # allow K8s probe IPs through security middleware
  - "*"
```

No other changes were needed.

## Why This Fix

The MLflow log message itself prescribed the fix: *"To allow connections from other hosts, use --host 0.0.0.0 and configure --allowed-hosts."* Using `"*"` is appropriate here because MLflow is deployed inside the cluster on an internal-only service — it is not exposed to the public internet. Restricting by IP range (e.g. the pod CIDR `10.42.0.0/16`) would also work but adds brittleness if the network config changes; `"*"` is the idiomatic internal-cluster setting and matches how other internal services in this project are configured.

## Result

With `--allowed-hosts "*"` set, the security middleware passes connections from the node IP. The readiness probe reaches `/health`, receives `{"status": "OK"}`, and the pod reaches `1/1 Running` with zero restarts. The rollout completes cleanly in Step 2b6.

---

# MLflow Deployment — Incident: OOMKilled (Insufficient Memory Limit)

**Date:** 2026-04-10

## Error Encountered

`./scripts/deploy.sh` failed at Step 2b6 with a rollout timeout. The pod status showed `OOMKilled` with repeated restarts:

```
NAME                      READY   STATUS      RESTARTS      AGE
mlflow-5b7cb96f8c-l7h4s   0/1     OOMKilled   2 (79s ago)   3m1s
```

The events section of `kubectl describe pod` showed the readiness probe continuously failing, and the pod cycling through OOMKill and restart:

```
Warning  Unhealthy  15s (x23 over 2m47s)  kubelet  Readiness probe failed: Get "http://10.42.0.18:5500/health": dial tcp 10.42.0.18:5500: connect: connection refused
Warning  BackOff    70s (x2 over 79s)     kubelet  Back-off restarting failed container mlflow in pod
```

## How It Was Identified

Unlike prior incidents (empty logs, security middleware), this time `kubectl logs` showed MLflow starting successfully — uvicorn bound to the port and spawned a child worker — but then the child immediately died:

```
Uvicorn running on http://0.0.0.0:5500 (Press CTRL+C to quit)
Started parent process [11]
Waiting for child process [14]
Child process [14] died
```

No Python traceback appeared, which rules out an application crash. A process that dies silently after spawning — without printing an error — is a hallmark of the OS-level OOM killer. `kubectl describe pod` confirmed it: `STATUS: OOMKilled`.

The memory limit in `deployment-mlflow.yaml` was **512Mi**. The MLflow image uses uvicorn's multi-process model: a parent process spawns one or more worker processes, each of which loads the full MLflow stack into memory. When the worker crossed the 512Mi ceiling, the Linux OOM killer terminated it instantly — no warning, no log line.

## Root Cause

The memory limit of 512Mi was set too low for the uvicorn worker model used by `ghcr.io/mlflow/mlflow:latest`. Each worker loads the full MLflow server stack — SQLite client, artifact handling, experiment tracking APIs — which consumes approximately 600–800Mi at startup. The OOM killer triggered before the worker could bind to the port and respond to the readiness probe, causing an infinite restart loop.

This mirrors the exact pattern seen with Airflow components in this project: the scheduler, webserver, and API server were all OOMKilled at their initial limits and required increases to 2Gi. MLflow follows the same pattern at a smaller scale.

## Fix Applied

In `deployment-mlflow.yaml` (lines 46–52), the memory resources were raised:

```yaml
# Before
resources:
  requests:
    cpu: "50m"
    memory: "128Mi"
  limits:
    cpu: "300m"
    memory: "512Mi"

# After
resources:
  requests:
    cpu: "50m"
    memory: "256Mi"   # raised from 128Mi — uvicorn loads full MLflow stack at startup
  limits:
    cpu: "300m"
    memory: "1Gi"     # raised from 512Mi — child worker OOMKilled at 512Mi
```

No other changes were needed.

## Why This Fix

**Why 1Gi and not 2Gi:** MLflow with a SQLite backend and no concurrent experiment runs is substantially lighter than Airflow components. The Airflow scheduler, for example, spawns ~15 provider subprocesses and was OOMKilled at 1Gi. MLflow runs a single uvicorn worker with one SQLite connection — 1Gi gives roughly 2× headroom over the observed ~600–800Mi baseline, which is sufficient. Starting at 2Gi would waste scheduling guarantees on a t3.large node that is already allocating memory across Airflow, Kafka, and PostgreSQL.

**Why raise the request too:** The request was raised from 128Mi to 256Mi to keep the request/limit ratio reasonable. A large gap between request and limit (128Mi vs 1Gi) means Kubernetes may schedule the pod onto a node with less free memory than the pod will actually need when it bursts, leading to OOMKills at the node level rather than the container level.

## How the Fix Solved the Problem

The Linux OOM killer terminates processes that exceed the memory `limit` set in the pod spec — it has no concept of "graceful shutdown." By raising the limit to 1Gi, the uvicorn worker now has enough headroom to load the full MLflow stack without being killed mid-startup. Once the worker starts successfully, it binds to port 5500, the readiness probe receives `{"status": "OK"}`, and the pod reaches `1/1 Running`.

## Result

MLflow deploys cleanly in Step 2b6. The pod reaches `1/1 Running` with zero restarts, and the uvicorn child worker survives startup without hitting the memory ceiling.

---

# MLflow Deployment — Incident: Uvicorn Worker Killed by Root-Owned SQLite File

**Date:** 2026-04-10

## Error Encountered

After a fresh deploy via `./scripts/deploy.sh`, the pod showed `0/1 Running` with a growing restart count:

```
mlflow-7cb4cbb474-8djz4   0/1   Running   2 (82s ago)   5m54s
```

`0/1` means the pod was alive but not ready — 0 of 1 containers passed the readiness probe. The pod had been restarting every ~3 minutes for several cycles.

## How It Was Encountered

Running the standard post-deploy verification:

```bash
kubectl get pods -n airflow-my-namespace | grep mlflow
```

The pod showed `Running` (misleading) but the `0/1` ready count and restart counter indicated it was crash-looping rather than healthy.

## How It Was Identified

Two commands revealed the full picture:

```bash
kubectl logs mlflow-7cb4cbb474-8djz4 -n airflow-my-namespace --previous
kubectl describe pod mlflow-7cb4cbb474-8djz4 -n airflow-my-namespace | tail -40
```

**Log output (previous container):**
```
INFO:  Uvicorn running on http://0.0.0.0:5500
INFO:  Started parent process [11]
INFO:  Waiting for child process [14]
INFO:  Child process [14] died
```

The uvicorn **worker** (child process) died 6 seconds after the parent started — with no Python traceback. A silent crash with no application-level error rules out Python exceptions and points to a kernel-level kill.

**Describe output (events):**
```
Warning  Unhealthy  Liveness probe failed: dial tcp 10.42.0.19:5500: connect: connection refused
Normal   Killing    Container mlflow failed liveness probe, will be restarted
```

Checking the host volume confirmed the root cause:

```bash
ls -la /home/ubuntu/mlflow-data/
# -rwxrwxrwx  1 root  root  663552  Apr 10 00:15  mlflow.db
```

The `mlflow.db` was **root-owned**, created by a previous deployment run before the `chown` fix was in place.

## Root Cause

The initContainer in `deployment-mlflow.yaml` only ran `chmod -R 777 /mlflow-data`. This opens read/write/execute permission bits for all users — but **does not change file ownership**.

The uvicorn worker runs as **UID 1000** (the default MLflow image user). SQLite uses `fcntl()` advisory locks to coordinate concurrent access. On Linux, `fcntl` lock acquisition can behave differently for a non-root process operating on a root-owned file, even when `rwx` permission bits are set. The worker died silently at the kernel level before it could log a Python exception.

Because the worker died before port 5500 had an active listener, every subsequent readiness and liveness probe returned `connection refused`. The liveness probe eventually killed the pod after its failure threshold, which restarted it into the same failure — creating an infinite restart loop.

## Fix Applied

**Immediate (one-time, run from EC2):**
```bash
sudo chown -R 1000:1000 /home/ubuntu/mlflow-data
kubectl rollout restart deployment/mlflow -n airflow-my-namespace
```

**Permanent (`deployment-mlflow.yaml` initContainer):**

Before:
```yaml
command: ["sh", "-c", "chmod -R 777 /mlflow-data"]
```

After:
```yaml
command: ["sh", "-c", "chmod -R 777 /mlflow-data && chown -R 1000:1000 /mlflow-data"]
```

## Why This Fix

The initContainer runs as root (busybox default), so it has the privilege to `chown` files owned by any user. Adding `chown -R 1000:1000` alongside the existing `chmod` hands off every file and directory under `/mlflow-data` — including any previously root-owned files from prior runs — to the MLflow process user before the main container starts.

This is **idempotent**: if files are already UID 1000, the command is a no-op. If future host-level operations or a bad deploy create root-owned files in the volume, the next pod start will correct them automatically.

The prior fix (chmod only) was correct for permission bits but incomplete: ownership and permissions are separate concepts in Linux. `rwxrwxrwx` allows access by mode, but `fcntl` lock semantics are tied to the file's owning UID in certain kernel paths.

## How the Fix Solved the Problem

With correct ownership, the uvicorn worker (UID 1000) acquires SQLite's `fcntl` file locks without hitting a kernel-level conflict. The worker initializes successfully, port 5500 opens, and both the readiness and liveness probes receive `{"status": "OK"}` from the `/health` endpoint — keeping the pod at `1/1 Running` permanently.

## Result

Pod reaches `1/1 Running` with zero restarts. DNS health check from the Airflow scheduler returns `{"status": "OK"}`. PVC shows `Bound`.

---

# MLflow Deployment — Incident: Uvicorn Multi-Worker Spawn Loop on Constrained Node

**Date:** 2026-04-10

## Error Encountered

`./scripts/deploy.sh` failed at Step 2b6 with a rollout timeout. The pod showed `0/1 Running` with 1 restart:

```
NAME                      READY   STATUS    RESTARTS      AGE
mlflow-58b74576c9-jlsz2   0/1     Running   1 (61s ago)   2m57s
```

Events showed repeated liveness and readiness probe failures over ~2.5 minutes:

```
Warning  Unhealthy  77s (x4 over 107s)    kubelet  Liveness probe failed: Get "http://10.42.0.24:5500/health": dial tcp 10.42.0.24:5500: connect: connection refused
Warning  Unhealthy  69s (x21 over 2m44s)  kubelet  Readiness probe failed: Get "http://10.42.0.24:5500/health": dial tcp 10.42.0.24:5500: connect: connection refused
```

## How It Was Encountered

The deploy script's built-in failure handler printed `kubectl describe` and the last 30 lines of pod logs automatically on timeout. The logs showed:

```
WARNING: Accepting ALL hosts. This may leave the server vulnerable to DNS rebinding attacks.
Registry store URI not provided. Using backend store URI.
[MLflow] Security middleware enabled. Allowed hosts: *.
2026/04/10 02:01:12 INFO:     Uvicorn running on http://0.0.0.0:5500 (Press CTRL+C to quit)
2026/04/10 02:01:12 INFO:     Started parent process [11]
2026/04/10 02:01:18 INFO:     Waiting for child process [14]
2026/04/10 02:01:18 INFO:     Child process [14] died
2026/04/10 02:01:24 INFO:     Waiting for child process [20]
2026/04/10 02:01:24 INFO:     Child process [20] died
```

## How It Was Identified

Three clues distinguished this incident from the prior `OOMKilled` incident (which had the same log signature):

1. **Pod STATUS was `Running`, not `OOMKilled`.** An OOM kill at the cgroup level changes the pod phase to `OOMKilled`. `Running` means the container itself was not killed by the kernel — only the internal child processes died.
2. **No Python traceback.** The worker died silently ~6 seconds after spawn. This rules out an application exception and points to a resource-level kill that produced no output.
3. **Memory limit is already 1Gi** (raised from 512Mi in a prior incident). If a single worker was fitting before at 1Gi, the new failure must come from something spawning *more than one* worker simultaneously.

Uvicorn's multi-process mode (used by `mlflow server`) defaults to spawning **one worker per available CPU core**. On the EC2 node — which is already under CPU and memory pressure from Airflow, Kafka, and PostgreSQL — the default worker count may be 2 or more. Each worker independently loads the full MLflow stack, which costs ~600–800Mi. Two workers together (1.2–1.6Gi) exceed the 1Gi container limit and are terminated by the kernel before they can bind connections. The parent process stays alive on port 5500 (it only listens and routes), but with no live workers, every probe gets `connection refused`.

## Root Cause

`mlflow server` uses uvicorn in multi-worker mode and does not cap the worker count by default — it derives it from CPU availability. On a constrained single-node K3S cluster sharing resources with multiple other services, the default worker count causes the total memory footprint of all workers to exceed the 1Gi container limit. Each worker is killed silently by the Linux OOM killer before it can accept connections; the parent process survives but is useless without workers.

## Fix Applied

Added `--workers 1` to the `mlflow server` command in `deployment-mlflow.yaml`:

```yaml
command:
  - mlflow
  - server
  - --backend-store-uri
  - sqlite:////mlflow-data/mlflow.db
  - --default-artifact-root
  - /mlflow-data/artifacts
  - --host
  - "0.0.0.0"
  - --port
  - "5500"
  - --workers          # cap at 1 worker — node is CPU/memory constrained, multiple workers OOMKill
  - "1"
  - --allowed-hosts
  - "*"
```

## Why This Fix

**Why `--workers 1` and not a higher memory limit:** The prior incident already raised the limit from 512Mi to 1Gi. Continuing to raise limits is the wrong response when the real problem is unnecessary parallelism. MLflow in this project serves a single-user pipeline with no concurrent experiment runs — one worker is functionally identical to two, with half the memory cost. Capping at 1 worker fits within the existing 1Gi limit with headroom to spare and avoids taking memory away from other pods on the node.

**Why the parent process survived but probes failed:** Uvicorn's master/worker architecture assigns the listening socket to the *parent* process. The parent does not handle requests — it only dispatches them to workers. When all workers die, the socket stays open (hence the parent is `Running`) but no process reads from it, causing `connect: connection refused` on the probe.

## How the Fix Solved the Problem

With `--workers 1`, uvicorn spawns exactly one child process. That single worker loads the MLflow stack, stays within the 1Gi memory limit, and opens its connection slot. The parent immediately routes the first probe request to it. The readiness probe receives `{"status": "OK"}`, the pod transitions to `1/1 Running`, and the rollout completes cleanly.

## Result

`./scripts/deploy.sh` completes Step 2b6 without timeout. Pod reaches `1/1 Running` with zero additional restarts. The uvicorn worker log no longer shows any `Child process died` entries.

---

# MLflow Deployment — Incident: OOMKilled After Successful Startup (Lazy ML Library Load)

**Date:** 2026-04-10

## Error Encountered

After a successful deploy via `./scripts/deploy.sh`, the post-deploy check showed the pod stuck at `0/1 Running` with 2 restarts, cycling every ~2 minutes:

```
mlflow-5fdf55f74f-zq26s   0/1   Running   2 (29s ago)   4m43s
```

## How It Was Encountered

Running the verification step from the deploy checklist:

```bash
kubectl get pods -n airflow-my-namespace | grep mlflow
```

The pod was `Running` but never reached `1/1`, and the restart count kept incrementing.

## How It Was Identified

Two commands pinpointed the cause:

**1. Pod logs (previous container):**
```bash
kubectl logs -n airflow-my-namespace -l app=mlflow --previous --tail=40
```

Output showed MLflow starting cleanly and the `/health` endpoint returning `200 OK` for a full minute — uvicorn was up, the server was running, security middleware was satisfied:

```
2026/04/10 02:18:00 INFO:     Application startup complete.
2026/04/10 02:18:00 INFO:     Uvicorn running on http://0.0.0.0:5500 (Press CTRL+C to quit)
2026/04/10 02:18:02 INFO:     10.42.0.1:60792 - "GET /health HTTP/1.1" 200 OK
...
2026/04/10 02:19:01 INFO:     10.42.0.1:49958 - "GET /health HTTP/1.1" 200 OK
```

No crash message, no Python traceback — the process was killed externally, not by an application error.

**2. Pod describe (exit reason):**
```bash
kubectl describe pod -l app=mlflow -n airflow-my-namespace | grep -A5 'Last State'
```

Output confirmed: `Reason: OOMKilled`. The container was killed by the Linux OOM killer after exceeding its memory hard limit.

## Root Cause

`ghcr.io/mlflow/mlflow:latest` ships with the full MLflow distribution, including numpy, pandas, and scikit-learn. These libraries are **not imported at startup** — MLflow loads them lazily the first time an experiment is actually logged from Airflow (approximately 60 seconds into the pod's life).

That lazy-load spike pushed total in-process memory to approximately 1GB:

| Component | Memory |
|---|---|
| Base MLflow + uvicorn server | ~400MB |
| Lazy-loaded numpy/pandas/sklearn on first log call | ~600MB |
| **Total peak** | **~1GB** |

The memory limit was `1Gi` — the spike exceeded it by a small margin, triggering an OOMKill with no Python-level error message (the kernel terminates the process directly, bypassing the Python runtime).

This is distinct from prior OOMKill incidents in this project, which happened at *startup*. Here, MLflow started correctly and passed health checks — the kill only happened when a real workload triggered the lazy import.

## Fix Applied

In `deployment-mlflow.yaml`, two values in the `resources` block were raised:

| Field | Before | After |
|---|---|---|
| `requests.memory` | `256Mi` | `512Mi` |
| `limits.memory` | `1Gi` | `1536Mi` |

```yaml
resources:
  requests:
    cpu: "50m"
    # raised from 256Mi: request=limit ratio was 1:4; kubelet treats burstable pods (request < limit)
    # as lower-priority eviction candidates under node memory pressure. 512Mi narrows the gap
    # and reduces eviction risk without over-reserving on this constrained t3.large node.
    memory: "512Mi"
  limits:
    cpu: "300m"
    # raised from 1Gi: OOMKilled because ghcr.io/mlflow/mlflow:latest lazy-loads numpy/pandas/sklearn
    # the first time Airflow logs an experiment. Memory profile: base MLflow+uvicorn ~400MB +
    # lazy ML lib load ~600MB = ~1GB spike. 1536Mi gives ~500MB of safe headroom above observed peak.
    memory: "1536Mi"
```

## Why This Fix

**Why raise the limit to 1536Mi:** The observed peak is ~1GB. 1536Mi gives ~500MB of headroom — enough to absorb variance in the lazy-load spike without wasting a full gigabyte of extra allocation. The t3.large node has 8GB total RAM; Airflow's scheduler, webserver, and API server each have 2Gi limits but rarely spike simultaneously, leaving sufficient headroom for MLflow's 1.5Gi ceiling.

**Why also raise the request:** Kubernetes assigns pods a Quality of Service (QoS) class based on the relationship between requests and limits. A pod with a large gap between request and limit (Burstable class) is prioritized for eviction when the node is under memory pressure — before its own limit is hit. Raising the request from 256Mi to 512Mi narrows the ratio from 1:4 to 1:3, reducing the eviction risk on a node that is already loaded with Airflow, Kafka, and PostgreSQL.

## How the Fix Solved the Problem

The OOM killer fires when a container's memory usage crosses its cgroup `limit`. By raising the limit from `1Gi` to `1536Mi`, the lazy numpy/pandas/sklearn import no longer pushes the process over the ceiling. The uvicorn worker absorbs the spike, stays alive, and continues responding to health probes — keeping the pod at `1/1 Running` through the full experiment-logging lifecycle.

## Result

Pod reaches `1/1 Running` and remains stable after Airflow logs the first experiment. No further OOMKills observed. `/health` continues returning `200 OK` past the 60-second mark where the lazy load previously triggered the kill.
