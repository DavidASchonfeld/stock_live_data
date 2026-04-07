# Command Reference

Explanations of cryptic shell commands used in debugging and operations. Each command is broken down by flags so you understand what's happening.

**Quick Navigation**
- Need help understanding port/socket debugging? See [ss -tlnp Section](#ss--tlnp)
- Looking for file transfer commands? See [rsync Section](#rsync--avz)
- Want Kubernetes monitoring? See [kubectl Sections](#kubectl-get-pods--w)
- Need definitions of technical terms? See [GLOSSARY.md](GLOSSARY.md)

---

## Network & Port Debugging

### `ss -tlnp`

**Purpose:** Show all listening TCP ports and the processes that are listening on them.

**Breakdown:**
- `ss` — Socket Statistics tool (modern replacement for `netstat`)
- `-t` — TCP sockets only (exclude UDP)
- `-l` — Listening sockets only (exclude established connections)
- `-n` — Numeric output (show IP addresses, not hostnames; show port numbers, not service names)
- `-p` — Show associated process name/PID

**Example output:**
```
LISTEN     0  128     0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=1234))
LISTEN     0  128     0.0.0.0:80  0.0.0.0:*  users:(("nginx",pid=5678))
```

**Why Kubernetes NodePorts don't appear:**

K3S doesn't create a listening process for NodePorts. Instead, it uses **iptables rules** (kernel-level firewall) to redirect traffic:

```
External traffic on EC2 port 30080
  ↓ (iptables rule)
Redirects to pod's actual port (8080)
  ↓
Airflow container receives request
```

Since iptables is at the kernel level (not a user-space process), `ss -tlnp` doesn't show it. To debug NodePort issues, check iptables directly:

```bash
# View iptables rules (requires sudo)
sudo iptables -t nat -L -n -v

# Or check K3S logs
kubectl describe service airflow-ui -n airflow
```

---

### `curl -v http://localhost:8080`

**Purpose:** Test if a web service is responding on a port. `-v` shows verbose output (headers, response time).

**Breakdown:**
- `curl` — Command-line tool to fetch URLs
- `-v` — Verbose mode (show request headers, response code, timing)
- Other useful flags:
  - `-I` — Headers only (no response body)
  - `-X POST` — Change HTTP method (default is GET)
  - `-H "Authorization: Bearer token"` — Add custom headers

**Example:**
```bash
# Test Airflow UI
curl -v http://localhost:8080

# Test Flask API with headers
curl -v -H "Content-Type: application/json" http://localhost:5000/api/stocks
```

---

## File Transfer

### `rsync -avz local/ ec2:/remote/`

**Purpose:** Synchronize files between your machine and EC2 instance (one-way copy; only sends changes).

**Breakdown:**
- `rsync` — Remote sync tool (faster than `scp` for large directories)
- `-a` — Archive mode (preserve permissions, timestamps, symlinks)
- `-v` — Verbose (show files being transferred)
- `-z` — Compress during transfer (saves bandwidth)
- `local/` — Source directory (note trailing slash means "copy contents")
- `ec2:/remote/` — Destination (assumes SSH access to ec2)

**Examples:**
```bash
# Copy DAG files to EC2
rsync -avz airflow/dags/ ubuntu@ec2-ip:/opt/airflow/dags/

# Copy entire project
rsync -avz ./ ubuntu@ec2-ip:/home/ubuntu/stock-live-data/

# Exclude certain files (e.g., __pycache__, .git)
rsync -avz --exclude '__pycache__' --exclude '.git' ./ ec2:/project/
```

**Useful flags:**
- `--exclude PATTERN` — Skip files matching pattern
- `--delete` — Delete files on EC2 that don't exist locally (dangerous!)
- `--dry-run` — Show what would be transferred without actually doing it

---

## SSH & Remote Access

### `ssh -i key.pem ubuntu@ec2-ip`

**Purpose:** Connect to EC2 instance securely.

**Breakdown:**
- `ssh` — Secure Shell protocol
- `-i key.pem` — Use private key for authentication
- `ubuntu@ec2-ip` — Username and host

**Examples:**
```bash
# Connect to EC2
ssh -i ~/.aws/my-key.pem ubuntu@52.1.2.3

# Run a command without opening shell
ssh -i key.pem ubuntu@ec2-ip "kubectl get pods -n airflow"

# Copy a file from EC2 to local (scp = secure copy)
scp -i key.pem ubuntu@ec2-ip:/remote/file.txt ./local/
```

**Note:** If SSH times out, check your EC2 security group. Your IP might have changed since last setup. See [EC2 SSH IP Restriction](../OVERVIEW.md#ec2-ssh-access).

---

## Kubernetes Operations

For the full kubectl command reference, see [KUBECTL_COMMANDS.md](KUBECTL_COMMANDS.md).

---

## File System Inspection

### `df -h` and `df -i`

**Purpose:** Check disk space and inode usage.

**Breakdown:**
- `df` — Disk free
- `-h` — Human-readable format (MB, GB instead of bytes)
- `-i` — Show inode usage instead of space

**Example output:**
```
Filesystem     Size Used Avail Use% Mounted on
/dev/nvme0n1p1 50G  30G  20G  60%  /
```

**For inode issues:**
```bash
df -i

# Output: if "Use%" is >90%, you have too many small files
# Common cause: PersistentVolume with millions of log files
```

---

### `du -sh <directory>`

**Purpose:** Show total disk space used by a directory.

**Breakdown:**
- `du` — Disk usage
- `-s` — Summary (just the total, not per-subdirectory)
- `-h` — Human-readable

**Examples:**
```bash
# Check Airflow logs size
du -sh /opt/airflow/logs

# Check MariaDB data size
du -sh /var/lib/mysql

# Find large directories (sort by size)
du -sh /* | sort -h
```

---

## Process Management

### `ps aux | grep airflow`

**Purpose:** Find running processes matching a pattern.

**Breakdown:**
- `ps aux` — Show all running processes with details
- `grep airflow` — Filter to lines containing "airflow"

**Output:**
```
ubuntu  1234 0.5 2.3 1234567 89012 ?  Sl  10:30  0:45 python -m airflow scheduler
```

**Columns:**
- `USER` — Process owner
- `PID` — Process ID
- `%CPU` / `%MEM` — Resource usage
- `COMMAND` — The actual command

---

### `kill <PID>` and `kill -9 <PID>`

**Purpose:** Terminate a process.

**Breakdown:**
- `kill <PID>` — Graceful shutdown (SIGTERM signal)
- `kill -9 <PID>` — Force kill (SIGKILL signal; immediate)

**Use cases:**
```bash
# Gracefully stop a process
kill 1234

# Force kill if graceful didn't work (last resort)
kill -9 1234

# Kill all processes matching a name (with confirmation)
killall -i airflow
```

---

## Docker Commands (Local Development)

### `docker build -t myimage:tag .`

**Purpose:** Build a Docker image from Dockerfile in current directory.

**Breakdown:**
- `docker build` — Build image
- `-t myimage:tag` — Tag the image (name:version)
- `.` — Dockerfile location (current dir)

**Examples:**
```bash
# Build Airflow image
docker build -t airflow-custom:1.0 ./airflow/

# Build with build arguments
docker build --build-arg AIRFLOW_VERSION=2.5.0 -t airflow:2.5 .
```

---

### `docker push myimage:tag`

**Purpose:** Push a Docker image to a registry (ECR, Docker Hub, etc.).

**Breakdown:**
- Before pushing, tag the image with registry URL:
  ```bash
  docker tag myimage:1.0 123456789.dkr.ecr.us-east-1.amazonaws.com/myimage:1.0
  docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/myimage:1.0
  ```

See [ECR_SETUP.md](ECR_SETUP.md) for full authentication steps.

---

## Summary

These commands are essential for:
- **Debugging:** `kubectl logs`, `kubectl describe`, `curl`
- **Monitoring:** `kubectl get pods -w`, `kubectl top`, `df -h`
- **Access:** `ssh`, `kubectl exec`
- **File transfer:** `rsync`, `scp`

For more context on why you use these commands, see [DEBUGGING.md](DEBUGGING.md).
