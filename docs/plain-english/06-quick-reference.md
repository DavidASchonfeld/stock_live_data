# Part 6: Quick Reference — Common Tasks

> Part of the [Plain English Guide](README.md)

---

### "I want to deploy my code changes"
```bash
# From your Mac, in the project directory:
./scripts/deploy.sh
```

### "I want to see if my DAGs are running"
```bash
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list
```

### "I want to see the Airflow UI"
```bash
# First, open the SSH tunnel (keep this terminal open):
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock

# Then in your browser:
# Airflow UI:   http://localhost:30080
# Dashboard:    http://localhost:32147/dashboard/
```

### "I want to check if pods are healthy"
```bash
ssh ec2-stock kubectl get pods --all-namespaces
```
Every pod should show `Running` and `1/1` READY. If any show `CrashLoopBackOff`, `Error`, or `ImagePullBackOff`, something is wrong.

### "I want to manually trigger my Stock pipeline"
```bash
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger Stock_Market_Pipeline
```

### "I want to check if data is getting into the database"
```bash
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- python3 -c "
from sqlalchemy import create_engine, text
import os
engine = create_engine(f'mysql+pymysql://{os.environ[\"DB_USER\"]}:{os.environ[\"DB_PASSWORD\"]}@{os.environ[\"DB_HOST\"]}/{os.environ[\"DB_NAME\"]}')
with engine.connect() as c:
    r = c.execute(text('SELECT COUNT(*) FROM company_financials')).scalar()
    print(f'company_financials: {r} rows')
    r = c.execute(text('SELECT COUNT(*) FROM weather_hourly')).scalar()
    print(f'weather_hourly: {r} rows')
"
```

### "SSH won't connect from a new location"
Your EC2 only allows SSH from one IP address (for security). When you're at a new location (different Wi-Fi), your IP changes. Go to AWS Console → EC2 → Security Groups → update the SSH rule with your new IP.

### "WARNING: connection is not using a post-quantum key exchange algorithm"

This warning appeared after upgrading to macOS with OpenSSH 10.2+. It meant the EC2 server was too old to support post-quantum key exchange algorithms.

**This is now resolved.** The EC2 was migrated to Ubuntu 24.04 LTS, which ships OpenSSH 9.6p1 and negotiates post-quantum algorithms automatically.

**Pending cleanup:** Remove the `KexAlgorithms -mlkem768x25519-sha256` line from `~/.ssh/config` under the `ec2-stock` host entry — it was only needed for the old Amazon Linux instance.
