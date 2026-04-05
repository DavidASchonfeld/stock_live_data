# EC2 Instance Sizing Guide

How to choose the right EC2 instance size for this stack, and what changes are required to run comfortably on a t3.large.

**Navigation:**
- Kafka setup steps? → [../BACKLOG.md](../BACKLOG.md)
- K3s resource risks? → [K3S_RISKS.md](K3S_RISKS.md)
- System architecture? → [../architecture/SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md)

---

## Component RAM Requirements

These are practical estimates for this specific stack at low/portfolio traffic levels.

| Component | Where it runs | Approx RAM |
|-----------|--------------|-----------|
| K3s (kubelet + control plane) | EC2 host | ~500MB |
| Airflow webserver pod | K3s pod | ~500MB–1GB |
| Airflow scheduler pod | K3s pod | ~300–500MB |
| Airflow dag-processor pod | K3s pod | ~200–300MB |
| Airflow triggerer pod | K3s pod | ~100–200MB |
| Airflow PostgreSQL pod | K3s pod | ~200–300MB |
| Flask/Dash dashboard pod | K3s pod | ~200–500MB |
| MariaDB (pre-Snowflake) | EC2 host directly | ~300–500MB |
| **Baseline total (no Kafka)** | | **~2.3–4GB** |
| Kafka broker (tuned) | K3s pod | ~900MB–1.2GB |
| **Total with Kafka (tuned)** | | **~3.2–5.2GB** |

> **After Snowflake migration:** MariaDB is removed from EC2 entirely, saving ~300–500MB and eliminating one process from the host. This is the single biggest action that makes t3.large comfortable long-term.

---

## Sizing Verdict

| Instance | vCPU | RAM | Monthly cost* | Verdict |
|----------|------|-----|--------------|---------|
| t3.small | 2 | 2GB | ~$15 | No — K3s alone can hit 1GB; no room for anything else |
| t3.medium | 2 | 4GB | ~$30 | No — fits today's stack barely, no headroom for Kafka |
| **t3.large** | **2** | **8GB** | **~$61** | **Yes — viable with tuning (see conditions below)** |
| t3.xlarge | 4 | 16GB | ~$121 | Comfortable, but ~$60/mo more than needed |

*us-east-1 on-demand Linux pricing, approximate.

---

## K8s Resource Limits (set in manifests)

Every pod has explicit `requests` and `limits` to prevent a single runaway component from OOMKilling (Out Of Memory Killing — the OS force-kills a pod that exceeds its RAM allowance) others.

- **`requests`** — the amount K8s guarantees and uses to decide which node to schedule the pod on.
- **`limits`** — the hard ceiling; exceeding memory causes an OOMKill restart (pod is killed and restarted by the OS); exceeding CPU causes throttling (slowed down, not killed).
- **Amounts chosen at ~2× observed baseline** to absorb startup spikes, while keeping the total K8s limit ceiling ~4.25 Gi — safely under 8 GB even with K3s and MariaDB running on the host.

| Component | Memory request | Memory limit | CPU request | CPU limit | Source file |
|-----------|---------------|--------------|-------------|-----------|-------------|
| Flask/Dash | 256 Mi | 512 Mi | 100m | 500m | `dashboard/manifests/pod-flask.yaml` |
| Airflow webserver | 512 Mi | 1 Gi | 200m | 1000m | `airflow/helm/values.yaml` |
| Airflow scheduler | 512 Mi | 1 Gi | 200m | 1000m | `airflow/helm/values.yaml` |
| Airflow triggerer | 128 Mi | 256 Mi | 100m | 300m | `airflow/helm/values.yaml` |
| Airflow dag-processor | 256 Mi | 512 Mi | 100m | 500m | `airflow/helm/values.yaml` |
| **K8s limits total** | **1.66 Gi** | **4.25 Gi** | | | |
| K3s system (host) | — | ~500 Mi | — | — | not a K8s pod |
| MariaDB (host, pre-Snowflake) | — | ~500 Mi | — | — | not a K8s pod |
| **Worst-case total** | | **~5.25 Gi** | | | **~2.75 Gi free** |

> Kafka (Step 3) adds ~1 Gi limit. After Snowflake removes MariaDB (~500 Mi), net headroom stays positive.
> See the inline YAML comments in the source files above for per-field rationale.

---

## Conditions for t3.large to Work

Three things make t3.large viable. All three are already part of the Step 2 roadmap.

### Condition 1: Complete Snowflake migration (remove MariaDB from EC2)

MariaDB runs directly on EC2 (outside K3s) and consumes ~300–500MB RAM continuously. Once your data moves to Snowflake (a cloud database), MariaDB is shut down and uninstalled from EC2. This is the highest-ROI change for freeing memory.

See [../BACKLOG.md](../BACKLOG.md) for the removal steps.

### Condition 2: Use KRaft mode for Kafka (no Zookeeper)

Kafka traditionally requires a companion process called Zookeeper, which costs ~500MB RAM. Kafka 3.x+ supports **KRaft mode**, which eliminates Zookeeper entirely. For a single-broker setup like this one, KRaft is simpler and uses less memory.

Set this in your Kafka K8s manifest:
```yaml
env:
  - name: KAFKA_PROCESS_ROLES
    value: "broker,controller"
  - name: KAFKA_NODE_ID
    value: "1"
  - name: KAFKA_CONTROLLER_QUORUM_VOTERS
    value: "1@localhost:9093"
```

### Condition 3: Tune Kafka's JVM heap

Kafka is a Java application and by default requests 1–2GB of heap. On t3.large you need to constrain it:

```yaml
# In Kafka K8s manifest (env section):
- name: KAFKA_HEAP_OPTS
  value: "-Xmx768m -Xms768m"
```

This limits Kafka to 768MB heap, which is sufficient for a single-topic, low-throughput portfolio pipeline.

Also set a K8s memory limit to prevent the JVM from creeping beyond the heap setting:
```yaml
resources:
  requests:
    memory: "900Mi"
  limits:
    memory: "1Gi"
```

---

## When to Resize Back Up

If any of these happen after switching to t3.large, consider upgrading to t3.xlarge:

| Signal | How to check |
|--------|-------------|
| Pod shows `OOMKilled` in last state (`OOMKilled` = Out Of Memory Killed — pod was force-killed for exceeding RAM limit) | `kubectl get pods -A` → look for `OOMKilled` |
| RAM usage consistently above 85% | `ssh ec2-stock free -h` |
| CPU sustained above 80% during DAG runs | `ssh ec2-stock top` |
| Kafka consumer lag keeps growing | Check Kafka consumer group offsets |
| Airflow scheduler task queuing delays | Airflow UI → task durations trending up |

Resizing is a stop/change-type/start operation in the AWS Console — no data loss, ~2 minutes downtime.

---

## Monitoring RAM After Go-Live

Run these after switching to t3.large to confirm headroom:

```bash
# Total RAM usage on EC2 host
ssh ec2-stock free -h

# RAM per process (sorted)
ssh ec2-stock ps aux --sort=-%mem | head -20

# K3s pod memory usage
ssh ec2-stock kubectl top pods --all-namespaces
# (requires metrics-server; if not installed, use 'kubectl describe node' instead)

# Check if any pod was OOMKilled recently
ssh ec2-stock kubectl get pods -A -o json | python3 -c "
import sys, json
data = json.load(sys.stdin)
for p in data['items']:
    for cs in p.get('status', {}).get('containerStatuses', []):
        last = cs.get('lastState', {}).get('terminated', {})
        if last.get('reason') == 'OOMKilled':
            print(p['metadata']['namespace'], p['metadata']['name'], cs['name'])
"
```

---

**Last updated:** 2026-04-04 — Added K8s resource limits table; limits now set in pod-flask.yaml and values.yaml.
