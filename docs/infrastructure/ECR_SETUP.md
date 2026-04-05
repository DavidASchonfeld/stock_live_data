# ECR Migration — Setup Complete

One-time checklist for the migration from `--docker` mode to K3S + AWS ECR. All steps completed.

**Quick Navigation**
- Want to understand containerization? See [ARCHITECTURE.md](ARCHITECTURE.md#container-runtime-docker-vs-containerd)
- Looking for Docker/push commands? See [COMMANDS.md](COMMANDS.md#docker-commands-local-development)
- Need to understand ECR or docker push flow? See [GLOSSARY.md](GLOSSARY.md#ecr-elastic-container-registry)
- Debugging deployment issues? See [DEBUGGING.md](DEBUGGING.md)

---

## AWS Console

- [x] **1. Create ECR repository**
  - AWS Console → ECR → **Create repository**
  - Name: `my-flask-app`, visibility: **Private**
  - After creation, copy the registry URI (looks like `123456789.dkr.ecr.us-east-1.amazonaws.com`)

- [x] **2. Attach IAM policy to EC2 instance role**

  **If the instance already has an IAM role:**
  - AWS Console → EC2 → your instance → **Security** tab → click the IAM role link
  - **Add permissions** → Attach **`AmazonEC2ContainerRegistryPowerUser`**

  **If the instance has no IAM role (create and attach one):**

  *Step A — Create the role:*
  1. AWS Console → **IAM → Roles → Create role**
  2. Trusted entity type: **AWS service**, Use case: **EC2** → Next
  3. Search and attach policy: **`AmazonEC2ContainerRegistryPowerUser`** → Next
  4. Role name: `ec2-ecr-role` → **Create role**

  *Step B — Attach it to your EC2 instance:*
  1. **EC2 → Instances** → select your instance (checkbox)
  2. **Actions** button (top-right, above instance list) → **Security → Modify IAM role**
  3. Select `ec2-ecr-role` from the dropdown → **Update IAM role**

---

## Fill in Placeholders (Mac)

- [x] **3. `scripts/deploy.sh` lines 13–15** — replace both placeholders with your real values:
  ```bash
  ECR_REGISTRY="123456789.dkr.ecr.us-east-1.amazonaws.com"
  ECR_IMAGE="$ECR_REGISTRY/my-flask-app:latest"
  AWS_REGION="us-east-1"
  ```

- [x] **4. `dashboard/manifests/pod-flask.yaml` line 12** — replace the placeholder:
  ```yaml
  image: 123456789.dkr.ecr.us-east-1.amazonaws.com/my-flask-app:latest
  ```

---

## SSH to EC2 (~3–5 min cluster downtime)

- [x] **5. Remove `--docker` from k3s.service**
  ```bash
  ssh ec2-stock
  sudo vi /etc/systemd/system/k3s.service
  # Remove "--docker" from the ExecStart line, save (:wq)
  sudo systemctl daemon-reload
  sudo systemctl restart k3s
  ```

- [x] **6. Verify containerd is running**
  ```bash
  sudo k3s ctr images ls          # should succeed (not "connection refused")
  kubectl get pods --all-namespaces
  # Airflow pods: recover on their own (~2 min, pull from Docker Hub)
  # Flask pod: will show ImagePullBackOff — expected, fixed by the first deploy run
  ```

---

## Back on Mac

- [x] **7. Run the deploy script**
  ```bash
  chmod +x scripts/deploy.sh   # first time only
  ./scripts/deploy.sh
  ```

---

## Deploy Script — Bug Fix (2026-03-30)

**Issue Found:** First deployment attempt failed in Step 2c (Kubernetes manifest sync) with error:
```
rsync: [Receiver] mkdir "/home/ubuntu/dashboard/manifests" failed: No such file or directory
```

**Root Cause:** Step 1's directory creation only created `$EC2_BUILD_PATH` for the dashboard, but didn't create the nested `manifests` subdirectory needed by Step 2c's rsync command.

**Fix Applied:** Updated Step 1 directory creation to explicitly include the manifests path:
```bash
# Line 21 (BEFORE):
ssh "$EC2_HOST" "mkdir -p $EC2_DAG_PATH $EC2_HELM_PATH $EC2_BUILD_PATH"

# Line 21 (AFTER):
ssh "$EC2_HOST" "mkdir -p $EC2_DAG_PATH $EC2_HELM_PATH $EC2_BUILD_PATH $EC2_DASHBOARD_PATH/manifests"
```

Also added a new variable on line 12 for clarity:
```bash
EC2_DASHBOARD_PATH="/home/ubuntu/dashboard"
```

**Why this solution:**
1. **Using `mkdir -p` with nested paths** — creates both `/home/ubuntu/dashboard` and `/home/ubuntu/dashboard/manifests` in one command
2. **Idempotent** — the `-p` flag prevents errors if directories already exist, so re-running deploy.sh is safe
3. **Maintainable** — using a variable instead of hardcoding makes the path explicit and reusable
4. **Matches the pipeline** — ensures Step 2c's rsync to `/home/ubuntu/dashboard/manifests/` always succeeds

**Verification:** After the fix, `./scripts/deploy.sh` runs all 7 steps without rsync errors and successfully deploys the Flask pod to Kubernetes with the latest image from ECR.
