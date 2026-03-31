# Refactor: K3S Runtime Migration — Docker Mode → ECR + Containerd

## Why This Change Was Made

K3S was installed on the EC2 instance with a `--docker` flag in `/etc/systemd/system/k3s.service`:

```
ExecStart=/usr/local/bin/k3s server --docker
```

This is a **legacy/deprecated mode** where K3S delegates all container management to the Docker daemon instead of using its own built-in containerd runtime. The `--docker` flag has been deprecated since Kubernetes removed dockershim in v1.24 and is not the standard K3S setup.

The deploy script (`scripts/deploy.sh`) was written assuming the **standard K3S setup** (containerd runtime), which has a separate image store from Docker and requires an explicit import step after `docker build`. Because the EC2 was running `--docker` mode, K3S never started its own containerd process — so the import step's socket (`/run/k3s/containerd/containerd.sock`) didn't exist and the step always failed.

This refactor corrects the mismatch: the EC2 now runs K3S with its default containerd runtime, and images are stored in and pulled from AWS ECR instead of a local Docker image store.

---

## Architecture Comparison

### Old Setup (`--docker` mode, no registry)

| Component | Old Behavior |
|---|---|
| K3S runtime | Docker daemon (`--docker` flag in k3s.service) |
| Image store | Docker's image store (shared with K3S) |
| After `docker build` | Image immediately visible to K3S — no import needed |
| Registry | None — all images local to EC2 |
| `imagePullPolicy` | `Never` (never try to pull; always use local Docker image) |
| deploy.sh Step 5 | **Broken** — tried to import via `/run/k3s/containerd/containerd.sock` (socket didn't exist) |

The `--docker` mode *worked* for running pods (K3S and Docker shared the same image store), but the deploy script's Step 5 assumed the standard containerd setup and failed.

### New Setup (default containerd + ECR)

| Component | New Behavior |
|---|---|
| K3S runtime | Built-in containerd (K3S default, no `--docker` flag) |
| Image store | K3S containerd's own image store (separate from Docker's) |
| After `docker build` | Image exists in Docker's store only — must be pushed to a registry for K3S to access it |
| Registry | AWS ECR (Elastic Container Registry) — private registry in your AWS account |
| `imagePullPolicy` | `IfNotPresent` — pull from ECR on first use; use cached image on subsequent restarts |
| deploy.sh Step 5 | Refreshes a Kubernetes `ecr-credentials` secret so containerd can authenticate to ECR |

---

## What Changed in Each File

| File | Change |
|---|---|
| `scripts/deploy.sh` | Step 4: adds `docker tag` + `docker push` to ECR after build. Step 5: replaced broken containerd import with ECR pull-secret refresh. |
| `dashboard/manifests/pod-flask.yaml` | `image:` changed to full ECR URI. `imagePullPolicy: Never` → `IfNotPresent`. Added `imagePullSecrets` referencing the `ecr-credentials` secret. |
| `OVERVIEW.md` | Updated deploy step descriptions and tech stack table. |

---

## Authentication Chain

No passwords or tokens are stored anywhere in the repo or on the EC2 filesystem.

```
EC2 Instance IAM Role
  └── AmazonEC2ContainerRegistryPowerUser policy
        ├── Docker push (in deploy.sh):
        │     aws ecr get-login-password | docker login --password-stdin <ECR_REGISTRY>
        │     docker push <ECR_IMAGE>
        │
        └── K3S containerd pull (via Kubernetes secret):
              kubectl create secret docker-registry ecr-credentials \
                --docker-password=$(aws ecr get-login-password)
              (refreshed on every deploy run)
```

ECR tokens are valid for 12 hours. The `ecr-credentials` secret is refreshed on every `deploy.sh` run. Because `imagePullPolicy: IfNotPresent` is used, a running pod does not re-pull the image on restart — it uses the cached image in containerd's local store. Only a new deploy (which pushes a new image and refreshes the secret) triggers an ECR pull.

---

## One-Time EC2 Setup (Runbook)

These steps are done once before running the new `deploy.sh` for the first time. They require ~3–5 minutes of cluster downtime when K3S restarts.

### 1. Create the ECR repository
In AWS Console → ECR → Create repository → name: `my-flask-app`, private visibility.

Note the registry URI shown after creation:
```
<ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com
```

### 2. Attach IAM policy to the EC2 instance role
AWS Console → EC2 → your instance → Security tab → IAM role → Add permissions → Attach `AmazonEC2ContainerRegistryPowerUser`.

If the instance has no IAM role: IAM → Roles → Create role → EC2 use case → attach `AmazonEC2ContainerRegistryPowerUser` → attach the new role to the EC2 instance.

### 3. Remove `--docker` from k3s.service
```bash
ssh ec2-stock
sudo vi /etc/systemd/system/k3s.service
# Remove "--docker" from the ExecStart line, save and quit
sudo systemctl daemon-reload
sudo systemctl restart k3s
```

### 4. Verify containerd is now active
```bash
sudo k3s ctr images ls
# Should succeed (previously this command would time out)

kubectl get pods --all-namespaces
# Airflow pods: will restart and pull their official Docker Hub images — expect ~2 min recovery
# Flask pod: will show ImagePullBackOff or ErrImageNeverPull — this is expected
#            It will be fixed by the first run of the new deploy.sh
```

### 5. Update pod-flask.yaml with your ECR URI
In `dashboard/manifests/pod-flask.yaml`, substitute your actual `<ACCOUNT_ID>` and `<REGION>` into the image field.

### 6. Update deploy.sh with your ECR details
In `scripts/deploy.sh`, fill in `ECR_REGISTRY` and `AWS_REGION` at the top of the file.

### 7. Run the new deploy.sh
```bash
./scripts/deploy.sh
```

---

## Rollback Plan

If something goes wrong and you need to revert to the old `--docker` setup:

```bash
ssh ec2-stock
sudo vi /etc/systemd/system/k3s.service
# Re-add "--docker" to the ExecStart line
sudo systemctl daemon-reload
sudo systemctl restart k3s
```

Then revert `dashboard/manifests/pod-flask.yaml` to:
```yaml
image: my-flask-app:latest
imagePullPolicy: Never
```
(and remove the `imagePullSecrets` block)

The Docker daemon and its image store remain on the instance throughout — the `my-flask-app:latest` image built by the old deploy flow is still there unless explicitly deleted.
