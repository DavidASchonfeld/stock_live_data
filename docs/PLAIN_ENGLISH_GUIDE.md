# Plain English Guide — How This Project Actually Works

This document explains the project in simple, non-technical language. It covers where your code lives, how it gets to the server, what pods are and how to move between them, and what all the past bugs were and how they got fixed.

---

## Part 1: Where Does Your Code Live?

Your code exists in **three places** at the same time. Think of it like making copies of a document:

### Place 1: Your Laptop (the original)

This is your Mac. When you open VS Code and edit `dag_stocks.py`, you're editing the file here:

```
Your Mac
  └── Documents/Programming/Python/StockLiveData/stock_live_data/
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
  └── /home/ec2-user/
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
  └── /home/ec2-user/airflow/dags/dag_stocks.py    ← File on EC2's hard drive
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
| EC2 server | Ran `ssh ec2-stock` | `[ec2-user@ip-... ~]$` | Type `exit` |
| Inside a pod | Ran `kubectl exec -it ... -- bash` | `airflow@airflow-scheduler-0:/$` | Type `exit` |

**The most common mistake:** Forgetting which "level" you're on. If you're inside EC2 and try to edit a local file, it won't work. If you're on your Mac and try to run `kubectl` without the SSH prefix, it won't work (unless you have an SSH tunnel running for the K8s API too).

---

## Part 3: How Files Get From Your Mac Into Pods

### The Mount System (How Pods See Files)

Remember: pods don't have their own hard drive. They "borrow" folders from EC2 using **mounts**. Here's how it works for your DAG files:

```
Step 1: You created a PersistentVolume (PV) — a Kubernetes object that says:
        "There is a folder on EC2 at /home/ec2-user/airflow/dags/"

Step 2: You created a PersistentVolumeClaim (PVC) — a Kubernetes object that says:
        "I need some storage, and I want to use the PV from Step 1"

Step 3: The Airflow pods say (in their config):
        "Mount the PVC at /opt/airflow/dags/ inside me"

Result: When the pod looks at /opt/airflow/dags/, it actually sees
        the files at /home/ec2-user/airflow/dags/ on EC2.
```

**Analogy:** A PV is like saying "there's a filing cabinet in room 204." A PVC is like saying "I need access to that filing cabinet." The mount is like putting a door from the pod directly to that filing cabinet.

### What deploy.sh Actually Does

When you run `./scripts/deploy.sh`, here's what happens in plain English:

1. **Checks your code for typos** — runs Python syntax checker on all DAG files
2. **Copies DAG files to EC2** — uses `rsync` (a smart copy tool that only sends files that changed)
3. **Copies Kubernetes config files to EC2** — same rsync
4. **Copies Flask website code to EC2** — same rsync
5. **Builds a new Docker image for Flask on EC2** — packages your website into a container
6. **Pushes that image to AWS ECR** — ECR is like a storage locker for Docker images
7. **Restarts Airflow pods** — forces them to see the new DAG files (prevents the stale cache bug)
8. **Restarts the Flask pod** — picks up the new website image
9. **Verifies everything is running** — checks pod statuses

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
- `deploy.sh` was copying files to `/home/ec2-user/airflow/dags/` on EC2
- But the PV config still said the folder was at `/tmp/airflow-dags/` (an old location from before you reorganized the project)

The pod mounted the old, empty folder. It looked inside and saw nothing. No DAGs, no errors — just silence.

**The fix:** Update the PV to point to the correct folder:

```yaml
# Before (wrong):
hostPath:
  path: /tmp/airflow-dags/

# After (correct):
hostPath:
  path: /home/ec2-user/airflow/dags/
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

**Last updated:** 2026-03-31
