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

## Deployment

These composes are **operator-applied Custom Apps, not Flux-managed.** Deploy as
a TrueNAS Custom App (paste the YAML into SCALE UI → Apps → Custom App on first
run). Subsequent changes to `hosts/hestia/**/docker-compose*.yml` on `master`
auto-deploy via `.github/workflows/deploy-hestia.yml` (except `x-deploy.archived`
apps, which are skipped).
