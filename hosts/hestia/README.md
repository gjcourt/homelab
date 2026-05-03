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
The compose YAML in each subdirectory is the canonical source.

| Service | Directory | Port | Notes |
|---------|-----------|------|-------|
| signal | `signal/` | 8080 | signal-cli daemon + signal-bridge SSE relay |
| llms | `llms/` | varies | llama.cpp / vLLM inference (GPU box) |
| monitoring | `monitoring/` | varies | nvtop and GPU metrics |
| gha-runner | `actions-runner/` | — | Self-hosted GitHub Actions runner; auto-deploys other hestia apps |

## Deploying changes

Once the GHA runner is online (see [`actions-runner/README.md`](actions-runner/README.md)),
edits to `hosts/hestia/<app>/docker-compose*.yml` are applied automatically:

1. Open a PR with the compose change.
2. Merge to `master`.
3. The `Deploy hestia Custom Apps` workflow ([`.github/workflows/deploy-hestia.yml`](../../.github/workflows/deploy-hestia.yml)) fires on push, scoped to `hosts/hestia/**/docker-compose*.yml`.
4. The self-hosted runner on hestia executes [`scripts/truenas-update-app.sh`](../../scripts/truenas-update-app.sh), which calls TrueNAS's WebSocket `app.update` API with the new compose. The corresponding container restarts.
5. Verify via the workflow run in GitHub Actions and `docker ps` on hestia.

**Adding a new app**: drop `hosts/hestia/<new-app>/docker-compose.yml` in this tree, then add a matrix entry in `.github/workflows/deploy-hestia.yml` whose `name:` matches the Custom App name in SCALE UI exactly. The first deploy of a brand-new app still needs the operator to paste the compose into SCALE UI once (chicken-and-egg — the runner can't create an app that doesn't yet exist, only update one). Subsequent edits flow through the runner.

**Excluded from the workflow**: the runner itself (`actions-runner/`). A compose change that recreates the runner's own container would kill the workflow mid-flight. Runner upgrades stay manual.

### Manual fallback

If the runner is offline or the change targets the runner itself:

1. Open SCALE UI → Apps → `<app>` → Edit.
2. Paste the new compose YAML.
3. Save.
4. Verify with `docker ps` and the app's healthcheck.

Plan: [`docs/plans/2026-05-02-hestia-gha-runner.md`](../../docs/plans/2026-05-02-hestia-gha-runner.md).

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
