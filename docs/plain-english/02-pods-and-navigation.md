# Part 2: What Are Pods and How Do You Navigate Them?

> Part of the [Plain English Guide](README.md)

## What is a Pod?

A pod is a tiny isolated computer running inside your EC2 server. Think of it like this:

- **EC2** = a physical office building
- **Pods** = individual offices inside the building
- Each office (pod) has its own stuff, its own "view" of files, and runs one specific program

Your project has these pods:

| Pod | What it does | Where it lives |
|-----|-------------|----------------|
| `airflow-scheduler-0` | Runs your DAG pipelines on schedule | `airflow-my-namespace` |
| `airflow-dag-processor-...` | Reads and parses your DAG files | `airflow-my-namespace` |
| `airflow-api-server-...` | Serves the Airflow web UI | `airflow-my-namespace` |
| `airflow-triggerer-0` | Handles delayed/deferred tasks | `airflow-my-namespace` |
| `airflow-postgresql-0` | Airflow's internal database (not your data) | `airflow-my-namespace` |
| `my-kuber-pod-flask` | Your Flask website/dashboard | `default` |

**MariaDB is NOT in a pod.** It runs directly on EC2 (outside Kubernetes). This is an important distinction — MariaDB is just a normal program running on the EC2 server.

## Namespaces: Which "Room" a Pod Is In

Pods are organized into **namespaces** — think of them as different floors of the office building:

- `airflow-my-namespace` = the floor where all Airflow pods live
- `default` = the floor where the Flask website pod lives

**Why this matters:** When you run a command to look at pods, you have to specify which floor you're looking on. If you look on the wrong floor, you won't see anything.

## How to Look at Pods

**From your Mac** (requires SSH tunnel running):
```bash
# See all pods on all floors
ssh ec2-stock kubectl get pods --all-namespaces

# See just the Airflow pods
ssh ec2-stock kubectl get pods -n airflow-my-namespace

# See just the Flask pod
ssh ec2-stock kubectl get pods -n default
```

**From inside EC2** (after running `ssh ec2-stock`):
```bash
kubectl get pods --all-namespaces
kubectl get pods -n airflow-my-namespace
kubectl get pods -n default
```

## How to Go "Inside" a Pod

Sometimes you need to look inside a pod — for example, to check if your DAG files are there, or to run an Airflow command. You use `kubectl exec`:

```bash
# From your Mac: run a command inside the Airflow scheduler pod
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- ls /opt/airflow/dags/
```

Let me break that command down piece by piece:

```
ssh ec2-stock                    ← "Call" the EC2 server
kubectl exec                     ← "Go inside a pod and run a command"
-n airflow-my-namespace          ← "On the Airflow floor"
airflow-scheduler-0              ← "Specifically, the scheduler pod"
--                               ← "Everything after this is the command to run inside"
ls /opt/airflow/dags/            ← "List the files in the DAGs folder"
```

**Think of it like a chain of phone calls:**
1. You call EC2 (`ssh ec2-stock`)
2. EC2 calls into the pod (`kubectl exec`)
3. The pod runs your command (`ls`)
4. The answer travels back through the chain to your Mac

## Where Am I Right Now?

This is the most confusing part. At any moment, your terminal could be running commands in one of three places:

| Where you are | How you got there | Your prompt looks like | How to leave |
|---------------|-------------------|----------------------|-------------|
| Your Mac | Default — you opened Terminal | `David@Davids-MacBook ~ %` | (you're already here) |
| EC2 server | Ran `ssh ec2-stock` | `[ubuntu@ip-... ~]$` | Type `exit` |
| Inside a pod | Ran `kubectl exec -it ... -- bash` | `airflow@airflow-scheduler-0:/$` | Type `exit` |

**The most common mistake:** Forgetting which "level" you're on. If you're inside EC2 and try to edit a local file, it won't work. If you're on your Mac and try to run `kubectl` without the SSH prefix, it won't work (unless you have an SSH tunnel running for the K8s API too).
