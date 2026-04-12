# Airflow Scheduler Pod Ready Timeout

Back to [Failure Mode Index](../FAILURE_MODE_MAP.md)

---

### AF-9: kubectl wait Timeout — airflow-scheduler-0 Ready (Apr 11 2026)

| Field | Detail |
|-------|--------|
| **Symptom** | `timed out waiting for the condition on pods/airflow-scheduler-0` — deploy fails at Step 7 with exit code 1 after ~15 min |
| **Cause** | `kubectl wait --timeout=360s` (6 min) is shorter than the scheduler's actual maximum startup window. The startup probe is configured with `failureThreshold: 10 / periodSeconds: 30 / timeoutSeconds: 45`. Because `timeoutSeconds` (45s) exceeds `periodSeconds` (30s), Kubernetes waits the full 45s for each failed probe before retrying — so the real ceiling is 10 × 45s = 450s (7.5 min). The original comment incorrectly calculated this as "10 × 30s = 5 min", hiding the mismatch. Any deploy where the scheduler takes between 360s and 450s to pass its startup probes will fail the `kubectl wait` even though the pod is healthy and would become Ready on its own. |
| **Fix** | Increased all three parallel `kubectl wait` timeouts in `scripts/deploy/airflow_pods.sh` from `--timeout=360s` → `--timeout=600s`. 600s gives ~2.5 min buffer above the 450s probe ceiling and matches the `helm upgrade --timeout 10m` already in the same script. Fixed the inaccurate comment in `values.yaml` line 138 to reflect the correct 7.5 min window. |
| **Rule** | `kubectl wait --timeout` must be set to at least `failureThreshold × max(periodSeconds, timeoutSeconds)` plus buffer. When `timeoutSeconds > periodSeconds` in a startup probe, `timeoutSeconds` dominates — use it in the calculation, not `periodSeconds`. |
| **Real incident?** | Yes — Apr 11 2026. |

---

### AF-10: kubectl wait Timeout — startupProbe ceiling collision (Apr 11 2026)

| Field | Detail |
|-------|--------|
| **Symptom** | Deploy fails again with same error after the AF-9 fix: `timed out waiting for the condition on pods/airflow-scheduler-0` after ~18 min. Increasing wait 360s→600s did not help. |
| **Cause** | `startupProbe.timeoutSeconds: 45` was exactly equal to the observed maximum response time for `airflow jobs check` on the t3.large node. With Kafka + MLflow + dashboard also running, every probe attempt hit the 45s ceiling and was counted as a failure. After 10 failures × 45s = 450s Kubernetes killed the container. The pod entered CrashLoopBackOff (10s backoff, restart at ~460s). The 600s kubectl wait expired 140s into the second startup cycle — pod never became Ready. Raising the wait timeout alone can never fix this; the probe itself must be given more headroom. |
| **Fix** | 1. Raised `startupProbe.timeoutSeconds` 45s→60s (15s buffer above ceiling). 2. Raised `startupProbe.failureThreshold` 10→15 (new max window: 15×60s=900s). 3. Raised `scheduler.livenessProbe.timeoutSeconds` 45s→60s (same reason). 4. Raised kubectl wait 600s→1000s (900s ceiling + 100s buffer). 5. Added `kubectl describe pod` + `kubectl logs` auto-print if the wait fails, so future failures are self-diagnosing. |
| **Rule** | `startupProbe.timeoutSeconds` must be strictly greater than the observed worst-case response time, not equal to it. Add at least 15s buffer. Also: kubectl wait timeout must exceed `failureThreshold × timeoutSeconds + CrashLoopBackOff_backoff (10s)` to survive one full restart cycle. |
| **Real incident?** | Yes — Apr 11 2026. |
