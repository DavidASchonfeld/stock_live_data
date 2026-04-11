# Incident: Ubuntu System Updates Pending on EC2 — 2026-04-10

## What Was Seen

On SSH login to the EC2 instance, Ubuntu displayed:

```
12 updates can be applied immediately.
To see these additional updates run: apt list --upgradable

1 additional security update can be applied with ESM Apps.
Learn more about enabling ESM Apps service at https://ubuntu.com/esm
```

## What Caused It

Ubuntu packages accumulate updates over time. The EC2 instance had not had `apt upgrade` run since it was provisioned or last updated, so 12 packages (plus 1 ESM security patch requiring Ubuntu Pro) had fallen behind their latest versions.

## How It Was Identified

The Ubuntu login banner automatically reports pending updates on every SSH login. No external monitoring or alerting was needed — the message appeared immediately upon connecting.

## How It Was Fixed

```bash
sudo apt update      # refreshed the package index from upstream mirrors
sudo apt upgrade -y  # applied all 12 pending updates non-interactively
```

For the ESM security update (requires Ubuntu Pro):
```bash
sudo pro attach <token>   # attach Ubuntu Pro (free for up to 5 personal machines)
sudo apt upgrade -y       # applies the additional ESM patch
```

## Why This Fix Was Chosen

`apt update && apt upgrade` is the standard, safe, non-destructive way to apply pending Debian/Ubuntu package updates. It does not remove packages or change configuration files without prompting. It is idempotent and reversible via `apt-mark hold` if a specific package needs to be pinned.

## How the Fix Solved the Problem

Refreshing the package index (`apt update`) synchronizes the local cache with upstream mirrors so `apt` knows which newer versions are available. Running `apt upgrade` then downloads and installs those newer package versions, replacing the outdated ones. After the upgrade, the login banner no longer reports pending updates because all installed packages match their latest available versions in the index.

## Docker Daemon Restart Prompt

During `apt upgrade`, a debconf dialog appeared for the `docker.io` package:

```
Automatically restart Docker daemon?
                  <Yes>                <No>
```

**Choice: Yes.**

- k3s uses **containerd**, not the Docker daemon — restarting Docker does not affect running Kubernetes pods or services.
- Selecting **No** would leave the Docker daemon running old binaries against new libraries, causing potential breakage when starting future containers.
- Selecting **Yes** restarts the daemon cleanly on the upgraded version immediately.

Any Docker containers with a restart policy (`always` / `unless-stopped`) were automatically brought back up after the daemon restarted.

## Follow-Up

- If a kernel package was among the updates, a reboot is required: `sudo reboot`
- Confirm with `uname -r` post-reboot that the new kernel is active
- Consider scheduling periodic unattended upgrades: `sudo apt install unattended-upgrades`
