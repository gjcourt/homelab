# monitoring — hestia thermals, power, and scheduled-job metrics

Prometheus exporters for hestia (a bare TrueNAS host outside the Talos
node-exporter DaemonSet).

## Services

| File | Purpose |
|------|---------|
| `docker-compose-nvtop.yml` | nvtop — interactive GPU process viewer |
| `docker-compose.yml` | thermalscope — Prometheus thermal/GPU exporter on `:9102` (archived; GPUs sold) |
| `docker-compose-ipmi-exporter.yml` | prometheus-ipmi-exporter — BMC fans/temps on `:9290` |
| `docker-compose-node-exporter.yml` | node-exporter (textfile collector only) on `:9100` — serves the homelabscope `.prom` files |
| `docker-compose-homelabscope-heartbeat.yml` | homelabscope-heartbeat — SSH-reads the alcatraz pull log + ZFS snapshot freshness, writes `.prom` files (archived until its image is built) |
| `docker-compose-gha-runner-exporter.yml` | gha-runner-exporter — polls Docker for the self-hosted GHA runner's state, writes `gha-runner.prom` (archived until installed once by hand) |

## homelabscope

`node-exporter` + `homelabscope-heartbeat` back the **homelabscope** scheduled-job
monitoring family (`homelabscope_job_*`). node-exporter exposes the textfile
collector at `/var/lib/node-exporter/textfile` on `:9100`; the cluster-side
`ScrapeConfig` + alerts + dashboard live in `infra/configs/homelabscope/` and
`infra/configs/dashboards/homelabscope-cm.yaml`. See
`docs/plans/2026-07-04-homelabscope.md`. Before homelabscope there was no
`:9100` scraper on hestia, so `immich-photos-backup`'s textfile metric was
orphaned — this is what fixes that.

`homelabscope-heartbeat` ships `x-deploy.archived: true` until its image is
built by `build-homelabscope-heartbeat.yml`; then pin the `@sha256` digest and
flip `archived: false`.

## gha-runner-exporter

Liveness for the self-hosted GitHub Actions runner (`hosts/hestia/actions-runner/`).

On 2026-08-18 the `gha-runner` container crash-looped **13,173 times** — GitHub
deprecated the pinned runner version, the runner exited 1 at startup, and
`restart: unless-stopped` restarted it forever. A deploy job sat queued at
GitHub for over a day and **nothing alerted**, because no Prometheus series
described the runner at all. This exporter is that missing signal.

It polls the local Docker socket every 60s and writes
`/var/lib/node-exporter/textfile/gha-runner.prom` atomically (`.prom.tmp` →
`mv`, so node-exporter never reads a half-written file):

| Metric | Meaning |
|--------|---------|
| `homelab_gha_runner_up{runner="hestia"}` | `1` only if `State.Running` is true **and** health is `healthy` or no healthcheck is defined; else `0` |
| `homelab_gha_runner_restart_count{runner="hestia"}` | `docker inspect .RestartCount` — climbs monotonically during a crash loop |
| `homelab_gha_runner_last_check_timestamp_seconds{runner="hestia"}` | Unix time of the last check that reached the Docker daemon |

Metric names and the `runner` label are a **frozen contract** with the alert
rules — do not rename them here without changing them there. (`promtool check
metrics` emits a naming-convention warning about the `_count` suffix on a
gauge; that is a lint opinion, not a parse error, and the name is deliberate.)

**No GitHub credential, by design.** The failure mode is a local crash loop and
Docker state describes it completely; hestia already holds `ACCESS_TOKEN` and
`TRUENAS_API_KEY` and a third long-lived secret is not worth "is the runner
Idle at GitHub" as a second opinion.

If the Docker daemon is unreachable the exporter leaves the previous `.prom`
**untouched** rather than writing `up=0` — the runner isn't necessarily down,
the exporter is blind. `last_check_timestamp_seconds` goes stale and the alert
rules catch the exporter itself.

Because a healthcheck is declared on the runner with `start_period: 60s`, a
normal restart briefly reports `up=0` while health is `starting`. Alert rules
should carry a `for:` window longer than that.

### First install

Ships with `x-deploy.archived: true`. `scripts/truenas-update-app.sh` calls
`app.query` before updating and exits non-zero for an app that has never been
installed, so deploying this un-archived would turn the next `deploy-hestia`
run red. Paste the compose into SCALE UI → Apps → Custom App, name it exactly
`gha-runner-exporter`, then flip `archived: false` in a follow-up PR so
`deploy-hestia.yml` owns it from then on.

## Deployment

These composes are **operator-applied Custom Apps, not Flux-managed.** Deploy as
a TrueNAS Custom App (paste the YAML into SCALE UI → Apps → Custom App on first
run). Subsequent changes to `hosts/hestia/**/docker-compose*.yml` on `master`
auto-deploy via `.github/workflows/deploy-hestia.yml` (except `x-deploy.archived`
apps, which are skipped).
