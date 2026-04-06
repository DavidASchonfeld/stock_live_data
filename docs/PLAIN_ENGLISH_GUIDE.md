# Plain English Guide — How This Project Actually Works

This document explains the project in simple, non-technical language. It covers where your code lives, how it gets to the server, what pods are and how to move between them, and what all the past bugs were and how they got fixed.

---

## Part 1: Where Does Your Code Live?

Your code exists in **three places** at the same time. Think of it like making copies of a document:

### Place 1: Your Laptop (the original)

This is your Mac. When you open VS Code and edit `dag_stocks.py`, you're editing the file here:

```
Your Mac
  └── Documents/Programming/Python/Data-Pipeline-2026/data_pipeline/
        ├── airflow/dags/          ← Your pipeline code (dag_stocks.py, edgar_client.py, etc.)
        ├── airflow/manifests/     ← Kubernetes config files (YAML)
        ├── dashboard/             ← Flask website code
        └── scripts/deploy.sh     ← The script that copies everything to EC2
```

**This is the "source of truth."** If you want to change something, you change it here first, then deploy.

### Place 2: The EC2 Server (the copy on AWS)

Your EC2 instance is a computer rented from Amazon, running 24/7 in the cloud. When you run `deploy.sh`, it copies your files from your Mac to EC2:

```
EC2 (Amazon cloud server)
  └── /home/ubuntu/
        ├── airflow/dags/          ← Copy of your pipeline code
        ├── airflow/manifests/     ← Copy of your Kubernetes configs
        └── dashboard/             ← Copy of your Flask code
```

The EC2 server is like a remote desktop computer you can only access through the terminal. You connect to it via SSH:
```
ssh ec2-stock
```

**Think of SSH like making a phone call to the EC2 server.** Once connected, you can type commands that run on EC2 instead of on your Mac.

### Place 3: Inside the Pods (the copy inside the mini-computers)

This is the part that's confusing. Your EC2 server runs Kubernetes (K3S), which creates **pods**. A pod is like a tiny virtual computer running inside EC2. Your DAG files get copied one more time — from EC2's filesystem into the pods:

```
EC2 server
  └── /home/ubuntu/airflow/dags/dag_stocks.py    ← File on EC2's hard drive
        │
        │ (Kubernetes mounts this folder into the pod)
        ▼
  Airflow Pod (tiny virtual computer inside EC2)
    └── /opt/airflow/dags/dag_stocks.py             ← Same file, seen from inside the pod
```

**The file is not actually copied a third time.** Kubernetes uses a "mount" — it makes the EC2 folder visible inside the pod, like plugging in a USB drive. The pod sees the same files as EC2, just at a different path.

### The Full Journey of Your Code

```
1. You edit dag_stocks.py on your Mac
2. You run ./scripts/deploy.sh
3. deploy.sh copies the file to EC2  (rsync over SSH)
4. Kubernetes makes that file visible inside the Airflow pod  (mount)
5. Airflow reads the file and runs your pipeline
```

---

## Part 2: What Are Pods and How Do You Navigate Them?

### What is a Pod?

A pod is a tiny isolated computer running inside your EC2 server. Think of it like this:

- **EC2** = a physical office building
- **Pods** = individual offices inside the building
- Each office (pod) has its own stuff, its own "view" of files, and runs one specific program

Your project has these pods:

| Pod | What it does | Where it lives |
|-----|-------------|----------------|
| `airflow-scheduler-0` | Runs your DAG pipelines on schedule | `airflow-my-namespace` |
| `airflow-dag-processor-...` | Reads and parses your DAG files | `airflow-my-namespace` |
| `airflow-api-server-...` | Serves the Airflow web UI | `airflow-my-namespace` |
| `airflow-triggerer-0` | Handles delayed/deferred tasks | `airflow-my-namespace` |
| `airflow-postgresql-0` | Airflow's internal database (not your data) | `airflow-my-namespace` |
| `my-kuber-pod-flask` | Your Flask website/dashboard | `default` |

**MariaDB is NOT in a pod.** It runs directly on EC2 (outside Kubernetes). This is an important distinction — MariaDB is just a normal program running on the EC2 server.

### Namespaces: Which "Room" a Pod Is In

Pods are organized into **namespaces** — think of them as different floors of the office building:

- `airflow-my-namespace` = the floor where all Airflow pods live
- `default` = the floor where the Flask website pod lives

**Why this matters:** When you run a command to look at pods, you have to specify which floor you're looking on. If you look on the wrong floor, you won't see anything.

### How to Look at Pods

**From your Mac** (requires SSH tunnel running):
```bash
# See all pods on all floors
ssh ec2-stock kubectl get pods --all-namespaces

# See just the Airflow pods
ssh ec2-stock kubectl get pods -n airflow-my-namespace

# See just the Flask pod
ssh ec2-stock kubectl get pods -n default
```

**From inside EC2** (after running `ssh ec2-stock`):
```bash
# Same commands, but without the "ssh ec2-stock" prefix
kubectl get pods --all-namespaces
kubectl get pods -n airflow-my-namespace
kubectl get pods -n default
```

### How to Go "Inside" a Pod

Sometimes you need to look inside a pod — for example, to check if your DAG files are there, or to run an Airflow command. You use `kubectl exec`:

```bash
# From your Mac: run a command inside the Airflow scheduler pod
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- ls /opt/airflow/dags/
```

Let me break that command down piece by piece:

```
ssh ec2-stock                    ← "Call" the EC2 server
kubectl exec                     ← "Go inside a pod and run a command"
-n airflow-my-namespace          ← "On the Airflow floor"
airflow-scheduler-0              ← "Specifically, the scheduler pod"
--                               ← "Everything after this is the command to run inside"
ls /opt/airflow/dags/            ← "List the files in the DAGs folder"
```

**Think of it like a chain of phone calls:**
1. You call EC2 (`ssh ec2-stock`)
2. EC2 calls into the pod (`kubectl exec`)
3. The pod runs your command (`ls`)
4. The answer travels back through the chain to your Mac

### Where Am I Right Now?

This is the most confusing part. At any moment, your terminal could be running commands in one of three places:

| Where you are | How you got there | Your prompt looks like | How to leave |
|---------------|-------------------|----------------------|-------------|
| Your Mac | Default — you opened Terminal | `David@Davids-MacBook ~ %` | (you're already here) |
| EC2 server | Ran `ssh ec2-stock` | `[ubuntu@ip-... ~]$` | Type `exit` |
| Inside a pod | Ran `kubectl exec -it ... -- bash` | `airflow@airflow-scheduler-0:/$` | Type `exit` |

**The most common mistake:** Forgetting which "level" you're on. If you're inside EC2 and try to edit a local file, it won't work. If you're on your Mac and try to run `kubectl` without the SSH prefix, it won't work (unless you have an SSH tunnel running for the K8s API too).

---

## Part 3: How Files Get From Your Mac Into Pods

### The Mount System (How Pods See Files)

Remember: pods don't have their own hard drive. They "borrow" folders from EC2 using **mounts**. Here's how it works for your DAG files:

```
Step 1: You created a PersistentVolume (PV) — a Kubernetes object that says:
        "There is a folder on EC2 at /home/ubuntu/airflow/dags/"

Step 2: You created a PersistentVolumeClaim (PVC) — a Kubernetes object that says:
        "I need some storage, and I want to use the PV from Step 1"

Step 3: The Airflow pods say (in their config):
        "Mount the PVC at /opt/airflow/dags/ inside me"

Result: When the pod looks at /opt/airflow/dags/, it actually sees
        the files at /home/ubuntu/airflow/dags/ on EC2.
```

**Analogy:** A PV is like saying "there's a filing cabinet in room 204." A PVC is like saying "I need access to that filing cabinet." The mount is like putting a door from the pod directly to that filing cabinet.

### What deploy.sh Actually Does

When you run `./scripts/deploy.sh`, here's what happens in plain English:

1. **Checks your code for typos** — runs Python syntax checker on all DAG files
2. **Copies DAG files to EC2** — uses `rsync` (a smart copy tool that only sends files that changed)
3. **Renders and copies the Flask pod manifest** — `pod-flask.yaml` in git contains `${ECR_REGISTRY}` as a placeholder (so your AWS account ID is never committed). Before sending the file to EC2, the script swaps that placeholder for your real ECR URL. It uses `envsubst` to do this substitution, with a fallback to `sed` if `envsubst` isn't on the PATH (see Bug 6 below).
4. **Copies Flask website code to EC2** — same rsync
5. **Builds a new Docker image for Flask on EC2** — packages your website into a container
6. **Pushes that image to AWS ECR** — ECR is like a storage locker for Docker images; requires an IAM role (see box below)
7. **Restarts Airflow pods** — forces them to see the new DAG files (prevents the stale cache bug)
8. **Restarts the Flask pod** — picks up the new website image
9. **Verifies everything is running** — checks pod statuses

> **IAM role — what it is and why deploy.sh needs it**
>
> An **IAM role** is a permission badge that tells AWS "this EC2 instance is allowed to do X." In this project, the role gives EC2 permission to push and pull Docker images from ECR (the image storage).
>
> Step 6 above works by asking AWS for a temporary 12-hour password (`aws ecr get-login-password`). AWS only hands that out if the EC2 instance has the right IAM role attached — it's how AWS knows the request is coming from your trusted server and not a random machine.
>
> **The catch with AMIs:** When you create a new instance from an AMI (a disk snapshot of the old one), AWS copies the entire disk — all your files, Docker images, K8s state — but it does **not** copy the IAM role assignment. The role is a property of the instance, not the disk. So every time you launch a new instance (including region migrations), you must manually re-attach the IAM role in the AWS Console.
>
> If you forget, `./scripts/deploy.sh` fails at Step 4 with: `Unable to locate credentials`.
>
> **Fix:** EC2 Console → select instance → **Actions → Security → Modify IAM role** → attach the role.
> **Verify:** `ssh ec2-stock 'aws sts get-caller-identity'` — should return your AWS account ID.

---

## Part 4: The SSH Tunnel — How You Access Things in Your Browser

Your EC2 server has the Airflow web UI and your Flask dashboard running, but they're not open to the internet (for security). To access them, you use an **SSH tunnel**.

**What is an SSH tunnel?**

Think of it like a secret passageway. Normally, your browser can't reach EC2's internal ports. But an SSH tunnel creates a connection that lets your Mac pretend those ports are local:

```bash
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
```

This command says:
- "When I go to `localhost:30080` on my Mac, secretly forward that to EC2's port 30080"
- "When I go to `localhost:32147` on my Mac, secretly forward that to EC2's port 32147"

**After running this command:**
- `http://localhost:30080` in your browser → Airflow UI
- `http://localhost:32147/dashboard/` in your browser → Your Flask dashboard

**If you close this terminal window, the tunnel dies and those URLs stop working.** You need to keep this terminal open (it looks like it's just sitting there doing nothing — that's normal).

**If you also need to run `kubectl` commands from your Mac**, use the extended tunnel that adds the Kubernetes API port:

```bash
ssh -N -L 6443:localhost:6443 -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
```

- Port `6443` is the **Kubernetes API server** — it's what `kubectl` talks to behind the scenes whenever you run a kubectl command. Without this port forwarded, `kubectl` on your Mac can't reach the cluster.
- The `-N` flag means "don't open a shell, just hold the tunnel open" — useful for running it silently in the background while you work in another terminal.

---

## Part 5: What Bugs Happened and How They Got Fixed

### Bug 1: The Invisible DAG (Configuration Drift)

**What happened:** The Stock DAG would appear in the Airflow UI for about 30 seconds, then vanish. It would keep doing this in a loop — appear, disappear, appear, disappear.

**Why it happened — in plain English:**

Airflow reads your DAG file every 5 seconds to check if anything changed. Your original code had this line:

```python
start_date = pendulum.now().subtract(days=1)  # "yesterday"
```

The problem: every time Airflow reads the file (every 5 seconds), `pendulum.now()` gives a different answer because time keeps moving. Airflow says "wait, the start_date was different 5 seconds ago — something changed — this DAG is broken" and removes it. Then it reads the file again, sees a "new" DAG, adds it back... and the cycle repeats.

**The fix:** Replace the moving date with a fixed date that never changes:

```python
start_date = pendulum.datetime(2025, 3, 29, 0, 0, tz="America/New_York")  # March 29, 2025 — forever
```

Now every time Airflow reads the file, the start_date is the same. Airflow says "nothing changed, all good" and leaves the DAG alone.

### Bug 2: The 90-Second Disappearing Act (Stale Cache)

**What happened:** After fixing Bug 1, the Stock DAG would appear and stay... for exactly 90 seconds. Then it would disappear again. This time it was a different problem.

**Why it happened — in plain English:**

Your Airflow installation has two pods that both look at your DAG files:
- The **Scheduler pod** — decides when to run DAGs
- The **Processor pod** — reads and parses DAG files

Both pods see the same folder on EC2 (through the mount system). But here's the problem: each pod keeps its own mental picture of what's in that folder (this is called a "filesystem cache"). When you deployed new files:

- The **Scheduler** saw the new `dag_stocks.py` file immediately
- The **Processor** was still looking at its old mental picture from months ago, which didn't include `dag_stocks.py`

Every 90 seconds, Airflow asks: "Processor, does `dag_stocks.py` exist?" The Processor checks its stale mental picture and says "nope, never heard of it." Airflow marks the DAG as stale and hides it.

**The fix:** Restart the Processor pod. When a pod restarts, it forgets its old mental picture and looks at the folder fresh:

```bash
kubectl delete pod -l component=dag-processor -n airflow-my-namespace
```

Kubernetes automatically creates a new Processor pod, which sees the current files.

**The permanent prevention:** The deploy script (`deploy.sh`) now automatically restarts both the Scheduler and Processor pods after every deploy (Step 7). This means the stale cache bug can't happen again — every deploy forces fresh views.

### Bug 3: The Wrong Folder (PV Path Mismatch)

**What happened:** Both DAGs were invisible. Files existed on EC2 but the pods couldn't see them.

**Why it happened — in plain English:**

Remember the filing cabinet analogy? The PV said "the filing cabinet is in room 204" — but the files were actually in room 307.

Specifically:
- `deploy.sh` was copying files to `/home/ubuntu/airflow/dags/` on EC2
- But the PV config still said the folder was at `/tmp/airflow-dags/` (an old location from before you reorganized the project)

The pod mounted the old, empty folder. It looked inside and saw nothing. No DAGs, no errors — just silence.

**The fix:** Update the PV to point to the correct folder:

```yaml
# Before (wrong):
hostPath:
  path: /tmp/airflow-dags/

# After (correct):
hostPath:
  path: /home/ubuntu/airflow/dags/
```

But you can't just edit a PV — Kubernetes doesn't allow changes to an existing PV. You have to delete the old PV and PVC and create new ones pointing to the right place.

### Bug 4: The Missing Variable (DAG Not Discovered)

**What happened:** The DAG file was in the pod, Airflow could read it, but the DAG didn't show up in the UI or in `airflow dags list`.

**Why it happened — in plain English:**

Airflow's DAG finder works like this: it opens your Python file and looks for a variable that holds a DAG object. The original code was:

```python
stock_market_pipeline()  # Runs the function but throws away the result
```

This is like baking a cake and then throwing it in the trash. The function creates a DAG, but since nobody keeps a reference to it, Airflow can't find it.

**The fix:** Save the result to a variable:

```python
dag = stock_market_pipeline()  # Now Airflow can find it by looking for "dag"
```

### Bug 6: envsubst Not Found on Apple Silicon

**What happened:** `deploy.sh` failed with `command not found: envsubst` on a Mac with Apple Silicon (M1/M2/M3 chip), even though the tool was installed.

**Why it happened — in plain English:**

`pod-flask.yaml` contains `${ECR_REGISTRY}` as a placeholder instead of your real AWS account ID — this keeps secrets out of git. Before the manifest is applied to Kubernetes, the script needs to swap that placeholder for your actual ECR URL. It uses a tool called `envsubst` to do that substitution.

On Intel Macs, `envsubst` is installed in a standard location that's always on the PATH. On Apple Silicon Macs, Homebrew installs it to `/opt/homebrew/bin/` — a different location. When `deploy.sh` runs, that folder isn't always in the shell's PATH, so the script couldn't find `envsubst` even though it was sitting right there on disk.

**The fix:** Added a fallback — if `envsubst` isn't found, use `sed` instead:

```bash
if command -v envsubst &>/dev/null; then
    envsubst '${ECR_REGISTRY}' < pod-flask.yaml > /tmp/pod-flask-rendered.yaml
else
    sed "s|\${ECR_REGISTRY}|$ECR_REGISTRY|g" pod-flask.yaml > /tmp/pod-flask-rendered.yaml
fi
```

Both produce identical output. `sed` is always available on every Mac and Linux system, so the fallback is guaranteed to work.

---

### Bug 7: PostgreSQL Pod Stuck — Image Not Found on Docker Hub

**What happened:** After bootstrapping the new Ubuntu EC2 instance, `airflow-postgresql-0` stayed in `ImagePullBackOff` indefinitely. K3s was trying to pull an image and kept failing.

**Why it happened — in plain English:**

The Airflow Helm chart has a built-in default for which PostgreSQL image to use. That default was `bitnami/postgresql:16.1.0-debian-11-r15`. Bitnami (the company that packages these images) quietly deleted most of their old versioned tags from Docker Hub — they only keep `latest` there now. So when K3s tried to download that exact version, Docker Hub said "that tag doesn't exist."

We tried a second tag (`bitnami/postgresql:16-debian-12`) — also gone.

**The fix:** Override the image to pull from **Amazon ECR Public** (`public.ecr.aws/bitnami/postgresql:16`). ECR Public is Amazon's own image registry. It has all the Bitnami images, no rate limits, and no authentication needed. Since your EC2 runs on Amazon's network, ECR Public is the ideal source.

This override is now permanently set in `airflow/helm/values.yaml` under the `postgresql:` section:

```yaml
postgresql:
  image:
    registry: public.ecr.aws
    repository: bitnami/postgresql
    tag: "16"
```

**Why it works for the bigger picture:** Bitnami has been migrating their canonical image hosting away from Docker Hub for a while. For any AWS-hosted stack, defaulting to ECR Public avoids both the missing-tag problem and Docker Hub's pull rate limits (which can throttle EC2 instances on the free tier).

---

### Bug 8: Airflow Webserver CrashLoopBackOff — Startup Probe Too Short

**What happened:** `airflow-webserver-...` kept restarting in a loop. It looked like a crash. But the logs showed something strange: gunicorn (the web server process) started up normally, loaded successfully — then was killed 18 seconds later with exit code 0 (a "clean" shutdown). Exit code 0 means success, not a crash.

**Why it happened — in plain English:**

Kubernetes has a concept called a **startup probe** — a health check it runs while a pod is starting up. The startup probe says: "I'll check every 10 seconds. If the pod isn't healthy after 6 checks (60 seconds total), kill it and try again."

On a fast machine, 60 seconds is plenty. On a t3.large, it isn't:
- gunicorn takes 30–40 seconds just to start
- then Airflow loads all its provider packages across 4 workers — another 30–60 seconds

The probe killed the pod at exactly 60 seconds, before gunicorn even had a chance to become ready. Because Kubernetes sent a `SIGTERM` ("please shut down cleanly"), gunicorn exited with code 0 — which looked like success but was actually the probe murdering it.

**Diagnosed by:** Running `kubectl logs --previous` (which shows logs from the *last* run of a crashed pod). The logs showed gunicorn starting normally, then: `[SIGTERM received] — shutting down` at the 18-second mark. No errors, no panics — just an external signal.

**The fix:** Override the startup probe in `values.yaml` to give 180 seconds (18 checks × 10 seconds):

```yaml
webserver:
  startupProbe:
    failureThreshold: 18
    periodSeconds: 10
    timeoutSeconds: 20
```

**Why it works for the bigger picture:** The chart defaults were written assuming a faster machine. t3.large is just slow enough on first boot that provider loading tips past the 60-second window. 3 minutes is generous but still catches real hangs — if the webserver hasn't started in 3 minutes, something is genuinely broken.

---

### Bug 9: Triggerer OOMKilled — 256Mi Memory Limit Too Low

**What happened:** `airflow-triggerer-0` kept restarting. Each time, `kubectl get pods` showed `OOMKilled` in the STATUS column.

**What OOMKilled means — in plain English:**

OOMKilled = "Out Of Memory Killed." This is the Linux kernel (not Airflow, not Kubernetes) forcibly killing a process because it exceeded its memory limit. Kubernetes sets a hard ceiling; the kernel enforces it instantly. There's no warning — the process just disappears.

**Why it happened:**

The triggerer's memory limit was set to `256Mi` (256 megabytes). At startup, the triggerer loads all Airflow provider packages into memory at once. That loading spike temporarily pushed past 256MB — and the kernel killed it before it finished starting. Every restart, the same thing happened.

**The fix:** Increase the triggerer memory limit in `values.yaml` to `512Mi`:

```yaml
triggerer:
  resources:
    limits:
      memory: "512Mi"
```

**Why it works for the bigger picture:** 512Mi gives the triggerer enough room to absorb the provider-loading burst. Once fully loaded, the triggerer settles back to ~100MB in steady state — so the extra headroom isn't wasted, it just handles the startup spike. The kernel now never needs to intervene.

---

### Bug 10: deploy.sh Fails — "No module named airflow"

**What happened:** Running `./scripts/deploy.sh` from the Mac failed immediately at the pre-flight check step with: `ModuleNotFoundError: No module named 'airflow'`.

**Why it happened — in plain English:**

`deploy.sh` validates your DAG files before deploying them — it runs a Python import check to make sure your DAG code doesn't have any obvious errors. It uses whatever `python3` is on your system PATH.

Your system `python3` (the one installed on your Mac by default) doesn't have Airflow installed. Airflow lives in the project's virtual environment (`airflow_env/`). When the script tried to import `airflow` to validate the DAGs, the system Python said "I don't know what airflow is."

**The fix:** Activate the project venv before running deploy:

```bash
export PATH="/Users/David/Documents/Programming/Python/Data-Pipeline-2026/data_pipeline/airflow_env/bin:$PATH"
./scripts/deploy.sh
```

This puts the venv's `python3` (which has Airflow installed) first on the PATH, so the validation step finds it.

**Why this only matters on the Mac:** The deploy script's validation step runs *locally* before SSHing to EC2. The actual pipeline code runs inside Kubernetes pods on EC2, where Airflow is always available in the container environment. So this issue only bites you when running `deploy.sh` directly from your Mac with a fresh terminal that hasn't activated the venv.

---

### Bug 11: Airflow UI (Port 30080) Not Reachable — Service Selector Mismatch

**What happened:** After the Ubuntu migration, the Flask dashboard loaded fine at `http://localhost:32147` but the Airflow UI at `http://localhost:30080` dropped the connection immediately (Safari: "server unexpectedly dropped the connection"). All pods showed `Running`.

**Why it happened — in plain English:**

Kubernetes services work like a telephone switchboard. The service doesn't actually run the app — it just routes traffic *to* the pod that does. It finds the right pod using **labels**, which are key-value tags that every pod carries (like a nametag).

The Airflow NodePort service (`airflow-service-expose-ui-port`) had a selector of `component: api-server`. This is the label that Airflow 3.x uses for its UI component. But the cluster was running **Airflow 2.9.3** (Helm chart 1.15.0), which names that same pod `component: webserver`.

The selector found zero matching pods — so the service had no destination to send traffic to. Any connection attempt was instantly refused. This is called an empty **endpoints** list:

```
NAME                             ENDPOINTS   AGE
airflow-service-expose-ui-port   <none>      112m
```

`<none>` is the tell. A healthy service shows an IP:port here (e.g., `10.42.0.26:8080`).

**Why the dashboard worked but Airflow didn't:**

The Flask service (`flask-service-expose-port`) uses a different selector — one that correctly matched the Flask pod. Only the Airflow service had the wrong label. Two services, two selectors, one broken.

**How it was diagnosed:**

1. Confirmed the webserver pod was Running and responding to Kubernetes health probes (HTTP 200 on `/health`)
2. Checked `kubectl get endpoints -n airflow-my-namespace` — Airflow's NodePort service showed `<none>`
3. Checked `kubectl describe svc airflow-service-expose-ui-port | grep Selector` — revealed `component=api-server`
4. Checked the webserver pod's actual labels: `component=webserver`
5. Mismatch confirmed — one label change away from working

**The fix:** Changed the selector in `airflow/manifests/service-airflow-ui.yaml` from `api-server` to `webserver`:

```yaml
# Before (wrong — Airflow 3.x label applied to 2.x cluster):
selector:
  component: api-server

# After (correct for Airflow 2.x):
selector:
  component: webserver
```

Then re-applied the manifest (`kubectl apply -f`). Endpoints populated immediately; port 30080 returned HTTP 200.

**Why this label was wrong in the first place:** The manifest was updated in anticipation of an Airflow 3.x upgrade (which renames the webserver component to "api-server"). That future-proofing was added too early — it broke the current 2.x deployment. The comment in the file now notes to update the label only when actually upgrading to Airflow 3.x.

**The bigger lesson:** When a port is unreachable but the pod is healthy, check the service endpoints first (`kubectl get endpoints`). If they show `<none>`, the service's selector doesn't match any pod labels. Compare `kubectl describe svc <name> | grep Selector` against `kubectl get pods --show-labels` to find the mismatch.

---

### Bug 12: ERR_NETWORK on the Airflow Grid View — Module-Level raise in a DAG File

**What happened:** Opening `http://localhost:30080/dags/API_Weather-Pull_Data/grid` showed a browser console error: "network connection was lost" / `ERR_NETWORK` on the `/object/grid_data` API call. The page itself loaded, but the grid of task runs was blank.

**Why it happened — in plain English:**

Airflow re-reads (parses) every DAG file every few seconds to detect changes. Parsing means Python literally runs the top-level code in the file — the code outside any function. If any of that top-level code raises an exception, the DAG file fails to load.

`dag_weather.py` and `dag_stocks.py` both had this block at module level (outside any function):

```python
import os
_required_secrets = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME"]
_missing_secrets = [k for k in _required_secrets if not os.getenv(k)]
if _missing_secrets:
    raise RuntimeError(f"Missing Kubernetes secrets...")
```

After the EC2 region migration and Ubuntu 24.04 migration, Kubernetes secrets weren't guaranteed to be mounted before the DAG processor pod finished starting. So when the `dag-processor` pod parsed `dag_weather.py`, the environment variables weren't set yet → the `RuntimeError` fired → the DAG failed to load → the `api-server` had an incomplete DAG registry → it dropped the HTTP connection mid-response → the browser saw `ERR_NETWORK` instead of a proper error message.

**Why did it drop the connection instead of showing an error page?**

In Airflow 3.x the `api-server` uses FastAPI instead of Flask. When the DAG registry is broken, FastAPI abandons the response rather than sending a clean error page. The browser sees the TCP connection close before any HTTP bytes arrive, which shows as a network error — not a 404 or 500.

**The fix:** Moved the secret validation inside the `load()` task function, where it only runs when the task actually executes — not during parse time:

```python
@task()
def load(inData):
    # Validate DB secrets at task-execution time (not parse time)
    import os
    _missing = [k for k in ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME"] if not os.getenv(k)]
    if _missing:
        raise RuntimeError(f"Missing Kubernetes secrets: {_missing}. Ensure db-credentials secret is mounted.")
    # ... rest of task
```

Also added `dag = zero_nameThatAirflowUIsees()` at the end of `dag_weather.py` to follow Airflow's documented best practice of assigning the DAG to a module-level variable.

**The bigger lesson:** In Airflow, the DAG file is a Python module that gets imported repeatedly. Think of the top level of your DAG file like `__init__.py` — it should only define structure. Never put I/O, network calls, secret checks, or anything that can raise an exception at module level. Anything that might fail belongs inside a `@task` function, which only runs when Airflow actually schedules that task.

---

### Bug 13: All Static Assets Fail with "Network Connection Was Lost" — Webserver OOMKilled

**What happened:** The Airflow UI at `http://localhost:30080/home` showed a blank, unstyled page. The browser console had 20+ identical errors: `"network connection was lost"` for every static file — `main.js`, `bootstrap.min.js`, `ab.css`, and all the rest.

**Why it happened — in plain English:**

When a _single_ API call fails (like Bug 12's grid view), you suspect a DAG parse problem. But when _every_ file fails _simultaneously_, that's a different diagnosis: the web server process was killed mid-page-load.

Here's what was happening on the server:

Airflow's web server uses **gunicorn** — a process that spawns multiple worker processes to handle requests. We had 4 workers configured. Each worker has to load all the Airflow provider packages (Azure, Snowflake, etc.) into memory when it starts. Each loaded worker uses ~300 MB of RAM.

4 workers × 300 MB = **~1.2 GB just for the web server**, plus Kubernetes overhead. The memory limit in `values.yaml` was set to `1Gi`. So the pod kept exceeding its limit, Kubernetes force-killed it (called an **OOMKill** — Out of Memory Kill), and the pod restarted.

The browser opened the page, started downloading CSS and JS files, then the pod got killed mid-download. Every file transfer was cut off. The browser reported this as "network connection was lost" — technically accurate, but not very helpful.

**Why did every file fail, not just some?**

When the pod is killed, ALL open HTTP connections are dropped at once. The page HTML might have already been delivered (which is why you saw a page at all), but all the CSS/JS/font requests that followed were cut off together.

**The fix (two parts):**

1. Increase the memory limit in `values.yaml` from `1Gi` to `2Gi` — gives headroom above the 1.2 GB baseline.
2. Reduce gunicorn workers from 4 to 2 (via `AIRFLOW__WEBSERVER__WORKERS: "2"`) — cuts memory footprint roughly in half.
3. Add a `helm upgrade` step to `deploy.sh` — `values.yaml` changes are just a text file until Helm applies them to the live cluster.

**The lesson about `helm upgrade` vs. file sync:**

`deploy.sh` used `rsync` to copy `values.yaml` to EC2, but never ran `helm upgrade`. The file was updated on disk but the running Kubernetes pods were still configured with the old settings. Think of it like editing a config file for a service but forgetting to restart the service — the changes don't take effect until you apply them.

Added **Step 2d** to `deploy.sh`:
```bash
ssh "$EC2_HOST" "helm upgrade airflow apache-airflow/airflow \
    -n airflow-my-namespace \
    --version 1.15.0 \
    --reuse-values \
    -f $EC2_HELM_PATH/values.yaml"
```

Now every `deploy.sh` run automatically applies any `values.yaml` changes to the live cluster.

---

### Bug 14: The Accidental Upgrade — Running `helm upgrade` Without a Version Pin

**What happened:** A `helm upgrade` command was run without specifying which version of the chart to install. Helm pulled the latest version — which happened to be a major version jump from Airflow 2.9.3 to Airflow 3.1.8. The database got upgraded to the new format before the upgrade timed out, leaving the cluster broken: the database expected Airflow 3.x but the pods still running were Airflow 2.x. Rolling back was impossible — Airflow cannot undo a database schema upgrade. The cluster was stuck pointing forward into Airflow 3.x.

**Why it happened — in plain English:**

The `helm upgrade` command installs a Helm chart (a package of Kubernetes configs). When you don't specify a version, it means "give me the latest." Like doing `pip install airflow` without a version number — you get whatever is newest, even if it's a breaking change.

```bash
# What was run (no version pin — dangerous):
helm upgrade airflow apache-airflow/airflow -f values.yaml

# What should always be run (version pinned):
helm upgrade airflow apache-airflow/airflow --version 1.20.0 -f values.yaml
```

When Helm runs an upgrade, it runs a **migration job** first — a one-time task that updates the database schema to match the new software version. That migration job ran successfully (it upgraded the database from Airflow 2.x format to Airflow 3.x format). But then the rest of the upgrade timed out because Airflow 3.x has a different architecture and the old config (`values.yaml`) wasn't set up for it yet.

After the timeout, the cluster was in a half-upgraded state:
- Database: Airflow 3.x format ✓
- Running pods: Airflow 2.9.3 ✗ (wrong version for the database)
- Trying to rollback to 2.9.3: impossible (2.9.3 refuses to talk to a 3.x database)

**The decision:** move forward to Airflow 3.x. The database was already there, 3.x is the current version, and our DAG code (using the TaskFlow API) is compatible.

**The lesson:** Always use `--version` when running `helm upgrade` in production. The version you're upgrading FROM and TO should be a deliberate choice, not whatever happened to be released that day.

---

### Bug 15: Every Pod Crashed Because of a Missing Secret — The Upgrade Kept Failing

**What happened:** After accepting the Airflow 3.x upgrade, we ran `helm upgrade --version 1.20.0` repeatedly — four times over several hours. Every attempt timed out. Every pod showed either `CreateContainerConfigError` or `Init:CrashLoopBackOff`. Nothing could start.

**Why it happened — in plain English:**

When Kubernetes starts a pod, it assembles all the environment variables that pod needs. Some of those variables come from **Secrets** — which are like locked boxes containing sensitive values (passwords, keys, etc.).

One environment variable, `AIRFLOW__WEBSERVER__SECRET_KEY`, was configured to come from a Secret named `airflow-webserver-secret-key`. This was a 2.x thing — Airflow 2.x used this secret for the web UI's session encryption.

In Airflow 3.x, this concept was replaced with `airflow-api-secret-key`. The Airflow 3.x chart creates `airflow-api-secret-key` but deliberately **does not create** `airflow-webserver-secret-key` anymore — it's 2.x-only. But the Helm chart's default settings still had `AIRFLOW__WEBSERVER__SECRET_KEY` turned on, pointing to the no-longer-created secret.

Result: every pod tried to mount a secret that didn't exist → `CreateContainerConfigError` → pod couldn't start.

This included the **migration job** — the one-time task that actually migrates the database. Because the migration job also couldn't start, the database was never migrated. And because the database wasn't migrated, every other pod's init container (which waits for migration to complete before allowing the main pod to start) waited forever, then crashed, then waited again.

**The chain that caused everything:**
```
Chart default leaves AIRFLOW__WEBSERVER__SECRET_KEY=true
    → every pod spec references 'airflow-webserver-secret-key' secret
        → secret doesn't exist in Airflow 3.x
            → every pod gets CreateContainerConfigError (can't start)
                → migration job can't start
                    → database never migrated
                        → init containers wait forever
                            → all pods crash after 60s
                                → helm upgrade times out
```

**The fix:** Add one setting to `values.yaml` that tells the chart "don't inject this 2.x env variable":
```yaml
enableBuiltInSecretEnvVars:
  AIRFLOW__WEBSERVER__SECRET_KEY: false
```

With this in place, no pod references the missing secret. The migration job can now start, upgrades the database, and all other pods can begin initializing normally.

**The lesson:** When upgrading between major versions of a Helm chart, read the migration guide. Airflow 2.x → 3.x is a major change — the webserver was split into separate components, secrets were renamed, and some defaults changed. What worked in 2.x won't necessarily work in 3.x.

---

### Bug 16: Scheduler Kept Dying — Memory and Probe Limits From the 2.x Era

**What happened:** After fixing Bug 15, the scheduler started up... then crashed after ~3 minutes... then started again... then crashed again. This happened repeatedly. Looking at the events, two different things were killing it:

1. The first few times: `OOMKilled` (Out of Memory Killed — Linux force-killed the process for using too much RAM)
2. Later times: startup and liveness probes failing with "timed out after 20s"

**Why the OOMKill happened — in plain English:**

Airflow 3.x changed how the scheduler works internally. In Airflow 2.x, the scheduler was a single process. In Airflow 3.x, it uses a "supervisor" model — it spawns about 15 worker processes simultaneously. Each worker process loads all of Airflow's provider packages (the add-ons for AWS, Snowflake, etc.) into memory when it starts.

With ~15 workers each using ~80-100 MB, the scheduler was briefly using well over 1 GB of RAM during startup. The old memory limit from Airflow 2.x was `1Gi` — sized for the 2.x single-process model. With 3.x's 15-worker model, it was almost guaranteed to hit that limit.

**Fix:** Raise the scheduler memory limit to `2Gi` in `values.yaml`.

**Why the probe timeout happened — in plain English:**

Kubernetes regularly runs health checks on running pods. The check it uses for the scheduler is:
```
airflow jobs check --job-type SchedulerJob
```

This command has to load the full Airflow codebase before it can check anything. In Airflow 2.x, this was quick (~5 seconds). In Airflow 3.x, loading all the provider packages takes 30-45 seconds on a t3.large.

The timeout setting was `20 seconds` — meaning: "if the check doesn't finish in 20 seconds, mark it as failed." In 3.x, the check always takes more than 20 seconds, so it always "failed," even though the scheduler itself was working perfectly.

With 5 consecutive failures, Kubernetes kills the pod and restarts it. Then the same thing happens again. The scheduler was being killed by a health check that was misconfigured for 3.x's slower startup.

**Fix:** Increase the timeout from 20 seconds to 45 seconds in `values.yaml`:
```yaml
scheduler:
  startupProbe:
    timeoutSeconds: 45   # was 20
  livenessProbe:
    timeoutSeconds: 45   # was 20
```

**The lesson:** When upgrading to a new major version, the old sizing values (memory limits, probe timeouts) were calibrated for the old architecture. Airflow 3.x is a more heavyweight process model than 2.x — it does more work in parallel, uses more memory, and takes longer to start. Old limits need to be recalibrated.

---

### Bug 5: Alpha Vantage Rate Limits (API Errors)

**What happened:** The Stock DAG would sometimes fail because the Alpha Vantage API stopped responding with data and instead gave an error message.

**Why it happened — in plain English:**

Alpha Vantage's free plan only allows 25 API calls per day. If you ran the DAG multiple times (manually + scheduled), you'd use up all 25 calls. After that, the API still responded with "200 OK" (which looks like success) but the response body said "you've been rate limited" instead of containing actual stock data.

**The fix (migration to SEC EDGAR):** We replaced Alpha Vantage entirely with SEC EDGAR, which is:
- Free with no daily limit (10 requests per second max, but we only make a handful)
- U.S. government public data — no API key needed, no restrictions on displaying data
- The `RateLimiter` class in `edgar_client.py` automatically slows down requests to stay under the 10/sec limit, even though our pipeline never comes close to hitting it

---

## Part 6: Quick Reference — Common Tasks

### "I want to deploy my code changes"
```bash
# From your Mac, in the project directory:
./scripts/deploy.sh
```

### "I want to see if my DAGs are running"
```bash
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list
```

### "I want to see the Airflow UI"
```bash
# First, open the SSH tunnel (keep this terminal open):
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock

# Then in your browser:
# Airflow UI:   http://localhost:30080
# Dashboard:    http://localhost:32147/dashboard/
```

### "I want to check if pods are healthy"
```bash
ssh ec2-stock kubectl get pods --all-namespaces
```
Every pod should show `Running` and `1/1` READY. If any show `CrashLoopBackOff`, `Error`, or `ImagePullBackOff`, something is wrong.

### "I want to manually trigger my Stock pipeline"
```bash
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger Stock_Market_Pipeline
```

### "I want to check if data is getting into the database"
```bash
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- python3 -c "
from sqlalchemy import create_engine, text
import os
engine = create_engine(f'mysql+pymysql://{os.environ[\"DB_USER\"]}:{os.environ[\"DB_PASSWORD\"]}@{os.environ[\"DB_HOST\"]}/{os.environ[\"DB_NAME\"]}')
with engine.connect() as c:
    r = c.execute(text('SELECT COUNT(*) FROM company_financials')).scalar()
    print(f'company_financials: {r} rows')
    r = c.execute(text('SELECT COUNT(*) FROM weather_hourly')).scalar()
    print(f'weather_hourly: {r} rows')
"
```

### "SSH won't connect from a new location"
Your EC2 only allows SSH from one IP address (for security). When you're at a new location (different Wi-Fi), your IP changes. Go to AWS Console -> EC2 -> Security Groups -> update the SSH rule with your new IP.

### "WARNING: connection is not using a post-quantum key exchange algorithm"

This warning appeared after upgrading to macOS with OpenSSH 10.2+. It meant the EC2 server (then running Amazon Linux 2023, OpenSSH 8.7p1) was too old to support post-quantum key exchange algorithms. The workaround at the time was to add `KexAlgorithms -mlkem768x25519-sha256` to `~/.ssh/config` to suppress the warning.

**This is now resolved.** The EC2 was migrated to Ubuntu 24.04 LTS, which ships OpenSSH 9.6p1. The new server negotiates `sntrup761x25519-sha512` (a post-quantum hybrid algorithm) automatically — no warning appears, and no workaround is needed.

**Pending cleanup (after Phase H EIP cutover):** Remove the `KexAlgorithms -mlkem768x25519-sha256` line from `~/.ssh/config` under the `ec2-stock` host entry — it is only there for the old AL2023 instance and is no longer needed once `ec2-stock` points to the Ubuntu instance.

**Is this a real risk?** The "store now, decrypt later" attack means an adversary records your encrypted traffic today and decrypts it later when quantum computers exist. For this pipeline — SSH tunnels to view the Airflow UI and dashboard — the risk is negligible. No sensitive credentials pass through the tunnel; it only carries UI traffic. But now you're using a proper post-quantum algorithm anyway.

---

## Part 7: Alerting — Getting Notified When Things Break

### What is alerting?

When a pipeline task fails or your data goes stale, the system now sends you a notification. Without alerting, you'd only find out something was broken when you manually opened the Airflow UI or checked the dashboard — which could be hours or days later.

### How it works

When a task fails, your pipeline automatically calls a function (`on_failure_alert`) that:
1. Writes the failure to the PVC log file (always)
2. Sends a message to Slack (if you set up a webhook URL)

A separate monitoring DAG (`Data_Staleness_Monitor`) runs every 30 minutes. It checks how old the latest data is in each table and alerts you if data hasn't been updated in too long.

### Preventing notification spam (cooldown)

Your DAGs run every 5 minutes. Without any protection, a single broken task could send you 12+ Slack messages per hour — one for every failed run. That's alert fatigue: so many notifications that you start ignoring them.

**The cooldown system works like a "don't call me again for an hour" rule:**

- The first time a task fails → you get a Slack message immediately
- If that same task fails again within 60 minutes → the message is suppressed (logged but not sent to Slack)
- When the task finally succeeds again → you get one `:green_circle: Task Recovered` message, then the clock resets

**The same rule applies to retries.** When Airflow retries a failing task, it would normally send both a "failure" and a "retry" message. Now it only sends the first one — the retry message is suppressed once you've already been told about the failure.

**And for the staleness check** — if your weather data stays stale for 2 hours, you get one alert. Not one every 30 minutes for as long as the problem lasts. When the data recovers, you get one `:green_circle: Staleness Resolved` message.

**How it stores state:** The cooldown timer is saved as an Airflow Variable (the same system that stores vacation mode). You can see these variables in the Airflow UI under Admin → Variables — they look like `alert_last_sent:Stock_Market_Pipeline:extract`. If you want to be notified again immediately (e.g., after fixing a problem and wanting to confirm the fix works), just delete that variable from the UI.

### What is Slack?

**Slack is a messaging app** — like iMessage or WhatsApp but designed for teams. You install it on your Mac or phone, create a free account (at slack.com), and it gives you channels (chat rooms). When your pipeline fails, a message pops up in your Slack channel like any other notification.

**You do not need to provide your email address to receive alerts.** Slack is its own system with its own login. Alerts are not sent via email.

**Slack is not a terminal tool.** You receive messages in the Slack app on your phone or Mac desktop.

### What is a Slack webhook?

A **webhook** is a secret URL that Slack gives you. When your pipeline sends an HTTP POST request to that URL with a message, Slack delivers that message to a channel. That's the entire mechanism:

```
Pipeline task fails
    ↓
Python: POST "Task Failed!" to https://hooks.slack.com/services/T.../...
    ↓
Slack receives it and shows it in your #alerts channel
    ↓
Notification appears on your phone/Mac like any Slack message
```

No email involved. No terminal. Just a pop-up notification in the Slack app.

### Do you need to set up Slack?

No — it's optional. Without any configuration, the alerting system runs in **log-only mode**:
- Failures are still logged to the PVC files on EC2
- You just won't get a phone/desktop notification

To get actual push notifications, you need to set up Slack (free account + webhook URL) and add the URL to your `.env` file locally and to the Kubernetes Secret in production. See [RUNBOOKS.md Runbook #12](operations/RUNBOOKS.md#12-configure-slack-alerting) for the step-by-step setup.

> **Current status (as of 2026-03-31):** A Slack webhook URL was generated and the alerting infrastructure is fully built, but it has **not been connected to a Slack account or workspace**. The system is currently running in **log-only mode** — all alerts write to PVC log files only. No Slack notifications are actively being received.

### Vacation mode and alerts

If you have vacation mode enabled:
- **Failure/retry alerts still fire** — if a DAG somehow fails during vacation instead of cleanly skipping, that means vacation mode is broken, which is worth knowing
- **Staleness alerts are silenced** — the staleness monitor sees `VACATION_MODE=true` and skips its check entirely; stale data is expected when you've intentionally paused the pipelines

### New files added for alerting

| File | Plain English |
|------|--------------|
| `airflow/dags/alerting.py` | The module that sends alerts — handles Slack, PVC logging, and staleness checking |
| `airflow/dags/alert_config.py` | Your private config: webhook URL and staleness thresholds (gitignored, never committed) |
| `airflow/dags/dag_staleness_check.py` | A new DAG that runs every 30 minutes and checks if your data is fresh |

---

## Part 8: The Big Picture

Here's what your project does, start to finish, in one paragraph:

**Every 5 minutes**, Airflow (running inside a pod on your EC2 server) wakes up and runs your Stock pipeline. The pipeline calls SEC EDGAR (a free U.S. government API) and asks for financial data about Apple, Microsoft, and Google — things like revenue, net income, and total assets from their annual SEC filings. It takes the messy, deeply nested response and flattens it into clean rows. Then it writes those rows into a MariaDB database (running directly on EC2, not in a pod). Meanwhile, the Weather pipeline does the same thing with weather data from Open-Meteo. Your Flask website (running in its own pod) reads from MariaDB and shows the data on a dashboard that you can view in your browser through an SSH tunnel. **Every 30 minutes**, a separate monitoring DAG checks how fresh the data is — if it's too old, it sends a Slack notification (or logs a warning if Slack isn't configured). If any pipeline task fails or retries, you're notified — once per hour per broken task, not on every single failed run — and when things recover, you get a green ":white_check_mark: Recovered" message.

---

## Part 9: What Size EC2 Do You Need?

### RAM and vCPU in plain English

Your EC2 instance is like a computer. Every program you run on it uses some memory (RAM). When RAM fills up, programs crash or slow to a crawl. vCPU is like the number of hands your computer has — more hands means it can do more things at the same time without waiting.

Your stack runs multiple programs simultaneously: Airflow (several pods), the Flask dashboard, MariaDB (for now), and eventually Kafka. All of these share the same RAM and vCPU.

### The size options

| Size | RAM | vCPU | Plain English |
|------|-----|------|--------------|
| t3.small | 2GB | 2 | Too small — K3s and Airflow alone can use most of this |
| t3.medium | 4GB | 2 | Not enough — barely fits today's stack, no room for Kafka |
| **t3.large** | **8GB** | **2** | **Works — the right size for this project** |
| t3.xlarge | 16GB | 4 | Comfortable but costs ~$60/month more than needed |

### Why t3.large works (and medium doesn't)

Right now, the stack uses roughly 2.5–4GB of RAM. t3.medium has 4GB total — that's basically nothing left over for Kafka or any unexpected spikes. t3.large has 8GB, which gives you real breathing room.

**The key reason t3.large stays comfortable long-term:** your roadmap replaces MariaDB with Snowflake. Snowflake is a cloud database — it runs on Snowflake's servers, not yours. Once that migration is done, MariaDB is uninstalled from EC2, freeing ~300–500MB of RAM. That's the single biggest thing you can do to help t3.large succeed.

### Kafka needs a special setting on t3.large

Kafka is written in Java. Java programs are famous for asking for way more memory than they actually need — kind of like someone who always grabs a huge desk even for a small task. By default, Kafka might claim 1–2GB of RAM just for itself.

On t3.large, you tell Kafka to use a smaller desk:

```
KAFKA_HEAP_OPTS="-Xmx768m -Xms768m"
```

This limits Kafka to 768MB, which is plenty for a low-volume pipeline. With this setting, Kafka fits comfortably alongside Airflow and the dashboard.

You also use **KRaft mode** for Kafka, which means Kafka runs without needing a helper program called Zookeeper. Skipping Zookeeper saves another ~500MB.

### Cost savings

- t3.xlarge: ~$121/month
- t3.large: ~$61/month
- **Savings: ~$60/month (~$720/year)**

If t3.large ever feels slow or pods start crashing with out-of-memory errors, you can resize to t3.xlarge in the AWS Console in about 2 minutes with no data loss.

### Resource limits — what they are and why every pod needs them

**The problem without limits:**

Imagine five people sharing a 8GB RAM computer, and none of them have any rule about how much RAM they're allowed to use. One person's program develops a memory leak (a bug where it slowly grabs more and more RAM without ever releasing it). Eventually it takes all 8GB. The other four programs crash.

That's exactly what happens in Kubernetes without resource limits. One runaway pod — say, the Airflow webserver spiking during startup — can silently eat all available RAM, causing other pods to get killed. And when a pod gets killed by the system for using too much memory, it gets an `OOMKilled` status (Out Of Memory Killed). You'd see it in `kubectl get pods`.

**The solution: requests and limits**

Each pod now has two numbers set for both memory and CPU:

- **Request** — "I need at least this much." Kubernetes uses this to decide which computer to put the pod on, and guarantees the pod will always have at least this much available.
- **Limit** — "This is the absolute most I'm allowed to use." If a pod tries to exceed this, Kubernetes kills it (for memory) or throttles it (for CPU). This protects every other pod from being starved.

Think of it like assigned seats on a plane:
- The **request** is your reserved seat — it's yours, guaranteed.
- The **limit** is the armrest rule — you can't take more than your share, even if the seat next to you is empty.

**Why were those specific numbers chosen?**

The amounts are based on observed RAM usage at low/portfolio traffic levels (documented in `EC2_SIZING.md`), with roughly **2× headroom** above the baseline to absorb startup spikes — Airflow's webserver, for example, can briefly spike to 800 MB when it first starts:

| Pod | Observed baseline | Memory limit set | Why that limit |
|-----|------------------|-----------------|----------------|
| Flask/Dash | ~200 MB | 512 Mi | 2.5× baseline — lightweight app, gives spike room |
| Airflow webserver | ~500–800 MB | 1 Gi | Covers cold-start spike; stays under 1 Gi in steady state |
| Airflow scheduler | ~300–500 MB | 1 Gi | Heart of Airflow — generous limit to prevent slow scheduling |
| Airflow triggerer | ~100–200 MB | 256 Mi | Very lightweight; limit is still 2× baseline |
| Airflow dag-processor | ~200–300 MB | 512 Mi | If it exceeds 512 Mi, it's a bug in a DAG file, not a sizing issue |

**What happens if a limit is hit?**

- **Memory limit hit** → pod is immediately killed and restarted (OOMKilled). You'd see `RESTARTS` count go up in `kubectl get pods`.
- **CPU limit hit** → pod is throttled (slowed down), not killed. Things just run slower.

In both cases, the other pods keep running normally — which is the whole point.

**How to check that limits are actually in place:**

```bash
# Check Flask pod limits
ssh ec2-stock kubectl describe pod my-kuber-pod-flask -n default | grep -A6 "Limits:"

# Check Airflow scheduler limits
ssh ec2-stock kubectl describe pod -n airflow-my-namespace -l component=scheduler | grep -A6 "Limits:"
```

You should see `memory: 512Mi` for Flask and `memory: 1Gi` for the scheduler.

**Where the limits are defined in your code:**

- Flask pod: `dashboard/manifests/pod-flask.yaml` (look for the `resources:` section)
- All Airflow components: `airflow/helm/values.yaml` (look for `webserver:`, `scheduler:`, `triggerer:`, `dagProcessor:` sections)

Each limit has a comment in the file explaining why that specific amount was chosen.

### What to watch for after switching

After you switch to t3.large, run this command and look at the "available" column — it should show at least 3–4GB free at rest:

```bash
ssh ec2-stock free -h
```

If pods start showing high RESTARTS counts or errors, check `kubectl get pods --all-namespaces`. A status of `OOMKilled` means a pod ran out of memory and crashed — that's the sign you need to resize up or tune memory settings.

See [infrastructure/EC2_SIZING.md](infrastructure/EC2_SIZING.md) for the full technical breakdown, and [BACKLOG.md](BACKLOG.md) for the step-by-step checklist.

---

## Part 10: What Is dbt, and Why Does It Matter?

### The problem dbt solves

Right now your pipeline does three things inside the `transform()` task in each DAG:

1. Pulls raw data from the API
2. Reshapes it (flattens JSON, renames columns, filters rows) using Python + Pandas
3. Writes the result directly to the database

This works fine for two tables. But imagine you have 20 tables. Now every transformation is buried in Python functions spread across multiple DAG files. If a business rule changes ("only use 10-K filings, not 10-Q"), you have to find which Python function implements that, edit it, redeploy the pod, and hope nothing broke.

dbt (data build tool) solves this by moving all transformation logic into **SQL files** that live in version control — one file per table, with a clear name, documented purpose, and automated tests.

### How dbt fits into your pipeline

Your pipeline becomes a two-stage process:

```
Stage 1 (Airflow DAG): Extract → Load raw data into Snowflake
  extract() → loads raw API data into PIPELINE_DB.RAW.COMPANY_FINANCIALS
                                  and PIPELINE_DB.RAW.WEATHER_HOURLY

Stage 2 (dbt): Transform raw → clean analytics tables
  dbt run → creates PIPELINE_DB.ANALYTICS.fct_company_financials
                 and PIPELINE_DB.ANALYTICS.dim_company
                 and PIPELINE_DB.ANALYTICS.fct_weather_hourly
```

The Airflow DAG handles "get the data in." dbt handles "make the data useful."

### What dbt models look like

Each dbt "model" is just a `.sql` file. For example, `models/marts/fct_company_financials.sql`:

```sql
-- Annual revenue for each company — cleaned and filtered
SELECT
    ticker,
    entity_name,
    period_end,
    fiscal_year,
    value AS revenue_usd,
    filed_date
FROM {{ ref('stg_company_financials') }}
WHERE metric = 'Revenues'
  AND fiscal_period = 'FY'
ORDER BY ticker, period_end
```

That `{{ ref('stg_company_financials') }}` is dbt's magic — it knows to run the staging model first, then this one. It builds a **lineage graph** automatically.

### dbt tests — built-in data quality

dbt has built-in tests you define in a YAML file alongside the SQL:

```yaml
# models/marts/fct_company_financials.yml
models:
  - name: fct_company_financials
    columns:
      - name: ticker
        tests:
          - not_null
          - accepted_values:
              values: ['AAPL', 'MSFT', 'GOOGL']
      - name: revenue_usd
        tests:
          - not_null
```

Run `dbt test` and it checks every rule. If revenue ever comes back null or a new ticker appears unexpectedly, the test fails and you know immediately. This is **data quality** — one of the most valued skills in data engineering.

### How dbt integrates with Airflow (using Cosmos)

`astronomer-cosmos` is an Airflow provider that turns dbt models into Airflow tasks automatically. Your DAG goes from:

```
extract → transform (Python) → load
```

to:

```
extract → load_raw → [dbt] stg_company_financials → fct_company_financials
```

Each dbt model becomes its own Airflow task, visible in the UI with its own logs. If `fct_company_financials` fails, you see exactly which SQL model broke, without digging through Python.

### Why recruiters care about dbt

dbt is the most common tool in modern data engineering stacks. A 2024 survey found it in over 60% of data teams. When a recruiter sees "Airflow + Snowflake + dbt" in your resume, they recognize that as the real-world production stack — not just a learning project. Adding dbt to your pipeline demonstrates you know how professional data teams work.

---

## Part 11: The Full Step 2 Roadmap — What Comes Next and Why

Here's the sequence after Step 1 (which you've now completed):

### Step 2a: Snowflake (in progress)

**What:** Replace MariaDB with Snowflake as your database layer.

**Why:** Snowflake is the dominant cloud data warehouse in the industry. It separates storage from compute, scales automatically, and handles SQL analytics far better than a single-machine database like MariaDB. It also frees ~300–500 MB of RAM on your EC2 instance because MariaDB is uninstalled after the migration.

**Status:** Scaffolding complete (dual-write code, dashboard switch, Runbook #14). You need to sign up for Snowflake (free trial, $400 credits) and run the activation steps.

**Recruiter signal:** "Knows Snowflake" appears in nearly every data engineering job posting.

### Step 2b: dbt (after Snowflake)

**What:** Move transformation logic out of Pandas/Python and into SQL models with dbt.

**Why:** Cleaner code, automatic lineage, built-in data quality tests, and the industry-standard pattern for transformations on top of Snowflake.

**Recruiter signal:** dbt is on nearly as many job postings as Snowflake itself. Together they're the modern data stack.

### Step 2c: Kafka (after dbt)

**What:** Add a streaming layer between your DAGs and Snowflake. Instead of Airflow writing directly to Snowflake on a schedule, each API response becomes a Kafka event. A Kafka consumer (or the Kafka Connect Snowflake Sink connector) writes the events to Snowflake in real time.

**Why:** Demonstrates you can build both batch pipelines (what you have now) and streaming pipelines — two distinct, in-demand skill sets. Also makes the pipeline more resilient: if Snowflake is temporarily unavailable, Kafka buffers the events rather than losing them.

**Memory note for t3.large:** Use KRaft mode (no Zookeeper) and set `KAFKA_HEAP_OPTS="-Xmx768m -Xms768m"` to cap Kafka's Java heap. This limits Kafka to 768 MB, well within the ~2–3 GB of free RAM after Snowflake replaces MariaDB.

**Recruiter signal:** Kafka appears on senior engineer roles, but seeing it on a junior portfolio project stands out strongly. It shows range beyond basic batch ETL.

### Portfolio extras that make recruiters notice

These are not required for the pipeline to work, but they significantly increase the project's impact on a resume:

| Extra | Why It Matters |
|---|---|
| **Architecture diagram** (Mermaid or draw.io PNG in README) | Visual diagrams show systems thinking; recruiters scan READMEs quickly |
| **GitHub Actions CI/CD** (run `dbt test` on every PR) | Shows you know DevOps basics — a differentiator for junior candidates |
| **Public dashboard URL** (open port 32147 in Security Group) | Lets recruiters click a link and see a live pipeline in action |
| **Cost callout in README** ("$60/month on t3.large vs $121 on t3.xlarge") | Business awareness is rare in junior candidates — this stands out |
| **dbt docs site** (`dbt docs generate && dbt docs serve`) | Auto-generated HTML showing all models, lineage graph, test results |
| **Slack alerting** (connect the webhook to a real workspace) | Operational maturity — shows you think about what happens when things break |
| **Data quality section in dashboard** (counts, freshness timestamp) | Product thinking — shows you care about data consumers, not just pipelines |

The things you already have that are already impressive:
- Real data sources (SEC EDGAR, Open-Meteo) — not toy datasets
- Production deployment on AWS (not just localhost)
- Kubernetes orchestration
- Vacation mode kill switch
- PVC-backed task logs that survive pod restarts
- Ubuntu + post-quantum SSH (shows you care about security and future-proofing)

---

**Last updated:** 2026-04-05 — Added resource limits explanation (Part 9); added Bug 6 (envsubst Apple Silicon fallback); updated deploy.sh step 3 to explain ECR_REGISTRY placeholder substitution. Added Bugs 7–10 (EC2 Ubuntu migration: Bitnami image removal from Docker Hub, webserver startup probe timeout, triggerer OOMKill, deploy.sh venv PATH). Updated SSH KEX warning section: migration to Ubuntu 24.04 LTS resolved the issue permanently. Added Bug 11 (Airflow UI port 30080 unreachable — service selector mismatch: `api-server` vs `webserver`). Added Bugs 12–13 (module-level raise in DAG causing ERR_NETWORK on grid view; webserver OOMKill causing all static assets to fail with "network connection was lost" — fixed by raising memory limit to 2 Gi, reducing workers to 2 via `webserver.env`, and adding `helm upgrade` Step 2d to deploy.sh). Added Parts 10–11 (dbt explanation and full Step 2 roadmap for portfolio).
