# Failure Modes — AWS EC2 / Infrastructure

Back to [Failure Mode Index](../FAILURE_MODE_MAP.md)

---

### EC-1: SSH Unreachable (IP Restriction)

| Field | Detail |
|-------|--------|
| **Symptoms** | `ssh ec2-stock` hangs or times out. EC2 instance is running in AWS console. |
| **Root cause** | Security group restricts SSH to a specific IP address. Working from a new location (different IP) means blocked. |
| **Blast radius** | Total loss of access. Can't deploy, can't debug, can't view logs. |
| **Prevention** | Document the process for updating the security group IP. Keep AWS console access available as backup. |
| **Real incident?** | Recurring — by design (security), but requires awareness when changing locations. |

### EC-2: Disk Full

| Field | Detail |
|-------|--------|
| **Symptoms** | Pods crash with write errors. MariaDB inserts fail. Container image pulls fail. `df -h` shows >95% usage. |
| **Root cause** | Container images, Airflow logs, MariaDB data, and K3s system data all share one EBS volume. No log rotation or image pruning configured. |
| **Blast radius** | Everything fails. Can't write logs, can't pull images, can't insert data. |
| **Prevention** | Monitor `df -h` periodically. Prune old container images (`crictl rmi --prune`). Rotate Airflow logs. Set MariaDB `max_binlog_size`. |

### EC-3: Instance Stopped/Terminated

| Field | Detail |
|-------|--------|
| **Symptoms** | SSH fails. AWS console shows instance in `stopped` or `terminated` state. |
| **Root cause** | AWS maintenance events, billing issues, or accidental stop. K3s doesn't auto-recover gracefully on all restart scenarios. |
| **Blast radius** | Total outage. All services down. Data on EBS volumes preserved (if not terminated). |
| **Prevention** | Enable CloudWatch alarm for instance state changes. Consider reserved instance or savings plan for cost predictability. |

### EC-4: ECR Auth Boundary

| Field | Detail |
|-------|--------|
| **Symptoms** | Image pulls fail with 401 errors. `docker login` or `crictl pull` returns authentication error. |
| **Root cause** | ECR tokens are region-specific and expire after 12 hours. If token isn't refreshed before pod restart, pull fails. |
| **Blast radius** | Any pod that needs to pull an image from ECR. Existing running pods unaffected. |
| **Prevention** | Automate token refresh. `deploy.sh` already handles this — ensure any manual pod restarts also refresh the token first. |

### EC-5: Resource Exhaustion (CPU/Memory)

| Field | Detail |
|-------|--------|
| **Symptoms** | SSH sluggish. Commands timeout. Pods report OOMKilled (Out Of Memory Killed — OS force-killed a pod for exceeding its RAM limit). `top` shows high memory/CPU. |
| **Root cause** | All RAM and vCPU shared across all K3s pods plus the OS. Large DataFrame operations in DAGs, runaway log growth, or memory leaks push past limits. |
| **Blast radius** | Cascading pod evictions. SSH itself may become unusable if the OOM killer targets system processes. |
| **Prevention** | Set K8s resource limits per pod. Monitor with `kubectl top nodes` and `kubectl top pods`. |
