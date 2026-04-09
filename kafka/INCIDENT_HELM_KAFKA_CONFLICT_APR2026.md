# Incident: Kafka Connection Refused Due to Helm/Manifest Conflict (Apr 2026)

## The Error

```bash
nc: connect to kafka.kafka.svc.cluster.local (10.43.34.43) port 9092 (tcp) failed: Connection refused
```

The Airflow scheduler could not reach Kafka despite `kafka-0` being `1/1 Running`.

## How It Was Encountered and Identified

**Step 1 — connection test failed** (nc from Airflow scheduler to Kafka):
```bash
kubectl exec -n airflow-my-namespace <scheduler-pod> -- nc -zv kafka.kafka.svc.cluster.local 9092
# Connection refused
```

**Step 2 — checked pod state:**
```bash
kubectl get pods -n kafka -o wide
# NAME                 READY   STATUS
# kafka-0              1/1     Running          ← healthy
# kafka-controller-0   0/1     Init:ImagePullBackOff
# kafka-controller-1   0/1     Pending
# kafka-controller-2   0/1     Init:ImagePullBackOff
```

Two Kafka deployments were running: `kafka-0` (from `kafka/k8s/kafka.yaml`) and
`kafka-controller-*` (from a Helm chart release).

**Step 3 — checked service endpoints:**
```bash
kubectl get endpoints kafka -n kafka
# NAME    ENDPOINTS   AGE
# kafka   <none>
```

No endpoints — the `kafka` Service had zero healthy backing pods.

**Step 4 — compared service selector vs pod labels:**

The Helm chart had overridden the `kafka` Service with a selector requiring 4 labels:
```
app: kafka
app.kubernetes.io/instance: kafka
app.kubernetes.io/name: kafka
app.kubernetes.io/part-of: kafka
```

`kafka-0` only carries `app: kafka` (set by `kafka/k8s/kafka.yaml`). It did not match.
The `kafka-controller-*` pods had all 4 labels but were all broken — so the service had
no valid endpoints and every connection was refused.

## The Fix

```bash
# 1. Remove the Helm Kafka release (deletes kafka-controller-* pods and the bad Service)
helm uninstall kafka -n kafka

# 2. Re-apply the manifest to recreate the Service with the correct selector
kubectl apply -f ~/kafka/k8s/kafka.yaml
```

Verified with:
```bash
kubectl get endpoints kafka -n kafka
# NAME    ENDPOINTS          AGE
# kafka   10.42.0.216:9092   22s   ← kafka-0 is now the endpoint

kubectl exec -n airflow-my-namespace <scheduler-pod> -- nc -zv kafka.kafka.svc.cluster.local 9092
# Connection to kafka.kafka.svc.cluster.local 9092 port [tcp/*] succeeded!
```

Node CPU also dropped from **98% → 48%** — the 3 broken Helm pods had been holding ~1000m
in CPU requests while doing nothing.

## Why/How the Fix Works

Kubernetes Services route traffic using **label selectors**: the Service watches for pods
whose labels match its selector and registers them as endpoints. If no pods match, no
traffic can flow — regardless of whether a pod on the same IP/port is reachable directly.

The Helm chart (`kafka-32.4.3`) created its own `kafka` Service with a 4-label selector
targeting its own `kafka-controller-*` pods. When `kubectl apply -f kafka/k8s/kafka.yaml`
was later run, it updated the Service annotation (`last-applied-configuration`) but Helm
retained ownership (`meta.helm.sh/release-name: kafka`) and kept its selector in place.
The result was a Service that only Helm's broken pods could match — `kafka-0` was invisible
to it.

Uninstalling the Helm release ceded ownership of the `kafka` Service and deleted the
broken pods. Re-applying the manifest recreated the Service with the simple
`app: kafka` selector, which `kafka-0` satisfies — restoring endpoints immediately.

**Root cause summary**: mixing `helm install` and `kubectl apply` for the same resource
name in the same namespace. Helm owns resources it creates; a later `kubectl apply` updates
the annotation but does not transfer ownership. Always use one management method per resource.
