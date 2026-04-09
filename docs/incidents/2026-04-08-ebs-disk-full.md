# EBS Disk Full Incident — April 8, 2026

## What Went Wrong

While deploying Phase 2 (dbt), the EC2 instance ran out of disk space. The root volume was 20 GiB and had crept up to ~88% used (about 17 GiB). At that point, Kubernetes' internal container runtime (K3S) automatically deleted the custom Docker image we had just built — `airflow-dbt:3.1.8-dbt` — to reclaim space.

With the image gone, all Airflow pods (scheduler, triggerer, dag-processor) got stuck in `ErrImageNeverPull`. This means: "I was told not to pull images from the internet (`imagePullPolicy: Never`), and the image I need isn't stored locally — so I can't start." The Airflow UI was unreachable and no DAGs could run.

---

## Root Cause

Two things combined to fill the disk:

**1. K3S stores images on disk, and they're large**

K3S (the lightweight Kubernetes distribution running on this EC2) stores every Docker image it uses in `/var/lib/rancher`. The custom Airflow+dbt image is 2.3 GiB on its own. Combined with the base Airflow image and other K3S state, `/var/lib/rancher` was using ~8.8 GiB.

**2. K3S automatically garbage-collects images when disk is low**

K3S watches disk usage and starts deleting unused images when usage crosses ~85%. At 88%, it silently deleted our `airflow-dbt:3.1.8-dbt` image. No warning, no error — the image just disappeared. The next time Kubernetes tried to start an Airflow pod, the image was gone.

The sequence that led here:
1. Built the `airflow-dbt:3.1.8-dbt` image (~2.3 GiB) — disk hit ~95%+
2. Ran `docker system prune -af` to free build cache — freed ~900 MB, disk dropped to ~88%
3. K3S GC kicked in and deleted the image to get below the threshold
4. All Airflow pods enter `ErrImageNeverPull`

---

## How It Was Fixed

**Step 1 — Resize the EBS volume in AWS Console (no downtime)**

EBS volumes (the virtual disks attached to EC2 instances) can be resized while the instance is running. No reboot, no data loss.

1. AWS Console → EC2 → Elastic Block Store → Volumes
2. Select the volume attached to the pipeline instance (`vol-0a0d93452fe85eeee`, 20 GiB, us-east-1b)
3. Actions → **Modify Volume** → change `20` to `25` → click **Modify**
4. Wait ~1 minute for the modification to show as "completed"

AWS has expanded the raw disk, but the operating system doesn't know yet — it still thinks the disk is 20 GiB.

**Step 2 — Tell the OS about the extra space**

SSH into EC2 and run:

```bash
sudo growpart /dev/nvme0n1 1    # expand the partition to fill the new disk size
sudo resize2fs /dev/nvme0n1p1   # expand the filesystem to fill the partition
df -h /                          # verify
```

Output after the fix:
```
Filesystem      Size  Used Avail Use% Mounted on
/dev/root        24G   12G   12G  52% /
```

12 GiB free — well below the K3S GC threshold.

---

## Why 25 GiB and Not More

We could have gone to 30 GiB, but 25 GiB is the right size to save money:

| Volume Size | Monthly Cost | Free Space After Fix |
|-------------|-------------|----------------------|
| 20 GiB (old) | $1.60 | ~3 GiB — too tight |
| **25 GiB (chosen)** | **$2.00** | **~12 GiB — comfortable** |
| 30 GiB | $2.40 | ~17 GiB — more than needed |

gp3 EBS costs $0.08/GiB/month. 25 vs 30 GiB saves $0.40/month — small, but the user's goal is to keep costs low.

Steady-state disk after all cleanup (removing Docker copy of image, eventually uninstalling MariaDB) will be ~14 GiB used on a 25 GiB disk = 56% — K3S won't GC anything at that level.

**Important:** EBS volumes can only grow, never shrink. If Kafka (Phase 3) unexpectedly needs more disk, we can expand again.

---

## Verification

After the fix, confirmed disk had room:

```bash
df -h /
# Filesystem      Size  Used Avail Use% Mounted on
# /dev/root        24G   12G   12G  52% /
```

Next step after this fix: rebuild and re-import the `airflow-dbt:3.1.8-dbt` image (see Phase 2 deploy steps).

---

## Lessons Learned

1. **Check disk before large image builds.** A 2.3 GiB image on a 20 GiB disk will consume 11% of total capacity. Run `df -h /` before any `docker build` + `k3s ctr images import` sequence.

2. **K3S silently GCs images at ~85% disk.** There's no warning. The image just disappears. If pods suddenly show `ErrImageNeverPull` after a disk-heavy operation, GC is the first thing to suspect.

3. **EBS resize is live and safe.** No reboot, no downtime, no data loss. `growpart` + `resize2fs` take under 10 seconds. This is a routine maintenance operation, not a scary one.

4. **`docker system prune -af` frees Docker cache, not K3S images.** After pruning Docker, run `sudo k3s ctr images list` to verify the image still exists in K3S. Docker and K3S maintain separate image stores.

---

## Quick Reference: Disk Commands

```bash
# Check overall disk usage
df -h /

# See what's eating the most disk
du -sh /var/lib/rancher     # K3S images
du -sh /var/lib/docker      # Docker images/cache

# List images K3S has stored
sudo k3s ctr images list

# List Docker images
docker images

# Free Docker build cache (safe to run anytime)
docker system prune -af
```

---

**Date:** 2026-04-08
**Affected component:** EC2 root EBS volume
**Resolution time:** ~5 minutes (AWS volume resize + OS partition expansion)
**Data lost:** None
