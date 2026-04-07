# Part 1: Where Does Your Code Live?

> Part of the [Plain English Guide](README.md)

Your code exists in **three places** at the same time. Think of it like making copies of a document:

## Place 1: Your Laptop (the original)

This is your Mac. When you open VS Code and edit `dag_stocks.py`, you're editing the file here:

```
Your Mac
  └── Documents/Programming/Python/Data-Pipeline-2026/data_pipeline/
        ├── airflow/dags/          ← Your pipeline code (dag_stocks.py, edgar_client.py, etc.)
        ├── airflow/manifests/     ← Kubernetes config files (YAML)
        ├── dashboard/             ← Flask website code
        └── scripts/deploy.sh     ← The script that copies everything to EC2
```

**This is the "source of truth."** If you want to change something, you change it here first, then deploy.

## Place 2: The EC2 Server (the copy on AWS)

Your EC2 instance is a computer rented from Amazon, running 24/7 in the cloud. When you run `deploy.sh`, it copies your files from your Mac to EC2:

```
EC2 (Amazon cloud server)
  └── /home/ubuntu/
        ├── airflow/dags/          ← Copy of your pipeline code
        ├── airflow/manifests/     ← Copy of your Kubernetes configs
        └── dashboard/             ← Copy of your Flask code
```

The EC2 server is like a remote desktop computer you can only access through the terminal. You connect to it via SSH:
```
ssh ec2-stock
```

**Think of SSH like making a phone call to the EC2 server.** Once connected, you can type commands that run on EC2 instead of on your Mac.

## Place 3: Inside the Pods (the copy inside the mini-computers)

This is the part that's confusing. Your EC2 server runs Kubernetes (K3S), which creates **pods**. A pod is like a tiny virtual computer running inside EC2. Your DAG files get copied one more time — from EC2's filesystem into the pods:

```
EC2 server
  └── /home/ubuntu/airflow/dags/dag_stocks.py    ← File on EC2's hard drive
        │
        │ (Kubernetes mounts this folder into the pod)
        ▼
  Airflow Pod (tiny virtual computer inside EC2)
    └── /opt/airflow/dags/dag_stocks.py             ← Same file, seen from inside the pod
```

**The file is not actually copied a third time.** Kubernetes uses a "mount" — it makes the EC2 folder visible inside the pod, like plugging in a USB drive. The pod sees the same files as EC2, just at a different path.

## The Full Journey of Your Code

```
1. You edit dag_stocks.py on your Mac
2. You run ./scripts/deploy.sh
3. deploy.sh copies the file to EC2  (rsync over SSH)
4. Kubernetes makes that file visible inside the Airflow pod  (mount)
5. Airflow reads the file and runs your pipeline
```
