# Failure Mode: Airflow Scheduler Container Not Ready for Exec After Pod Restart

## Summary

After a pod restart, `kubectl exec` into the `airflow-scheduler-0` scheduler container fails with:

```
error: Internal error occurred: unable to upgrade connection: container not found ("scheduler")
```

This caused two deploy warnings:
- `WARNING: Airflow DAG verification or variable setup failed.`
- `WARNING: ml-venv setup failed. anomaly_detector.py will not run until this is resolved.`

And ultimately a `DEPLOY FAILED (exit code: 1)`.

## Root Cause

`kubectl wait --for=condition=Ready` checks the pod's `Ready` condition in the Kubernetes API. However, this condition becoming `true` does not mean the container runtime has fully registered the container as exec-able.

There is a short race window (a few seconds) where:
1. The pod reports `Ready = True`
2. `kubectl exec` still fails with `container not found`

This is a Kubernetes timing issue. The kubelet sets the `Ready` condition when startup probes pass and readiness probes succeed, but the exec channel to the container may not be open yet at that exact moment.

Both Phase C of `step_restart_airflow_pods()` and `step_setup_ml_venv()` in `scripts/deploy/airflow_pods.sh` ran `kubectl exec` immediately after the `kubectl wait`, hitting this race every time.

## Fix

Added **Phase B.5** in `step_restart_airflow_pods()` inside `scripts/deploy/airflow_pods.sh`.

After all three pods are confirmed `Ready`, the deploy script now polls `kubectl exec airflow-scheduler-0 -- /bin/true` in a loop (up to 30 attempts, 2 seconds apart = 60s max) before proceeding to Phase C or `step_setup_ml_venv()`.

In practice the exec succeeds on attempt 1-3 (2-6 seconds after `Ready`).

## Impact

- `anomaly_detector.py` would silently fail to run because `/opt/ml-venv` did not exist
- Airflow variables (`KAFKA_BOOTSTRAP_SERVERS`) were not set, which could cause DAG failures
- Every deploy would end with `DEPLOY FAILED` despite the cluster being healthy

## Date

2026-04-11
