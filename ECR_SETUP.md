# ECR Migration — Setup Complete

One-time checklist for the migration from `--docker` mode to K3S + AWS ECR. All steps completed.

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
