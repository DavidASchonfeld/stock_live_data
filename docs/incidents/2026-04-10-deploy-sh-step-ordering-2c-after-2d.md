# Incident: deploy.sh — Step 2c/2c1 ran after Step 2d (wrong order)

**Date:** 2026-04-10
**Severity:** Medium — deploy succeeded but Helm hook timed out every run; pods could start with stale/missing secrets

---

## What caused it

Steps 2c (sync K8s manifests to EC2) and 2c1 (apply K8s secrets) were placed **after** Step 2d (Helm upgrade) in `scripts/deploy.sh`. The Snowflake credential secret referenced by pods via `envFrom` wasn't applied to the cluster before Helm triggered pod restarts. Pods failed to become healthy within the hook window → post-upgrade hook timed out.

The misplacement was likely introduced when Step 2d was added: it was inserted right after Step 2c2 without noticing that Step 2c and 2c1 were further down the file. The comment on line 357 even read *"These secrets must exist before Helm upgrade (Step 2d)"* — the intent was documented, but the code contradicted it.

---

## How it was encountered and identified

The symptom in the terminal output was:

```
=== Step 2d: Applying Helm values to live Airflow release ===
Error: UPGRADE FAILED: post-upgrade hooks failed: 1 error occurred:
	* timed out waiting for the condition
```

Then, immediately after, Step 2c appeared — revealing that manifests and secrets hadn't been synced yet when Helm ran. Grepping for step headers in the script confirmed the ordering: `Step 2d` (line 312) came before `Step 2c` (line 349) and `Step 2c1` (line 355).

---

## How it was fixed

Moved the Step 2c and Step 2c1 blocks to **before** Step 2c2 and Step 2d in `scripts/deploy.sh`. No logic was changed — only the execution order.

New order: `2c → 2c1 → 2c2 → 2d`

Updated the Step 2c1 comment from "must exist before Helm upgrade" to explicitly say "Must run before Step 2d" to make the dependency obvious inline.

---

## Why this fix works

Helm upgrade triggers pod restarts. Pods use `envFrom` to load Snowflake credentials from a Kubernetes Secret. If that Secret doesn't exist yet when the pod starts, Kubernetes marks the pod as `Failed` and the post-upgrade hook times out waiting for readiness. By applying the Secret (Step 2c1) before the Helm upgrade (Step 2d), the Secret is already present when pods restart, so they start cleanly and the hook completes within its timeout window.

---

## How to efficiently report similar errors in the future

When re-running the script and hitting an error, **only paste the failing step's output** — from the `=== Step X ===` header line through the error message and any immediately following lines. The full log is not needed. Example: paste from `=== Step 2d ===` through the timeout line only.
