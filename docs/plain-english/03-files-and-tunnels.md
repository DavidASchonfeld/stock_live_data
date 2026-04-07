# Part 3: How Files Get Into Pods + SSH Tunnels

> Part of the [Plain English Guide](README.md)

---

## The Mount System (How Pods See Files)

Pods don't have their own hard drive. They "borrow" folders from EC2 using **mounts**. Here's how it works for your DAG files:

```
Step 1: You created a PersistentVolume (PV) — a Kubernetes object that says:
        "There is a folder on EC2 at /home/ubuntu/airflow/dags/"

Step 2: You created a PersistentVolumeClaim (PVC) — a Kubernetes object that says:
        "I need some storage, and I want to use the PV from Step 1"

Step 3: The Airflow pods say (in their config):
        "Mount the PVC at /opt/airflow/dags/ inside me"

Result: When the pod looks at /opt/airflow/dags/, it actually sees
        the files at /home/ubuntu/airflow/dags/ on EC2.
```

**Analogy:** A PV is like saying "there's a filing cabinet in room 204." A PVC is like saying "I need access to that filing cabinet." The mount is like putting a door from the pod directly to that filing cabinet.

## What deploy.sh Actually Does

When you run `./scripts/deploy.sh`, here's what happens in plain English:

1. **Checks your code for typos** — runs Python syntax checker on all DAG files
2. **Copies DAG files to EC2** — uses `rsync` (a smart copy tool that only sends files that changed)
3. **Renders and copies the Flask pod manifest** — `pod-flask.yaml` in git contains `${ECR_REGISTRY}` as a placeholder (so your AWS account ID is never committed). Before sending the file to EC2, the script swaps that placeholder for your real ECR URL. It uses `envsubst` to do this substitution, with a fallback to `sed` if `envsubst` isn't on the PATH.
4. **Copies Flask website code to EC2** — same rsync
5. **Builds a new Docker image for Flask on EC2** — packages your website into a container
6. **Pushes that image to AWS ECR** — ECR is like a storage locker for Docker images; requires an IAM role (see box below)
7. **Restarts Airflow pods** — forces them to see the new DAG files (prevents the stale cache bug)
8. **Restarts the Flask pod** — picks up the new website image
9. **Verifies everything is running** — checks pod statuses

> **IAM role — what it is and why deploy.sh needs it**
>
> An **IAM role** is a permission badge that tells AWS "this EC2 instance is allowed to do X." In this project, the role gives EC2 permission to push and pull Docker images from ECR (the image storage).
>
> Step 6 above works by asking AWS for a temporary 12-hour password (`aws ecr get-login-password`). AWS only hands that out if the EC2 instance has the right IAM role attached.
>
> **The catch with AMIs:** When you create a new instance from an AMI (a disk snapshot), AWS copies the entire disk but does **not** copy the IAM role. You must manually re-attach the IAM role each time.
>
> If you forget, deploy.sh fails at Step 6 with: `Unable to locate credentials`.
>
> **Fix:** EC2 Console → select instance → **Actions → Security → Modify IAM role** → attach the role.

---

## The SSH Tunnel — How You Access Things in Your Browser

Your EC2 server has the Airflow web UI and your Flask dashboard running, but they're not open to the internet (for security). To access them, you use an **SSH tunnel**.

**What is an SSH tunnel?**

Think of it like a secret passageway. Normally, your browser can't reach EC2's internal ports. But an SSH tunnel creates a connection that lets your Mac pretend those ports are local:

```bash
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
```

This command says:
- "When I go to `localhost:30080` on my Mac, secretly forward that to EC2's port 30080"
- "When I go to `localhost:32147` on my Mac, secretly forward that to EC2's port 32147"

**After running this command:**
- `http://localhost:30080` in your browser → Airflow UI
- `http://localhost:32147/dashboard/` in your browser → Your Flask dashboard

**If you close this terminal window, the tunnel dies and those URLs stop working.** Keep the terminal open.

**If you also need to run `kubectl` commands from your Mac**, use the extended tunnel that adds the Kubernetes API port:

```bash
ssh -N -L 6443:localhost:6443 -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
```

- Port `6443` is the **Kubernetes API server** — it's what `kubectl` talks to behind the scenes.
- The `-N` flag means "don't open a shell, just hold the tunnel open."
