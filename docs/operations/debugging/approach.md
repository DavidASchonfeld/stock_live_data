# Debugging Approach — Mental Model

A learning-oriented reference for debugging this project's stack: **K3s + Airflow + Flask on EC2**.

**Quick Navigation**
- Need to understand what `ss -tlnp`, `kubectl`, or `rsync` do? See [../../reference/COMMANDS.md](../../reference/COMMANDS.md)
- Want to understand system architecture? See [../../architecture/SYSTEM_OVERVIEW.md](../../architecture/SYSTEM_OVERVIEW.md)
- Looking for a specific term definition? See [../../reference/GLOSSARY.md](../../reference/GLOSSARY.md) (iptables, XCom, inode, etc.)
- Failure mode catalog? See [../../architecture/FAILURE_MODE_MAP.md](../../architecture/FAILURE_MODE_MAP.md)
- Prevention checklists? See [../PREVENTION_CHECKLIST.md](../PREVENTION_CHECKLIST.md)
- Need help with a specific issue? Jump to [Common Issues (A-F)](common-issues-1.md) or [Common Issues (G-N)](common-issues-2.md)

---

## Mental Model — How the Stack Connects

Before diving into commands, understand the three-layer path that traffic takes when you open `http://localhost:30080`:

```
Your Mac (SSH tunnel)
  → EC2 NodePort (iptables rule, not a bound socket)
    → K8s Service (matches pods by selector labels)
      → Pod endpoint (the actual running container)
```

**Key things that trip you up:**

- **`ss -tlnp` returns nothing for NodePorts** — k3s uses iptables rules, not bound sockets. The port "exists" in the iptables firewall, not as a listening process. `ss` only shows bound sockets, so it will always look empty for k3s NodePorts. See [COMMANDS.md#ss--tlnp](COMMANDS.md#ss--tlnp) for full explanation of this command.

- **`docker ps` shows `k8s_` prefixed containers** — that means containerd is running the pods, not Docker Compose. You're in Kubernetes. Use `kubectl`, not `docker`.

- **Two namespaces exist in this project:**
  - `airflow-my-namespace` — all Airflow pods (scheduler, api-server, triggerer, postgresql, dag-processor)
  - `default` — Flask/Dash pod (`my-kuber-pod-flask`)

- **kubectl context defaults to `airflow-my-namespace`** on EC2, so commands without `-n` apply there. Use `-n default` or `--all-namespaces` for Flask resources.
