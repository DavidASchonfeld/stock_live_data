# Technical Glossary

A reference for technical terms, abbreviations, and tools used in this project.

**Quick Navigation**
- Want to understand the architecture behind these terms? See [ARCHITECTURE.md](ARCHITECTURE.md)
- Looking for explanations of cryptic shell commands? See [COMMANDS.md](COMMANDS.md)
- Need Kubernetes CLI reference? See [KUBECTL_COMMANDS.md](KUBECTL_COMMANDS.md)

---

## Data & Pipeline Terms

### ETL (Extract, Transform, Load)
A three-phase data pipeline pattern:
- **Extract:** Fetch raw data from source (APIs, databases, files)
- **Transform:** Clean, normalize, and reshape data (pandas, SQL)
- **Load:** Store processed data in destination (database, data warehouse)

Your project extracts stock/weather data from APIs, transforms it with pandas, and loads it into MariaDB.

### DAG (Directed Acyclic Graph)
Airflow's way of defining workflows. A DAG is a visual representation of tasks and their dependencies:
- **Directed:** Arrows show task execution order (A → B → C)
- **Acyclic:** No cycles (a task can't indirectly depend on itself)

Example: `extract_stocks` → `transform_stocks` → `load_stocks`

Your DAGs: `dag_stocks.py`, `dag_weather.py`

### XCom (Cross-Communication)
Airflow's mechanism for tasks to pass data to each other. One task pushes a value; another task pulls it.

```python
# Task 1 pushes data
task_1.xcom_push(key="stock_data", value=df)

# Task 2 pulls it
df = context["task_instance"].xcom_pull(key="stock_data")
```

### SMA (Simple Moving Average)
A technical indicator calculated as the average of a stock's closing price over N days:
- **20-day SMA:** Average closing price of last 20 trading days
- **50-day SMA:** Average closing price of last 50 trading days
- **Usage:** Traders use it to identify trends; smooths out daily price noise

Your dashboard likely displays SMAs as lines on stock price charts.

### JSON Normalize
A pandas operation (`pd.json_normalize()`) that flattens nested JSON into a flat DataFrame.

```json
{
  "symbol": "AAPL",
  "timeSeries": {
    "2026-03-30": {"open": "100", "close": "102"}
  }
}
```
Becomes a flat table:
| symbol | timeSeries.2026-03-30.open | timeSeries.2026-03-30.close |
|--------|-------|--------|
| AAPL   | 100   | 102    |

---

## Kubernetes & Container Terms

### K3S
A lightweight, certified Kubernetes distribution optimized for edge and resource-constrained environments. Bundles Kubernetes into a single ~40 MB binary.

**Why you use it:** Full Kubernetes features (orchestration, auto-restart, rolling updates) at minimal resource overhead on your single EC2 instance.

**Alternative:** Full EKS (expensive) or Docker Compose (no orchestration).

### Kubernetes
An open-source container orchestration platform that automates deployment, scaling, and management of containerized applications.

K3S is a distribution of Kubernetes.

### Pod
The smallest unit in Kubernetes — one or more containers that share:
- Network namespace (single IP address, can communicate via localhost)
- Storage volumes (PersistentVolumes mounted at specific paths)

Your pods:
- Airflow scheduler + webserver
- MariaDB
- Flask + Dash application

### Node
A machine (physical or virtual) that runs Kubernetes. In your case, your EC2 instance is a single K3S node.

### NodePort Service
A Kubernetes Service type that exposes a pod's port on the node's IP address:
- Port `30000-32767` on the EC2 instance
- External traffic hits the EC2 public IP on that port
- K3S redirects to the actual pod port using iptables rules

Example: Airflow UI runs inside a pod on port 8080; NodePort exposes it as `ec2-instance-ip:30080` (or configured port).

**Why NodePort doesn't show in `ss -tlnp`:** K3S uses Linux iptables (firewall rules at kernel level), not a listening process.

### ClusterIP Service
A Kubernetes Service type for internal pod-to-pod communication:
- Only accessible from within the cluster
- Pods find each other via DNS (e.g., `mariadb:3306`)
- No external traffic reaches ClusterIP services

Example: Flask pod talks to MariaDB pod via ClusterIP Service.

### Namespace
A logical partition in a Kubernetes cluster. You use the `airflow` namespace to isolate Airflow pods from system pods.

Benefits:
- Prevent name conflicts (multiple apps can have a "web" pod)
- Resource quotas (limit CPU/memory per namespace)
- Easy cleanup (delete entire namespace and all its pods)

### PersistentVolume (PV)
A piece of storage (on your EC2 host or cloud provider) that **persists beyond pod lifecycle**. When a pod crashes, the PV remains.

**Type in your project:** `hostPath` — a folder on the EC2 machine that K3S mounts into pods.

Example PVs:
- `/tmp/airflow-dags` → Airflow DAG files
- `/var/lib/mysql` → MariaDB data

### PersistentVolumeClaim (PVC)
A "request for storage" — pods mount PVCs, not PVs directly. K3S matches a PVC to an available PV.

```yaml
Pod mounts PVC → PVC binds to PV → Pod accesses storage on EC2 host
```

### Container
A lightweight, isolated environment that runs an application with its dependencies packaged together.

Containers are created from **images** (blueprints) and run as **processes** managed by a container runtime (Docker, containerd).

### Container Runtime
Software that executes containers. Manages process isolation, filesystem, and networking.

**Your project uses:** containerd (lightweight, native Kubernetes support)

**Alternative:** Docker (heavier, more features)

### Docker Image
A blueprint for creating containers. Includes:
- Base OS (Ubuntu, Alpine, etc.)
- Application code
- Dependencies (Python, Node.js, etc.)
- Configuration

Your images are stored in AWS ECR; K3S pulls them from ECR and containerd runs them.

### ECR (Elastic Container Registry)
AWS's private Docker image registry. You push Docker images to ECR; K3S pulls from ECR to run containers.

**Alternative:** Docker Hub (public), private registries (self-hosted).

### Containerd
A lightweight container runtime (alternative to Docker). K3S uses containerd by default because:
- Smaller footprint (~20 MB vs Docker's ~100 MB)
- Native Kubernetes support (implements CRI — Container Runtime Interface)
- Faster pod startup

You don't interact with containerd directly; K3S does.

### Helm
A package manager for Kubernetes. Templates allow parameterized deployments:
- Write once, deploy to multiple environments
- Easier upgrades (change `values.yaml`, run `helm upgrade`)
- Version control of Kubernetes configurations

**Your project uses Helm for:** Airflow deployment with custom values (image, scheduler config, DB credentials).

---

## Database & Infrastructure Terms

### MariaDB
A MySQL-compatible relational database (drop-in replacement for MySQL). Stores your stock and weather data.

**Why you chose it:** Open-source, lightweight, good for single-instance deployments.

### YAML
A human-readable data format used for Kubernetes manifests and configuration files.

```yaml
kind: Pod
metadata:
  name: airflow-scheduler
spec:
  containers:
    - name: airflow
      image: apache/airflow:latest
```

### Manifest
A YAML file that describes Kubernetes resources (pods, services, volumes). K3S reads manifests and creates resources.

Example: `pv-dags.yaml` (PersistentVolume), `pod-flask.yaml` (Pod definition).

### EC2 Instance
An AWS virtual machine. You run a `t3.large` instance (2 vCPU, 8 GB RAM) that hosts your K3S cluster.

### SSH (Secure Shell)
A protocol for secure remote access to machines. You use SSH to connect to your EC2 instance:
```bash
ssh -i your-key.pem ubuntu@ec2-instance-ip
```

### Security Group
AWS firewall rules that control inbound/outbound traffic to EC2 instances.

**Your setup:** Restrict SSH access to your current IP, allow HTTP/HTTPS for Airflow UI and Flask API.

---

## Debugging & Operations Terms

### Inode
A filesystem data structure that represents a file or directory. Each file has a unique inode number.

**Debugging use:** When a PersistentVolume is "full," it might be an inode limit (too many small files) rather than disk space.

Check with: `df -i` (shows inode usage).

### iptables
Linux kernel's firewall rule system. K3S uses iptables to:
- Redirect NodePort traffic to pod ports
- Implement network policies
- Control inter-pod communication

**Why NodePort doesn't show in `ss -tlnp`:** iptables rules operate at kernel level, not as a listening process.

### Socket
An endpoint for network communication. `ss -tlnp` (socket statistics) shows listening TCP sockets and their associated processes.

### Port
A number (0-65535) used to identify a service on a machine.

- **Well-known ports:** 80 (HTTP), 443 (HTTPS), 3306 (MySQL), 8080 (Airflow UI)
- **Dynamic/private ports:** 30000-32767 (Kubernetes NodePort range)

### Log
Text output from applications. K3S logs are viewable via:
```bash
kubectl logs <pod-name> -n airflow
```

Use logs to debug pod crashes, API errors, or DAG failures.

### CRI (Container Runtime Interface)
Kubernetes's standard interface for container runtimes. Both Docker and containerd implement CRI, so K3S can use either.

### Replica
A copy of a pod. Kubernetes can run multiple replicas of the same pod for redundancy and load balancing.

Example: 3 replicas of a Flask pod = 3 identical Flask instances.

Your setup mostly uses 1 replica per service (to save resources on the single EC2 instance).

---

## Monitoring & Observability Terms

### Health Probe
A Kubernetes mechanism to check if a pod is healthy:
- **Liveness probe:** Is the pod alive? If not, restart it.
- **Readiness probe:** Is the pod ready to receive traffic?

Example: Flask pod health check hits `GET /health` endpoint; if it fails, K3S marks the pod unhealthy.

### Metrics
Quantitative measurements (CPU, memory, requests/sec) collected for monitoring.

Kubernetes can collect metrics; tools like Prometheus visualize them.

### Alert
A notification triggered when metrics exceed thresholds (e.g., "CPU > 80%").

---

## Development Terms

### Git Branch
A parallel version of your code. Main branches: `main` (production), feature branches (development).

Your setup: Code in Git, K3S reads manifests from Git.

### CI/CD (Continuous Integration / Continuous Deployment)
- **CI:** Automated testing when code is pushed
- **CD:** Automated deployment to production

Not fully configured in your project yet, but setup can trigger K3S deployments on Git push.

### Environment Variable
Configuration passed to applications at runtime. K3S manifests can inject env vars into pods.

Example: `AIRFLOW_HOME=/opt/airflow`, `AIRFLOW__CORE__EXECUTOR=KubernetesExecutor`

---

## Summary

Use this glossary when you encounter unfamiliar terms in logs, documentation, or Kubernetes errors. For deep explanations, see [ARCHITECTURE.md](ARCHITECTURE.md).
