# Part 8–9: The Big Picture + EC2 Sizing

> Part of the [Plain English Guide](README.md)

---

## The Big Picture

Here's what your project does, start to finish, in one paragraph:

**Every day**, Airflow (running inside a pod on your EC2 server) wakes up and runs your Stock pipeline. The pipeline calls SEC EDGAR (a free U.S. government API) and asks for financial data about Apple, Microsoft, and Google — things like revenue, net income, and total assets from their annual SEC filings. It takes the messy, deeply nested response and flattens it into clean rows. Then it writes those rows into a MariaDB database (running directly on EC2, not in a pod). Meanwhile, the Weather pipeline does the same thing hourly with weather data from Open-Meteo. Your Flask website (running in its own pod) reads from MariaDB and shows the data on a dashboard that you can view in your browser through an SSH tunnel. **Every 30 minutes**, a separate monitoring DAG checks how fresh the data is — if it's too old, it sends a Slack notification (or logs a warning if Slack isn't configured). If any pipeline task fails or retries, you're notified once per hour per broken task, and when things recover, you get a "Recovered" message.

---

## What Size EC2 Do You Need?

### RAM and vCPU in plain English

Your EC2 instance is like a computer. RAM is memory — when it fills up, programs crash. vCPU is like the number of hands your computer has — more hands means more simultaneous tasks.

### The size options

| Size | RAM | vCPU | Verdict |
|------|-----|------|---------|
| t3.small | 2GB | 2 | Too small — K3s and Airflow alone use most of this |
| t3.medium | 4GB | 2 | Not enough — barely fits today's stack, no room for Kafka |
| **t3.large** | **8GB** | **2** | **Works — the right size for this project** |
| t3.xlarge | 16GB | 4 | Comfortable but costs ~$60/month more than needed |

### Why t3.large works

Current stack uses roughly 2.5–4 GB. t3.large has 8 GB — real breathing room. The roadmap replaces MariaDB with Snowflake (a cloud database), freeing ~300–500 MB because MariaDB gets uninstalled.

**Cost savings:** t3.large ~$61/month vs. t3.xlarge ~$121/month = **~$720/year saved**. If t3.large ever feels slow, resize to t3.xlarge in the AWS Console in ~2 minutes with no data loss.

### Kafka needs a special setting

Kafka (Java) grabs way more memory than needed by default. On t3.large, limit it:
```
KAFKA_HEAP_OPTS="-Xmx768m -Xms768m"
```
Also use **KRaft mode** (no Zookeeper), saving another ~500 MB.

---

## Resource Limits — What They Are and Why Every Pod Needs Them

**Without limits:** One runaway pod can eat all 8 GB of RAM, crashing every other pod. With limits, each pod has a ceiling it can't exceed.

**Requests vs. limits:**
- **Request** — "I need at least this much." Kubernetes guarantees this amount.
- **Limit** — "The absolute most I'm allowed to use." Exceeding it for memory = instant kill (OOMKilled). Exceeding it for CPU = throttled (slowed down).

Think of it like seats on a plane: the request is your reserved seat, the limit is the armrest rule.

### Current limits

| Pod | Observed baseline | Memory limit | Why |
|-----|------------------|-------------|-----|
| Flask/Dash | ~200 MB | 512 Mi | 2.5× baseline — lightweight app |
| Airflow webserver | ~500–800 MB | 1 Gi | Covers cold-start spike |
| Airflow scheduler | ~300–500 MB | 1 Gi | Heart of Airflow — generous limit |
| Airflow triggerer | ~100–200 MB | 256 Mi | Very lightweight |
| Airflow dag-processor | ~200–300 MB | 512 Mi | If it exceeds this, it's a DAG bug |

### What happens when a limit is hit?
- **Memory limit** → pod immediately killed and restarted (OOMKilled). `RESTARTS` count goes up.
- **CPU limit** → pod throttled (slowed), not killed. Things just run slower.

In both cases, other pods keep running normally — that's the whole point.

### How to check limits are in place
```bash
ssh ec2-stock kubectl describe pod my-kuber-pod-flask -n default | grep -A6 "Limits:"
ssh ec2-stock kubectl describe pod -n airflow-my-namespace -l component=scheduler | grep -A6 "Limits:"
```

### Where limits are defined
- Flask pod: `dashboard/manifests/pod-flask.yaml` (the `resources:` section)
- Airflow components: `airflow/helm/values.yaml` (under `webserver:`, `scheduler:`, etc.)

### After switching to t3.large
Run `ssh ec2-stock free -h` and check the "available" column — it should show 3–4 GB free at rest. If pods show high RESTARTS or `OOMKilled`, resize up or tune memory settings.

See [infrastructure/EC2_SIZING.md](../infrastructure/EC2_SIZING.md) for the full technical breakdown.
