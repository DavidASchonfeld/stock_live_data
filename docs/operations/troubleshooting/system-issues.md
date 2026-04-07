# System Issues

Troubleshooting OS-level problems, SSH warnings, kubectl permissions, and browser console errors.

**See also:** [Parent index](../TROUBLESHOOTING.md) | [DEBUGGING.md](../DEBUGGING.md) | [RUNBOOKS.md](../RUNBOOKS.md)

---

## Issue: `apt upgrade -y` Appears Frozen / No Output for Several Minutes

### Symptoms
- `sudo apt upgrade -y` runs for a while then goes completely silent
- No output, no progress indicator, cursor just sits there
- Can happen mid-upgrade or at the start of a large package

### Root Cause
`apt upgrade` encountered a config file prompt for a package with locally-modified config files (e.g. `/etc/ssh/sshd_config`, `/etc/systemd/...`). The `-y` flag auto-confirms package installation but does **not** auto-answer config file diff prompts — those require explicit input.

### Fix
Press **Enter** to accept the default (keep the existing config file). The upgrade will resume immediately.

If it's still frozen after pressing Enter, try pressing `n` (keep current) or `y` (use new version) depending on the prompt context.

### Prevention (for scripts)
Use `DEBIAN_FRONTEND=noninteractive` to suppress all interactive prompts and always keep the current config:
```bash
sudo DEBIAN_FRONTEND=noninteractive apt upgrade -y
```
This is safe for automated/scripted use but not recommended interactively — you won't see what config choices were made.

### Notes
- This is harmless — the upgrade did not fail, it was just waiting
- The prompt appears in the terminal output if you're actively watching, but is easy to miss in an overnight run
- Real incident: 2026-04-06, `apt upgrade` waited ~6 hours overnight for an Enter keypress ([CHANGELOG.md](../../incidents/CHANGELOG.md))

---

## Issue: SSH Post-Quantum Key Exchange Warning

### Solution

**Option 1: Upgrade OpenSSH on EC2** (recommended)
```bash
ssh ec2-stock
sudo yum update openssh-server openssh-clients -y
sudo systemctl restart sshd
```

**Option 2: Add SSH config workaround**
Edit `~/.ssh/config`:
```
Host ec2-stock
  HostKeyAlgorithms=ssh-ed25519,ecdsa-sha2-nistp256
  KexAlgorithms=curve25519-sha256,ecdh-sha2-nistp256
```

---

## Issue: `kubectl` — `permission denied` reading `/etc/rancher/k3s/k3s.yaml`

### Symptoms
`deploy.sh` Step 2e (or any subsequent `kubectl` command) fails via SSH with:
```
error: error loading config file "/etc/rancher/k3s/k3s.yaml": open /etc/rancher/k3s/k3s.yaml: permission denied
```

### Root Cause
K3s writes its kubeconfig to `/etc/rancher/k3s/k3s.yaml` owned by `root` (mode 600). The
`ubuntu` SSH user has no read permission. Unlike standalone `kubectl`, the K3s kubectl binary
(symlinked to `k3s`) reads this path **directly** and ignores `~/.kube/config`, so copying the
file doesn't help — the permissions on the source file must be fixed.

### Fix
`deploy.sh` Step 1c runs on every deploy and makes the file world-readable:
```bash
sudo chmod 644 /etc/rancher/k3s/k3s.yaml
```
Runs on every deploy so permissions are restored even if K3s restarts and rewrites the file.

### Verification
Run `./scripts/deploy.sh` — Step 2e and all subsequent `kubectl` steps should succeed.

---

## Issue: `TypeError: undefined is not an object (evaluating 'moment.tz')` in Browser Console

### Symptoms
- Browser console shows on the Airflow Home Page:
  ```
  [Error] TypeError: undefined is not an object (evaluating 'moment.tz')
      (anonymous function) (jquery-latest.js:...)
  ```
- Airflow UI still works correctly — this is cosmetic only

### Root Cause
A known Airflow 3.x bug in the legacy Flask-AppBuilder (FAB) components still embedded in some pages. The `AIRFLOW__WEBSERVER__DEFAULT_UI_TIMEZONE` env var (set in `values.yaml`) prevents `moment.tz.guess()` (auto-detect timezone), but FAB also calls `moment.tz(date, tz)` to format dates, which requires `moment-timezone.js` to be loaded synchronously. The script loading order in Airflow 3.x does not guarantee this. The fix would be a one-line change to a Jinja2 template inside Airflow's own source — moving the `<script src="moment-timezone.js">` tag to load before the FAB date-rendering script — but this lives in the Airflow project, not here.

### What Flask-AppBuilder is (and isn't)
FAB is a dependency **inside Airflow** — it's the old framework Airflow used to build its own web UI pages. It has nothing to do with our DAG code. Our DAGs use `from airflow.sdk import dag, task, XComArg`, which is the modern Airflow 3.x API. FAB is being phased out by the Airflow project itself: with each new Airflow release, more UI pages are rewritten in React, and as each page is converted the `moment.tz` error disappears from that page. This happens automatically on a version upgrade — no changes to our code are required.

### Options if you want to fix it
| Option | What it involves | Verdict |
|--------|-----------------|---------|
| **Wait for upstream fix** | Apache Airflow merges a template patch; we pick it up via a normal `helm upgrade` | Best option — zero effort, happens automatically |
| **Custom Docker image** | Build `FROM apache/airflow:3.1.8`, overwrite the offending FAB template file, push to ECR, point Helm at it | Fragile — breaks on every Airflow version bump; not worth it for a cosmetic error |
| **Do nothing** | Error only appears in the browser developer console (F12 → Console), never visible to users | Correct choice for this project |

### Status
Non-blocking. Only visible in browser developer console — not shown to users. Cannot be fixed via Helm config. Resolves automatically when Airflow upgrades the affected UI page from FAB to React.

---

## Issue: `404 Not Found` for Task Instance URL — "Mapped Task Instance ... was not found"

### Symptoms
Navigating to a bookmarked Airflow URL (saved before the 3.x upgrade) shows:
```
404 Not Found
The Mapped Task Instance with dag_id: `...`, run_id: `...`, task_id: `...`, and map_index: `-1` was not found
```

### Root Cause
Airflow 2.x API URLs represented **every** task instance — including non-mapped (regular) tasks — with `map_index: -1` as a sentinel value. Airflow 3.x changed the task instance API: non-mapped tasks no longer use `map_index` in the endpoint path, so any 2.x deep-link URL that includes `map_index=-1` returns 404 in 3.x.

This does **not** mean the task failed or that data is missing. It means the URL format is outdated.

### Fix
Discard the old bookmark. Navigate to the task instance via the Airflow 3.x UI:
1. Open the Airflow UI → click the DAG name
2. Click a run in the **Runs** grid
3. Click the task name in the task grid

### Notes
- Only affects saved/bookmarked URLs from before the upgrade; all new links generated by the 3.x UI are correct
- Confirmed non-issue: all DAG runs and task states remain accessible and correct via the new navigation path
