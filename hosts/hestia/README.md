# hestia (TrueNAS SCALE)

| Attribute | Value |
|-----------|-------|
| SSH | `truenas_admin@10.42.2.10` |
| Role | NAS + GPU inference host |
| OS | TrueNAS SCALE |
| Docker | 29.0.4 (managed by TrueNAS Apps) |
| Arch | x86_64 |

## Services

All services on hestia run as **TrueNAS Custom Apps** (SCALE UI → Apps → Custom App).
The compose YAML in each subdirectory is the canonical source; paste into SCALE UI to deploy or update.

| Service | Directory | Port | Notes |
|---------|-----------|------|-------|
| signal | `signal/` | 8080 | signal-cli daemon + signal-bridge SSE relay |
| llms | `llms/` | varies | llama.cpp / vLLM inference (GPU box) |
| monitoring | `monitoring/` | varies | nvtop and GPU metrics |
| gha-runner | `actions-runner/` | — | Self-hosted GitHub Actions runner; auto-deploys other hestia apps |

## ZFS datasets

Operator-owned datasets (survive App deletion):

| Dataset | Mount | Used by |
|---------|-------|---------|
| `tank/apps/signal` | `/mnt/tank/apps/signal` | signal-cli identity data |
| `main/apps/actions-runner` | `/mnt/main/apps/actions-runner` | runner registration + workspace |

## Common operations

```bash
# Check running apps
ssh truenas_admin@10.42.2.10 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"'

# Check an app's env (drift detection)
ssh truenas_admin@10.42.2.10 'docker inspect ix-<app>-<svc>-1 \
  --format "{{.Config.Image}}{{range .Config.Env}}\n{{.}}{{end}}"'

# Create a ZFS snapshot before risky changes
ssh truenas_admin@10.42.2.10 'sudo zfs snapshot tank/apps/<dataset>@pre-change-$(date +%Y%m%d)'
```
