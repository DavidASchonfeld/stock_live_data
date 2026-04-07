# Troubleshooting Guide

**Quick Navigation**
- Looking for general debugging approach? See [DEBUGGING.md](DEBUGGING.md)
- Need command explanations? See [../reference/COMMANDS.md](../reference/COMMANDS.md)
- Want to understand Airflow or ETL? See [../architecture/SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md)
- Looking for term definitions? See [../reference/GLOSSARY.md](../reference/GLOSSARY.md)
- Failure mode catalog? See [../architecture/FAILURE_MODE_MAP.md](../architecture/FAILURE_MODE_MAP.md)
- Prevention checklists? See [PREVENTION_CHECKLIST.md](PREVENTION_CHECKLIST.md)

---

## Topic Files

| File | Covers |
|------|--------|
| [Airflow DAG Issues — Discovery](troubleshooting/airflow-dag-issues.md) | DagBag errors, parse failures, deprecation warnings, DAG not discoverable, Variable.get changes |
| [Airflow DAG Issues — Runtime](troubleshooting/airflow-dag-runtime-issues.md) | DAG disappearing after deploy, dynamic start_date, processor cache staleness, task failures, task state sync |
| [Kubernetes Pod Issues](troubleshooting/kubernetes-pod-issues.md) | Pod crashes, OOMKill, CrashLoopBackOff, CreateContainerConfigError, Helm upgrade stuck, service selector mismatch |
| [Deploy Issues](troubleshooting/deploy-issues.md) | deploy.sh warnings, DAG validation, DAG files not visible, changes not reflected in cluster |
| [Docker Build Issues](troubleshooting/docker-build-issues.md) | BuildKit/buildx missing, Docker build failures on EC2 |
| [System Issues](troubleshooting/system-issues.md) | apt freeze, SSH warnings, kubectl permissions, browser console errors, 404 bookmark URLs |

---

## Common Commands Reference

### Check Everything is Running

```bash
# Airflow pods
ssh ec2-stock kubectl get pods -n airflow-my-namespace

# Scheduler pod logs
ssh ec2-stock kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=50

# PersistentVolume status
ssh ec2-stock kubectl get pv,pvc -A | grep dag

# K3S cluster status
ssh ec2-stock kubectl cluster-info
ssh ec2-stock kubectl get nodes
```

### Manual DAG Trigger (if needed)

```bash
# Trigger specific DAG run from EC2
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger -e '2026-03-30' 'Stock_Market_Pipeline'"
```

### Check Database Tables

```bash
# From EC2 MariaDB
ssh ec2-stock "mariadb -u airflow_user -p'[PASSWORD]' -h <MARIADB_PRIVATE_IP> -e 'SHOW TABLES;'"

# From pod (if mariadb-client installed)
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  mariadb -u airflow_user -p'[PASSWORD]' -h <MARIADB_PRIVATE_IP> -e 'SHOW TABLES;'"
```

---

## Prevention Checklist

When making infrastructure changes:

- [ ] Update `deploy.sh` paths
- [ ] Update K8s manifests to match
- [ ] Test `deploy.sh` with dry-run or test branch first
- [ ] Verify files on EC2 after deploy
- [ ] Verify files in pod after pod restart
- [ ] Check Airflow logs for DAG parsing errors
- [ ] Monitor first DAG run for execution errors
