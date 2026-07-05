---
status: in-progress
last_modified: 2026-07-04
summary: "homelabscope — one Prometheus metric family (homelabscope_job_last_success_seconds{job}) + textfile collector + cronjob recording rules + templated staleness/absence alerts + Grafana table monitoring EVERY scheduled homelab job; fixes the orphaned (unscraped) immich-backup metric"
---

# homelabscope — unified monitoring for every scheduled job

## Problem

The homelab runs a growing set of automatic/scheduled jobs — nightly photo
syncs, ZFS snapshots, AdGuard config sync, Renovate, pingo — and **none of them
were monitored as a class**. Each was either silent or wired up ad hoc.

The sharpest failure: `immich-photos-backup.sh` faithfully wrote
`immich_photos_backup_last_success_seconds` to the node-exporter textfile
collector at `/var/lib/node-exporter/textfile/immich-backup.prom` on every run,
and an `ImmichPhotoBackupStale` alert keyed off it — but **nothing scraped
:9100 on hestia**. Only thermalscope (:9102) and ipmi-exporter (:9290) were up;
there was no node-exporter. The metric was orphaned, the alert could never fire,
and a stalled backup would have gone unnoticed. Verified 2026-07-04: `ss -ltn`
on hestia shows 9102 + 9290 listening, **not 9100**, and the `.prom` file present
but unscraped.

The generalized gap: a job whose heartbeat **vanishes entirely** (container
gone, CronJob deleted, script crashing before it writes) looks identical to
"healthy" when nothing is watching.

## Design

**One metric family for all jobs**, regardless of where they run:

| Metric | Meaning |
|---|---|
| `homelabscope_job_last_success_seconds{job="<name>"}` | unix ts of last success |
| `homelabscope_job_last_duration_seconds{job="<name>"}` | wall-clock of last run |
| `homelabscope_job_last_status{job="<name>"}` | 0=ok, 1=fail (where known) |
| `homelabscope_job_max_age_seconds{job="<name>"}` | per-job staleness budget |

Fed from three source types, all normalizing into that family:

1. **hestia/alcatraz shell jobs → node-exporter textfile collector on hestia.**
   A new node-exporter Custom App (`hosts/hestia/monitoring/docker-compose-node-exporter.yml`,
   textfile collector only, :9100) exposes the `.prom` files, and a new
   `ScrapeConfig` (`infra/configs/homelabscope/scrapeconfig.yaml`, static target
   `10.42.2.10:9100`, `honorLabels: true`) scrapes them. This finally activates
   the previously-orphaned immich metric.
   - **immich-photos-backup** (hestia, `0 4 * * *`) — its textfile writer now
     emits the homelabscope family alongside the legacy `immich_photos_backup_*`
     series (back-compat kept for one release).
   - **alcatraz-photos-pull** (alcatraz Synology DSM, ~05:00) and
     **zfs-snapshot-main-{family,homes}** can't run an exporter, so a small
     hestia-side **homelabscope-heartbeat** Custom App
     (`images/homelabscope-heartbeat/` → `hosts/hestia/monitoring/docker-compose-homelabscope-heartbeat.yml`)
     loops every 10 min and, read-only: SSHes to alcatraz (reusing the
     immich-photos-backup key + `truenas-backup@10.42.2.11`) to parse the pull
     log's last `=== <ts> END (success, Ns) ===` trailer; and reads
     `zfs list -t snapshot` (newest creation = last success) with a
     `.zfs/snapshot` directory-listing fallback.

2. **k8s CronJobs → recording rules over kube-state-metrics** (no new exporter).
   `infra/configs/homelabscope/prometheus-rule.yaml` projects
   `kube_cronjob_status_last_successful_time` (verified present on this cluster)
   onto the family with `max(...) + labels: {job: <name>}`, and emits each job's
   `homelabscope_job_max_age_seconds` via `vector(N)`.

**Alerting — one templated pair** (`homelabscope.alerts` group):
- `HomelabscopeJobStale`: `time() - homelabscope_job_last_success_seconds > homelabscope_job_max_age_seconds`
  — every job checked against its OWN budget with a single rule (last_success and
  max_age share a label set per job, so the comparison joins on `job`).
- `HomelabscopeJobMetricAbsent`: `absent(...)` per critical job for 1h — catches
  a heartbeat vanishing entirely, the failure mode the old setup missed.

The dataless `ImmichPhotoBackupStale` alert is **retired** (superseded), with a
tombstone comment in `infra/configs/alerts/prometheus-rules.yaml`.

**Dashboard:** `infra/configs/dashboards/homelabscope-cm.yaml` — a table with one
row per job (age, duration, status, health = age/budget colored red when over
budget), stat tiles (jobs over budget / tracked / worst ratio), and a
freshness-ratio timeseries. At-a-glance "are all my scheduled jobs healthy".

## Job registry

| Job | Source | Cadence | max-age | job label |
|---|---|---|---|---|
| immich-photos-backup | hestia textfile | daily 04:00 | 30h | `immich-photos-backup` |
| alcatraz-photos-pull | heartbeat (ssh log) | daily ~05:00 | 30h | `alcatraz-photos-pull` |
| zfs-snapshot main/family | heartbeat (zfs) | daily 05:00 | 30h | `zfs-snapshot-main-family` |
| zfs-snapshot main/homes | heartbeat (zfs) | daily 05:00 | 30h | `zfs-snapshot-main-homes` |
| immich-photos-30d-sync | cronjob (immich-stage) | daily 03:00 | 30h | `immich-photos-30d-sync` |
| renovate | cronjob (renovate) | @daily | 30h | `renovate` |
| adguard-sync (prod) | cronjob (adguard-prod) | `0 */6` | 14h | `adguard-sync-prod` |
| adguard-sync (stage) | cronjob (adguard-stage) | `0 */6` | 14h | `adguard-sync-stage` |
| renovate-automerge | cronjob (renovate) | `0 */6` | 14h | `renovate-automerge` |
| pingo | cronjob (pingo) | `*/5` | 20m | `pingo` |

Budgets: daily → 30h (one missed window), 6h → 14h, 5m → 20m.

## Operator steps (require live hestia access; not done by this PR)

This PR ships repo artifacts only — no live infra changes. To activate:

1. **Deploy the node-exporter Custom App.** `hosts/hestia/monitoring/docker-compose-node-exporter.yml`
   auto-deploys via `deploy-hestia.yml` on merge (stock image
   `quay.io/prometheus/node-exporter:v1.9.1`). Confirm `:9100` comes up and the
   ScrapeConfig target goes green in Prometheus.
2. **Build the heartbeat image.** The first merge of `images/homelabscope-heartbeat/**`
   triggers `build-homelabscope-heartbeat.yml`, publishing
   `ghcr.io/gjcourt/homelabscope-heartbeat:<date>`.
3. **Enable the heartbeat Custom App.** Its compose ships `x-deploy.archived: true`
   (won't deploy against a nonexistent image). In a follow-up PR, pin the
   `@sha256` digest and flip `archived: false`; `deploy-hestia.yml` rolls it out.
4. **Bump the immich-photos-backup image digest.** The edit to
   `images/immich-photos-backup/immich-photos-backup.sh` triggers a rebuild;
   pin the new digest in `hosts/hestia/immich-photos-backup/docker-compose.yml`
   in a follow-up PR (standard images/** two-step; NOT bumped here).

## Deferred / assumptions

- The heartbeat's `.zfs/snapshot` fallback parses snapshot NAMES
  (`auto-YYYY-MM-DD_HH-MM-...`) in `America/Los_Angeles` (the observed schedule
  TZ); it's only used if in-container `zfs list` can't reach `/dev/zfs`.
- Alert delivery: the monitoring stack's critical receiver is still `null`
  (Signal decommission) — see the monitoring-enhancement plan. homelabscope
  alerts evaluate correctly but delivery is gated on a receiver being chosen.
- `homelabscope_job_last_status` is only meaningful for jobs that report it
  (textfile writers set 0 on success); cronjob-backed rows omit it.
