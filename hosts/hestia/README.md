# hestia (TrueNAS SCALE)

| Attribute | Value |
|-----------|-------|
| SSH | `truenas_admin@10.42.2.10` (no passwordless sudo; `/home` is `noexec`) |
| IPMI | ASRock Rack BMC at `10.42.2.13` (switch port 48) |
| Role | TrueNAS storage + general-purpose compute (no GPUs since 2026-05-16) |
| OS | TrueNAS SCALE |
| Docker | 29.0.4 (managed by TrueNAS Apps) |
| Arch | x86_64 |

## Hardware

| Component | Detail |
|-----------|--------|
| Board | ASRock Rack SIENAD8-2L2T (8 DIMM slots over 6 memory channels — 2 channels share slots) |
| CPU | AMD EPYC 8324P (32-core Siena, Zen 4c), NPS1 (1 NUMA node) |
| RAM | DDR5, populated 6× (1DPC × 6 channels = full speed); 8-DIMM mode forces 2 channels into 2DPC and derates the bus |
| GPUs | **None** — 2× RTX 4090 sold 2026-05-16. `nvidia-smi` fails on the host; this is expected. |
| Storage | pool `main` at `/mnt/main` (~21 TB) |

### Network interfaces

- **Management NIC** `enp201s0` — 1 GbE onboard, static `10.42.2.10/24` (active interface).
- **25 GbE Mellanox NIC** — removed 2026-05-14 (overheating → PCIe BadTLP errors and instability); switch ports 51/52 LAG config preserved but inactive.
- **IPMI NIC** — ASRock Rack BMC at `10.42.2.13` (switch port 48, DHCP on the Lab VLAN).

## Services

All services on hestia run as **TrueNAS Custom Apps** (SCALE UI → Apps → Custom App).
The compose YAML in each subdirectory is the canonical source.

| Service | Directory | Notes |
|---------|-----------|-------|
| gha-runner | `actions-runner/` | Self-hosted GitHub Actions runner; auto-deploys other hestia apps |
| immich-photos-backup | `immich-photos-backup/` | Daily rsync of the Immich photo library from alcatraz into ZFS |
| qbittorrent | `qbittorrent/` | P2P client, private-tracker downloads |
| thermalscope | `thermalscope/` | Thermal + power telemetry, Prometheus metrics over host networking |
| ipmi-exporter | `monitoring/` | IPMI metrics (`docker-compose-ipmi-exporter.yml`) |
| memory bench | `bench/memory/` | On-demand STREAM + Intel MLC bandwidth benchmark (not a long-running app) |
| llms | `llms/` | **Archived 2026-05-16** — llama.cpp / vLLM inference; GPUs sold |
| monitoring (GPU) | `monitoring/` | **Archived** — `nvtop` + GPU metrics; GPUs sold |

> signal-cli moved off hestia into the cluster (`apps/base/signal-cli/`, namespace `signal-cli`) — the old `signal/` Custom App was removed. Both it and the `hermes` bots are currently scaled to 0 (no LLM backend since the GPU sale).

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
| `main/apps/actions-runner` | `/mnt/main/apps/actions-runner` | runner registration + workspace |
| `main/family/images/photos` | `/mnt/main/family/images/photos` | Immich photo-backup rsync target |
| `tank/apps/signal` | `/mnt/tank/apps/signal` | Legacy — signal-cli identity data from the removed Custom App (signal-cli now runs in-cluster) |

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
