# K3s Hidden Complexity & Risks

A deep dive into the non-obvious risks of running K3s on a single EC2 instance with Docker images and persistent storage. These are the things that aren't in the "Getting Started" tutorials.

**Navigation:**
- PV/PVC specific deep dive? → [PERSISTENCE.md](PERSISTENCE.md)
- Full failure catalog? → [../architecture/FAILURE_MODE_MAP.md](../architecture/FAILURE_MODE_MAP.md)
- Blast radius of each failure? → [../architecture/COMPONENT_INTERACTIONS.md](../architecture/COMPONENT_INTERACTIONS.md)

---

## 1. Single-Node K3s: What You Gain and What You Lose

### What You Gain

- **Cost efficiency** — One t3.large (~$54/mo) vs. EKS cluster ($73/mo just for control plane + compute)
- **Simplicity** — No multi-node networking, no leader election complexity, no split-brain scenarios
- **Full K8s API** — Production-grade orchestration (auto-restart, rolling updates, health checks)
- **Portfolio value** — Demonstrates real K8s knowledge without cloud-managed training wheels

### What You Lose (Hidden Costs)

**No rescheduling.** In multi-node K8s, if a node fails, pods move to other nodes. With one node, if the node has resource pressure, pods are evicted with nowhere to go. They stay in `Pending` until resources free up — which may require human intervention.

**No rolling update safety.** A `helm upgrade` that breaks pod startup takes down ALL instances of that component simultaneously. In multi-node setups, K8s rolls updates one pod at a time and stops if the new version fails. Single-node means you're updating your only copy.

**Shared failure domain.** CPU spike in one pod affects all pods. Memory leak in Airflow starves MariaDB. A `crictl` bug or kernel panic takes everything down at once.

**No network partition testing.** All pod-to-pod traffic is localhost. You'll never see DNS resolution failures, network timeouts between services, or connection pool exhaustion that would appear in a multi-node cluster. If you move to a production multi-node setup later, expect networking bugs you've never seen.

### What to Do About It

You don't need to solve these — this is a portfolio project. But you should **document that you understand them**. In interviews, saying "I chose single-node K3s for cost and accepted these tradeoffs" is more impressive than pretending it's production-grade.

---

## 2. containerd vs Docker: The Image Lifecycle Gap

K3s uses containerd, not Docker. This creates subtle differences:

### Building Images

You build with Docker on your Mac. containerd on EC2 pulls from ECR. They're compatible (both use OCI image spec), but:

- **`docker images` on EC2 shows nothing useful** — Docker daemon isn't managing K3s images. Use `crictl images` instead.
- **`docker pull` on EC2 doesn't help K3s** — containerd has its own image cache. Images pulled by Docker aren't visible to containerd.
- **Image pruning** — `docker system prune` doesn't free space for K3s. Use `crictl rmi --prune` to clean containerd's cache.

### Debugging Image Issues

```bash
# What images does K3s/containerd actually have?
sudo crictl images

# What images is a pod trying to use?
kubectl describe pod <pod-name> -n <namespace>
# Look at "Containers:" → "Image:" field

# Force re-pull an image (after pushing a new version to ECR)
kubectl delete pod <pod-name> -n <namespace>
# Pod recreates and pulls fresh from ECR (if imagePullPolicy: Always)
```

### The ECR Authentication Dance

ECR tokens expire every 12 hours. The lifecycle:

```
1. deploy.sh runs: aws ecr get-login-password → generates token
2. Token stored as K8s Secret (ecr-credentials)
3. Pod spec references imagePullSecrets: ecr-credentials
4. When pod starts: containerd uses token to pull from ECR
5. 12 hours later: token expires
6. If pod restarts after 12 hours: ImagePullBackOff
```

**The risk:** If you don't deploy for >12 hours, any pod restart (crash, eviction, manual delete) will fail to pull its image. The pod was running fine, you deleted it expecting a quick restart, and now it's stuck in `ImagePullBackOff`.

**Mitigation options:**
- Run `deploy.sh` at least once per day (refreshes token)
- Create a K8s CronJob that refreshes the ECR token automatically
- Use a long-lived credential helper on the node

---

## 3. The Helm State Management Trap

Helm is the "package manager" for your Airflow deployment. But it maintains its own state, and conflicts with manual changes are silent.

### How Helm State Works

```
Helm stores release state as K8s Secrets:
  sh.helm.release.v1.airflow.v1  (revision 1)
  sh.helm.release.v1.airflow.v2  (revision 2)
  sh.helm.release.v1.airflow.v3  (revision 3, current)

Each secret contains:
  - The complete rendered manifests for that revision
  - The values.yaml used
  - The chart version
```

### The Drift Danger

If you manually `kubectl patch` a resource that Helm manages (e.g., fixing a service selector), Helm doesn't know about it. The state becomes:

```
Helm thinks:  service selector = component: webserver  (from values.yaml)
Cluster has:  service selector = component: api-server (from your kubectl patch)
```

Next `helm upgrade`:
- **Best case:** Helm overwrites your patch with whatever's in `values.yaml` → selector reverts → endpoint breaks again
- **Worst case:** Three-way merge conflict → unpredictable behavior

### The Rule

**If Helm manages it, change it through Helm.** This means:

1. Make the fix in `values.yaml` or the Helm chart
2. Run `helm upgrade` to apply
3. If you need an emergency manual fix, do it via `kubectl` BUT immediately backport the change to `values.yaml`
4. Track manual fixes in a "manual patches" section of your changelog until they're backported

### What Helm Manages vs. What You Manage

| Resource | Managed by |
|----------|-----------|
| Airflow pods (scheduler, api-server, triggerer, processor) | Helm |
| Airflow services | Helm |
| Airflow PostgreSQL | Helm |
| Airflow PV/PVC (if defined in Helm chart) | Helm |
| Your custom PV/PVC (pv-dags.yaml, etc.) | You (kubectl apply) |
| Flask pod and service | You (kubectl apply) |
| K8s Secrets (db-credentials, ecr-credentials) | You (kubectl create/apply) |
| Custom NodePort services | You (kubectl apply) |

---

## 4. Resource Contention on a Single Node

### Your Current Resource Budget

```
t3.xlarge: 4 vCPU, 16 GB RAM

Approximate usage (no resource limits set):
┌──────────────────────────┬───────┬────────┐
│ Component                │ CPU   │ Memory │
├──────────────────────────┼───────┼────────┤
│ K3s system (kubelet,     │ 0.5   │ ~500MB │
│ containerd, coredns)     │       │        │
├──────────────────────────┼───────┼────────┤
│ Airflow Scheduler        │ 0.5-1 │ ~1-2GB │
│ Airflow API Server       │ 0.2   │ ~500MB │
│ Airflow Triggerer        │ 0.1   │ ~300MB │
│ Airflow DAG Processor    │ 0.3   │ ~500MB │
│ Airflow PostgreSQL       │ 0.2   │ ~500MB │
├──────────────────────────┼───────┼────────┤
│ MariaDB                  │ 0.3   │ ~1-2GB │
├──────────────────────────┼───────┼────────┤
│ Flask + Dash             │ 0.1   │ ~200MB │
├──────────────────────────┼───────┼────────┤
│ OS + SSH + misc          │ 0.2   │ ~500MB │
├──────────────────────────┼───────┼────────┤
│ TOTAL (approximate)      │ 2.4   │ ~6-8GB │
│ HEADROOM                 │ 1.6   │ ~8-10GB│
└──────────────────────────┴───────┴────────┘
```

You have headroom now. But these scenarios eat it fast:

- **Large DataFrame processing** — A DAG that fetches 10,000 rows and runs pandas operations can spike to 2-4GB temporarily
- **Multiple simultaneous DAG runs** — Two DAGs running extract+transform at the same time
- **Log accumulation** — Airflow logs grow unbounded; PostgreSQL WAL logs grow during heavy writes
- **containerd image cache** — Each image version stays cached until manually pruned

### Setting Resource Limits (Future Improvement)

When you're ready, add resource requests/limits to protect critical pods:

```yaml
# Priority: protect MariaDB and Scheduler above all else
# These are the two components whose failure cascades widest

# In pod spec:
resources:
  requests:        # guaranteed minimum
    memory: "1Gi"
    cpu: "250m"
  limits:          # hard ceiling
    memory: "2Gi"
    cpu: "1000m"
```

**Requests** guarantee minimum resources. **Limits** set a ceiling — if a pod exceeds its memory limit, K8s OOMKills it (Out Of Memory Kill: the OS force-kills the pod and restarts it). Set limits generously to avoid unnecessary kills, but set them to prevent one pod from starving everything.

---

## 5. DNS and Service Discovery Gotchas

### How Pod-to-Pod Communication Works in K3s

Pods don't use IP addresses directly. They use K8s Service names, which resolve via CoreDNS:

```
Flask pod wants MariaDB:
  1. Flask code connects to "mariadb-service.default.svc.cluster.local:3306"
     (or just "mariadb-service" if in same namespace)
  2. CoreDNS resolves to ClusterIP (e.g., 10.43.100.50)
  3. kube-proxy (iptables rules) routes to actual pod IP (e.g., 10.42.0.15)
  4. Connection established
```

### What Can Go Wrong

**CoreDNS pod crashes** — All DNS resolution fails. Pods can't find each other by name. This is rare but catastrophic. Check with:
```bash
kubectl get pods -n kube-system | grep coredns
```

**Service name typo** — Connection hangs (DNS resolves to nothing) or connects to wrong service. Symptoms look like "network unreachable" but the real issue is the service name in your code.

**Namespace boundary** — Services in `airflow-my-namespace` aren't automatically visible from `default` namespace by short name. Use fully qualified: `service-name.namespace.svc.cluster.local`.

**Your project's shortcut** — Your DAGs connect to MariaDB using the direct IP (`<MARIADB_PRIVATE_IP>`), bypassing K8s DNS entirely. This works because MariaDB runs on the EC2 host (not as a K8s-managed pod). If you ever move MariaDB into K8s as a pod, you'll need to switch to service-name-based connections.

---

## 6. K3s Upgrades and Maintenance

### K3s Auto-Updates

K3s can be configured to auto-update, but on a single-node cluster this means:

1. K3s binary updates
2. K3s restarts
3. All pods restart
4. If the new K3s version has a bug, everything is down
5. No rollback path without SSH access

**Recommendation:** Don't enable auto-updates on a single-node cluster. Update manually when you've verified the new version in release notes.

### Updating K3s Manually

```bash
# On EC2:
# 1. Check current version
k3s --version

# 2. Check what's available
curl -s https://update.k3s.io/v1-release/channels | jq

# 3. Back up current state
kubectl get all --all-namespaces -o yaml > /tmp/k8s-backup.yaml

# 4. Update (replaces binary and restarts)
curl -sfL https://get.k3s.io | sh -

# 5. Verify
k3s --version
kubectl get pods --all-namespaces
```

---

## 7. Security Considerations

### What's Exposed

```
EC2 Security Group:
  - Port 22 (SSH): restricted to your IP
  - Port 30080 (Airflow UI NodePort): only via SSH tunnel
  - Port 32147 (Flask dashboard NodePort): only via SSH tunnel
```

Your NodePorts are exposed on the EC2 instance's network interface. Anyone who can reach the EC2 IP on those ports can access Airflow UI and your dashboard. The SSH tunnel adds a layer of security, but the ports themselves are open on the EC2 side.

### K8s Secrets Are Not Encrypted at Rest

K8s Secrets are base64-encoded, not encrypted. Anyone with `kubectl` access can read them:
```bash
kubectl get secret db-credentials -n airflow-my-namespace -o jsonpath='{.data.DB_PASSWORD}' | base64 -d
```

For a portfolio project, this is fine. For production, you'd use:
- AWS Secrets Manager with External Secrets Operator
- Sealed Secrets (encrypted in Git, decrypted in cluster)
- SOPS (encrypted files in Git)

### What to Tell Recruiters

"I use K8s Secrets for credential management, which are base64-encoded and adequate for this single-user development environment. For production, I'd implement AWS Secrets Manager with External Secrets Operator for encryption at rest and centralized rotation."

---

**Last updated:** 2026-03-31
