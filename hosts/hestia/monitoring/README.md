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
| `docker-compose-homelabscope-heartbeat.yml` | homelabscope-heartbeat — SSH-reads the alcatraz pull log + ZFS snapshot freshness, probes the Docker socket for per-container state, writes `.prom` files |

## homelabscope

`node-exporter` + `homelabscope-heartbeat` back the **homelabscope** scheduled-job
monitoring family (`homelabscope_job_*`). node-exporter exposes the textfile
collector at `/var/lib/node-exporter/textfile` on `:9100`; the cluster-side
`ScrapeConfig` + alerts + dashboard live in `infra/configs/homelabscope/` and
`infra/configs/dashboards/homelabscope-cm.yaml`. See
`docs/plans/2026-07-04-homelabscope.md`. Before homelabscope there was no
`:9100` scraper on hestia, so `immich-photos-backup`'s textfile metric was
orphaned — this is what fixes that.

When the image changes, `build-homelabscope-heartbeat.yml` publishes a new tag
on push to `master`; pin it in the compose in a follow-up PR (this repo does not
auto-track tags).

### `homelabscope_container_*` — per-container state on hestia

The heartbeat also carries a **second, unrelated family**: a probe over the local
Docker socket. hestia has no cAdvisor and no docker exporter, so before this
there was no signal at all when a hestia container died or crash-looped — the
blind spot that let the GitHub Actions runner sit dead for days on 2026-08-18.
Rationale for extending the heartbeat rather than adding a new exporter is in
[`docs/plans/2026-08-18-hestia-deploy-monitoring-gap.md`](../../../docs/plans/2026-08-18-hestia-deploy-monitoring-gap.md)
section A — short version: it is already privileged, already scraped, already
alerted on, the dashboard is generic over the family, and **the watcher is
already watched**.

| Series | Meaning |
|---|---|
| `homelabscope_container_running{name}` | 1 = running, 0 = not |
| `homelabscope_container_restarts_total{name}` | Docker `RestartCount` (counter; resets on recreate) |
| `homelabscope_container_health{name}` | 1 healthy / 0 unhealthy / 2 starting — **absent** when the container declares no healthcheck |
| `homelabscope_container_started_seconds{name}` | Unix ts the current instance started |
| `homelabscope_container_last_exit_code{name}` | `State.ExitCode` of the previous instance |
| `homelabscope_container_probe_success` | 1/0 — distinguishes "Docker unreachable" from "every container gone" |

It reports **every** container the daemon knows about, running or not — not a
curated watchlist. `x-deploy.archived` is not a trustworthy "should be running"
source (thermalscope is archived in the repo and `up=1` in reality), so the
collector reports what *is* and the `PrometheusRule` decides what *must* be.
`CONTAINER_EXCLUDE_REGEX` suppresses churn (e.g. buildx builders the runner
spawns) without an image rebuild.

**Why `last_exit_code` and `started_seconds` exist.** The `gha-runner` container
is *ephemeral*: its entrypoint tests `[ -n "$EPHEMERAL" ]`, so the compose's
`EPHEMERAL: "false"` — a non-empty string — still activates `--ephemeral`. It
completes one job, deregisters, exits 0, and is restarted by `unless-stopped`:
roughly **one restart per job** (`RestartCount` 0 → 9 across a 10-job deploy).
So restart rate alone cannot tell *restarting and doing work* from *restarting
and completing nothing* — the 11894-restart crash loop. Exit code and instance
uptime can: a crash loop is `restarts high AND last_exit_code != 0`, or
equivalently a `time() - started_seconds` that never grows past seconds.

A true completed-jobs counter is **not** cheaply observable from polled Docker
state and is deliberately not faked — `docker inspect` exposes only the *last*
exit, not a history. The exact source would be the `/events` stream (`die`
carries `exitCode`), which needs a second long-lived process and cross-restart
counter persistence. That is the escalation path if exit code proves
insufficient in practice. `restarts_total` is already the starts signal
(`starts = restarts + 1`), so a separate `..._starts_total` would add nothing.

The container probe polls on `CONTAINER_INTERVAL_SECONDS` (default 60s), not the
600s the SSH/ZFS jobs use: `ExitCode` is a point-in-time sample, and the tight
cadence is what makes it catch a fast crash loop mid-failure while not misreading
a multi-minute job as a flap.

## Deployment

These composes are **operator-applied Custom Apps, not Flux-managed.** Deploy as
a TrueNAS Custom App (paste the YAML into SCALE UI → Apps → Custom App on first
run). Subsequent changes to `hosts/hestia/**/docker-compose*.yml` on `master`
auto-deploy via `.github/workflows/deploy-hestia.yml` (except `x-deploy.archived`
apps, which are skipped).
