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

**Adding a new app**: drop `hosts/hestia/<new-app>/docker-compose-<new-app>.yml` in this tree — no workflow edit needed. The `discover` job in `.github/workflows/deploy-hestia.yml` enumerates `hosts/hestia/**/docker-compose*.yml` on every run and derives the SCALE app name from the filename suffix (`docker-compose-foo.yml` → `foo`). The first deploy of a brand-new app still needs the operator to paste the compose into SCALE UI once (chicken-and-egg — the runner can't create an app that doesn't yet exist, only update one). Subsequent edits flow through the runner automatically.

**Optional `x-deploy:` block** (docker-compose extension key — compose itself ignores `x-*` keys) lets a compose file control deploy behavior:

```yaml
x-deploy:
  archived: true              # bool — must be a YAML true/false, not a string. Skips deploy.
  archived-at: 2026-05-16     # for posterity; surfaced in the workflow run log
  archived-reason: GPUs sold from hestia
  name: thermalscope          # optional — override the filename-derived app name
                              # (needed when the file is plain docker-compose.yml)

services:
  ...
```

Toggle `archived: true` ↔ `false` to deactivate / re-activate an app without removing the compose file from the repo.

**Excluded from the workflow**: the runner itself (`actions-runner/`). A compose change that recreates the runner's own container would kill the workflow mid-flight. Runner upgrades stay manual.

### Manual fallback

If the runner is offline or the change targets the runner itself:

1. Open SCALE UI → Apps → `<app>` → Edit.
2. Paste the new compose YAML.
3. Save.
4. Verify with `docker ps` and the app's healthcheck.

Plan: [`docs/plans/2026-05-02-hestia-gha-runner.md`](../../docs/plans/2026-05-02-hestia-gha-runner.md).

## Operations notes

- [`app.update` does not recreate a crashlooping container](../../docs/operations/2026-05-14-truenas-app-update-quirk.md) — fix-up PRs to a broken app may return `SUCCESS` without taking effect; workaround is an explicit `app.stop` / `app.start`.

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
