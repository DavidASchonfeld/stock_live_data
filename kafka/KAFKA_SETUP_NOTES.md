# Kafka Setup Notes

## Cost

**Apache Kafka is free.** It is open-source software (Apache License 2.0).
You are running it inside K3s on your existing EC2 instance — no new AWS service,
no new bill. The only cost is the EC2 compute you were already paying for.

---

## What Went Wrong (and How It Was Fixed)

### Error 1 — Exec probe timed out every cycle (K3s hairpin)

**What the error looked like:**
`kafka-0` stayed `Ready: False` indefinitely. The readiness probe timed out on every attempt.

**What was causing it:**
The probe ran `kafka-broker-api-versions.sh --bootstrap-server localhost:9092` inside the
pod. Kafka replied with a metadata response that said "reconnect to me at
`kafka.kafka.svc.cluster.local:9092`" (the ClusterIP address). The client then tried to
reconnect through that ClusterIP address — which sent traffic through K3s's network layer
back to the same pod. K3s drops or delays this "hairpin" traffic (pod → ClusterIP → same pod),
so the reconnect never completed and the probe timed out every time.

**How it was fixed:**
Changed `KAFKA_ADVERTISED_LISTENERS` from the ClusterIP hostname
(`kafka.kafka.svc.cluster.local:9092`) to the headless pod DNS name
(`kafka-0.kafka-headless.kafka.svc.cluster.local:9092`). The headless address
resolves directly to the pod's IP, so there is no traffic hairpin through the ClusterIP.

---

### Error 2 — Exec probe failed with NXDOMAIN (headless DNS chicken-and-egg)

**What the error looked like:**
After fixing the advertised listener, the exec probe still failed immediately with a DNS
lookup error instead of a timeout.

**What was causing it:**
In Kubernetes, the headless DNS entry for a pod (`kafka-0.kafka-headless...`) only exists
while that pod is in `Ready` state. The exec probe (AdminClient) bootstrapped to
`localhost:9092`, got a metadata response advertising the headless hostname, and then tried
to reconnect to that hostname — but the pod wasn't Ready yet, so the DNS entry didn't exist
yet. DNS returned NXDOMAIN, the reconnect failed, the probe failed, the pod never became
Ready, and the DNS entry was never created. A perfect deadlock.

**How it was fixed:**
Replaced the exec probe (AdminClient) with a `tcpSocket` probe. The kubelet checks
readiness by opening a direct TCP connection to the pod's IP on port 9092. It never
does a DNS lookup. It never spawns a JVM or an AdminClient. Port 9092 only accepts
connections after Kafka has fully initialized, so a successful TCP connect is a genuine
signal that the broker is ready. The chicken-and-egg is broken entirely.

---

### Error 3 — StatefulSet rolling-update deadlock (`kubectl rollout status` timeout)

**What the error looked like:**
```
error: timed out waiting for the condition
WARNING: Kafka rollout did not complete — skipping topic creation.
```

**What was causing it:**
After `kubectl apply` updated the StatefulSet spec (to use the tcpSocket probe), the
running pod was never replaced. Kubernetes StatefulSet rolling updates work like this:
replace the old pod only after it becomes Ready. But the old pod was still running the
old exec probe (which always failed), so it was permanently `Not Ready`, so the controller
refused to replace it, so it could never pick up the new probe config. The pod had been
alive for 9+ hours still running the original probe. `kubectl rollout status` was waiting
for a rollout that was permanently stalled.

**How it was fixed (two parts):**

*Immediate fix (one-time):*
Manually deleted the stuck pod with `kubectl delete pod kafka-0 -n kafka`. When a
StatefulSet pod is deleted, the controller immediately recreates it from the current
desired spec. The new pod started with the correct tcpSocket probe, became Ready, and
the rollout completed.

*Permanent fix (in `deploy.sh`):*
Added a deadlock guard that runs after every `kubectl apply`. It compares the StatefulSet's
`currentRevision` (what pods are actually running) with its `updateRevision` (what the spec
says they should be running). If they differ AND `kafka-0` is Not Ready, the guard
gracefully deletes the pod before waiting for rollout. This means future spec changes
(probe tweaks, env var changes, resource limit bumps) will never silently stall again.

---

## Verifying Kafka Is Working

Run all of these from your Mac. They SSH into EC2 and run kubectl/kafka commands inside
the cluster.

> **Note on SSH syntax:** The commands below were originally written as `ssh ec2-user@52.70.211.1`.
> That form is wrong for two reasons: (1) the EC2 username is `ubuntu`, not `ec2-user`, and
> (2) writing `alias@IP` bypasses `~/.ssh/config` entirely, so no identity file is loaded and
> auth fails with "Permission denied (publickey)". The corrected form is `ssh ec2-stock`
> (the alias only), which resolves `User ubuntu`, `HostName 52.70.211.1`, and the `.pem` key
> automatically from `~/.ssh/config`.

### Step 1 — Pod is healthy

```bash
ssh ec2-user@52.70.211.1 "kubectl get pod kafka-0 -n kafka"
# Expected: STATUS=Running, READY=1/1
```

**Corrected command:**
```bash
# Use the SSH alias — supplies User=ubuntu and the .pem key from ~/.ssh/config
ssh ec2-stock "kubectl get pod kafka-0 -n kafka"
# Expected: STATUS=Running, READY=1/1
```

### Step 2 — Broker is accepting connections

```bash
ssh ec2-user@52.70.211.1 "
    kubectl exec kafka-0 -n kafka -- \
        /opt/kafka/bin/kafka-broker-api-versions.sh \
        --bootstrap-server localhost:9092 2>&1 | head -5
"
# Expected: a list of supported Kafka API versions (no timeout, no error)
```

**Corrected command:**
```bash
# Use the SSH alias — supplies User=ubuntu and the .pem key from ~/.ssh/config
ssh ec2-stock "
    kubectl exec kafka-0 -n kafka -- \
        /opt/kafka/bin/kafka-broker-api-versions.sh \
        --bootstrap-server localhost:9092 2>&1 | head -5
"
# Expected: a list of supported Kafka API versions (no timeout, no error)
Example:

David@MacBookPro data_pipeline % ssh ec2-stock "
    kubectl exec kafka-0 -n kafka -- \
        /opt/kafka/bin/kafka-broker-api-versions.sh \
        --bootstrap-server localhost:9092 2>&1 | head -5
"
kafka-0.kafka-headless.kafka.svc.cluster.local:9092 (id: 1 rack: null isFenced: false) -> (
	Produce(0): 0 to 12 [usable: 12],
	Fetch(1): 4 to 17 [usable: 17],
	ListOffsets(2): 1 to 10 [usable: 10],
	Metadata(3): 0 to 13 [usable: 13],
```

### Step 3 — Topics exist

```bash
ssh ec2-user@52.70.211.1 "
    kubectl exec kafka-0 -n kafka -- \
        /opt/kafka/bin/kafka-topics.sh \
        --list --bootstrap-server localhost:9092
"
# Expected:
# stocks.financials.raw
# weather.hourly.raw
```

**Corrected command:**
```bash
# Use the SSH alias — supplies User=ubuntu and the .pem key from ~/.ssh/config
ssh ec2-stock "
    kubectl exec kafka-0 -n kafka -- \
        /opt/kafka/bin/kafka-topics.sh \
        --list --bootstrap-server localhost:9092
"
# Expected:
# stocks-financials-raw
# weather-hourly-raw

Example:
David@MacBookPro data_pipeline % ssh ec2-stock "
    kubectl exec kafka-0 -n kafka -- \
        /opt/kafka/bin/kafka-topics.sh \
        --list --bootstrap-server localhost:9092
"
stocks-financials-raw
weather-hourly-raw
```

### Step 4 — Produce and consume a test message (end-to-end)

Run these in two separate terminal tabs.

**Tab 1 — start a consumer (leave it running):**
```bash
ssh ec2-user@52.70.211.1 "
    kubectl exec kafka-0 -n kafka -- \
        /opt/kafka/bin/kafka-console-consumer.sh \
        --bootstrap-server localhost:9092 \
        --topic weather-hourly-raw \
        --from-beginning
"
```

**Corrected Tab 1:**
```bash
# Use the SSH alias — supplies User=ubuntu and the .pem key from ~/.ssh/config
ssh ec2-stock "
    kubectl exec kafka-0 -n kafka -- \
        /opt/kafka/bin/kafka-console-consumer.sh \
        --bootstrap-server localhost:9092 \
        --topic weather-hourly-raw \
        --from-beginning
"
```

**Tab 2 — produce one test message:**
```bash
ssh ec2-user@52.70.211.1 "
    echo 'hello-kafka' | kubectl exec -i kafka-0 -n kafka -- \
        /opt/kafka/bin/kafka-console-producer.sh \
        --bootstrap-server localhost:9092 \
        --topic weather-hourly-raw
"
```

**Corrected Tab 2:**
```bash
# Use the SSH alias — supplies User=ubuntu and the .pem key from ~/.ssh/config
ssh ec2-stock "
    echo 'hello-kafka' | kubectl exec -i kafka-0 -n kafka -- \
        /opt/kafka/bin/kafka-console-producer.sh \
        --bootstrap-server localhost:9092 \
        --topic weather-hourly-raw
"
```

Tab 1 should print `hello-kafka` within a few seconds of running Tab 2. If it does, Kafka is 100% working end-to-end.
Press Ctrl-C in Tab 1 to stop the consumer.

**Q: The whole test took ~15 seconds — is that normal?**
Yes. The delay is startup overhead, not Kafka latency. Two costs stack:
1. **JVM startup** — both `kafka-console-producer.sh` and `kafka-console-consumer.sh` spawn a full JVM. On a resource-constrained EC2 instance this takes 5–10 seconds.
2. **Consumer group init** — the consumer must connect, join a consumer group, negotiate partition assignments, and fetch initial offsets before it can print anything (another 3–5 seconds).
Once the consumer is up, actual message delivery is milliseconds. The 15 seconds is pure startup cost.

### Step 5 — Airflow can reach Kafka (cross-namespace connectivity)

```bash
ssh ec2-user@52.70.211.1 "
    kubectl exec -n airflow-my-namespace \
        \$(kubectl get pod -n airflow-my-namespace -l component=scheduler -o jsonpath='{.items[0].metadata.name}') \
        -- nc -zv kafka.kafka.svc.cluster.local 9092
"
# Expected: Connection to kafka.kafka.svc.cluster.local 9092 port [tcp/*] succeeded!
```

**Corrected command:**
```bash
# Use the SSH alias — supplies User=ubuntu and the .pem key from ~/.ssh/config
ssh ec2-stock "
    kubectl exec -n airflow-my-namespace \
        \$(kubectl get pod -n airflow-my-namespace -l component=scheduler -o jsonpath='{.items[0].metadata.name}') \
        -- nc -zv kafka.kafka.svc.cluster.local 9092
"
# Expected: Connection to kafka.kafka.svc.cluster.local 9092 port [tcp/*] succeeded!
```

This confirms Airflow pods can reach Kafka across namespaces before you wire up any DAGs.
