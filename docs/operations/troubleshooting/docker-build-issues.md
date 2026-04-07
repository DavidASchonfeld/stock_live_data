# Docker Build Issues

Troubleshooting Docker build failures, BuildKit, and ECR push problems during deployment.

**See also:** [Deploy Issues](deploy-issues.md) | [Parent index](../TROUBLESHOOTING.md) | [DEBUGGING.md](../DEBUGGING.md)

---

## Issue: `BuildKit is enabled but the buildx component is missing or broken`

### Symptoms
`deploy.sh` Step 4 fails during the Docker build on EC2 with:
```
ERROR: BuildKit is enabled but the buildx component is missing or broken.
       Install the buildx component to build images with BuildKit:
       https://docs.docker.com/go/buildx/
```

### What is BuildKit?
BuildKit is Docker's modern build engine, introduced as opt-in in Docker 18.09 and made
the **default in Docker 23+**. It replaces the legacy "classic" builder with a faster,
more parallel build graph, better layer caching, and new Dockerfile syntax features
(e.g. `--mount=type=cache` to cache pip/apt downloads between builds).

### What is `docker-buildx-plugin`?
`docker-buildx-plugin` is the apt package that installs the `buildx` binary — the CLI
frontend that BuildKit requires. When Docker runs a build with BuildKit enabled, it calls
`buildx` internally even if you use the classic `docker build` syntax. Without the
plugin, Docker has no way to invoke its own build engine and aborts with the above error.

### Root Cause
`deploy.sh` sets `DOCKER_BUILDKIT=1` explicitly, and Docker 23+ also enables BuildKit in
`daemon.json` by default. Either trigger requires `buildx`. On a fresh or recently
upgraded Ubuntu instance, `docker-buildx-plugin` is a separate apt package that isn't
always installed automatically alongside `docker.io` or `docker-ce`.

### Why not just remove `DOCKER_BUILDKIT=1`?
That would suppress our explicit opt-in, but if the Docker daemon has BuildKit on by
default (the case on Docker 23+), the build would still fail. Removing the env var is a
workaround that masks the real missing dependency rather than satisfying it.

### Why BuildKit matters for this project's future
As the pipeline grows to include Snowflake loaders, dbt runners, and Kafka consumers, each
will likely have its own container image. BuildKit features that become valuable then:
- **`--mount=type=cache`** — caches `pip install` and `apt-get` layers across rebuilds,
  cutting build times from ~2 min to ~10 s for unchanged dependencies
- **`--platform`** — builds multi-architecture images if you ever switch instance types
- **Parallel build graph** — independent `RUN` steps execute concurrently

### Fix
`docker-buildx-plugin` only exists in Docker's **official apt repo** (`download.docker.com`).
Ubuntu's default `docker.io` package (what this EC2 uses) does not include it, so
`apt-get install docker-buildx-plugin` fails with "Unable to locate package".

`deploy.sh` Step 4a instead downloads the buildx binary directly from GitHub releases —
the same source Docker's own install docs recommend when the plugin package isn't available:
```bash
if ! docker buildx version &>/dev/null; then
    BUILDX_VER=$(curl -fsSL https://api.github.com/repos/docker/buildx/releases/latest \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
    mkdir -p ~/.docker/cli-plugins
    curl -fsSL "https://github.com/docker/buildx/releases/download/${BUILDX_VER}/buildx-${BUILDX_VER}.linux-amd64" \
        -o ~/.docker/cli-plugins/docker-buildx
    chmod +x ~/.docker/cli-plugins/docker-buildx
fi
```
Docker discovers CLI plugins in `~/.docker/cli-plugins/` automatically — no apt or root
access needed after the download. The GitHub API call always fetches the latest stable
release so the script stays current without manual version bumps.

### Verification
Run `./scripts/deploy.sh`. Step 4 should complete with a successful push to ECR. On
subsequent deploys the `if` check short-circuits (buildx is already installed) so no
extra network traffic occurs.
