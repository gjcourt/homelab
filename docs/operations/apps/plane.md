# Plane (project tracker)

## 1. Overview

Plane Community Edition is the self-hosted tracker behind lab project 03-042 (the agent desk).
Agents in `bench-cloud` read and update tickets over Plane's REST API and its MCP server. It runs
on staging (`plane-stage`) and production (`plane-prod`, added by plan step 7; see section 7 for
activation).
Plan: [`docs/plans/2026-10-18-plane-staging.md`](../../plans/2026-10-18-plane-staging.md).

## 2. Architecture

- **Install:** Flux HelmRelease `plane`, upstream chart `plane-ce` 1.8.4 (app v1.4.2). Post-render
  patches in `apps/base/plane/release.yaml` add readiness probes and security contexts, and make
  the migrator wait for Postgres (section 6). The overlay adds `DATABASE_URL` and the webhook
  allowlist.
- **Workloads:** web, admin, space, live (websockets), api, worker, beat-worker, plus the migrator
  Job `plane-api-migrate-<revision>` on every install and upgrade.
- **Database:** CNPG cluster `plane-db-staging-cnpg-v1` (Postgres 15, 1 instance, 2Gi
  `truenas-iscsi`). WAL and base backups go to S3 through the barman-cloud plugin
  (`plane-db-staging-cnpg-v1-backup` ObjectStore).
- **Production database:** CNPG cluster `plane-db-production-cnpg-v1` (Postgres 15, 3 instances,
  5Gi `truenas-iscsi`), backups to `s3://gjcourt-homelab-backup/production/plane`.
- **Chart-local services:** Valkey, RabbitMQ, and MinIO for uploads. Production's MinIO volume
  uses the Retain class `truenas-iscsi` (10Gi) and runs with its own root credentials
  (`plane-doc-store` Secret); staging's uses the Delete class and the chart defaults.

## 3. URLs

| What                     | Where                                              |
| ------------------------ | -------------------------------------------------- |
| App                      | `https://plane.stage.burntbytes.com` (LAN and VPN) |
| Instance admin           | `https://plane.stage.burntbytes.com/god-mode/`     |
| API from `bench-cloud`   | `http://plane-api.plane-stage.svc.cluster.local:8000` |
| Production app           | `https://plane.burntbytes.com` (LAN and VPN)       |
| Production admin         | `https://plane.burntbytes.com/god-mode/`           |
| Production API (cluster) | `http://plane-api.plane-prod.svc.cluster.local:8000` |

## 4. Backups and the `serverName` rule

The barman-cloud archiver **refuses to write into an S3 path that already holds data**. Every
database lifetime therefore needs its own `serverName`, set in each overlay's `database.yaml`
(`apps/staging/plane/` and `apps/production/plane/`):

- **Bump `serverName` on every restore or recreate.** A restored or freshly initialised cluster
  archiving into an old path fails, or worse, mixes its own base backups into that path (see the
  drill below).
- **Change it only while no cluster is running.** A running cluster picks up the new path at once
  and archives into it, which spoils the path for the restore you meant to run.
- **Record what each path holds** in the comment block above `serverName`. The history so far:

| `serverName` | Holds                                                                                      |
| ------------ | ------------------------------------------------------------------------------------------ |
| `…-v1`       | First install (2026-10-18): base backup `20261019T033306` plus WAL up to 05:53Z on 10-19. **The restore source.** It also holds empty base backups that later fresh clusters wrote into it |
| `…-v2`, `-v3` | Fresh-initdb clusters created mid-rebuild. Empty data; never restore from them            |
| `…-v4`       | WAL from the first restore, 07:49Z–08:29Z on 10-19, no base backup                         |
| `…-v5`       | Archive of the current cluster (second restore, 16:14Z on 10-19)                           |

Production (`s3://gjcourt-homelab-backup/production/plane`):

| `serverName`                  | Holds                                       |
| ----------------------------- | ------------------------------------------- |
| `plane-db-production-cnpg-v1` | First install. Never written to before it.  |

## 5. Restore drill (plan step 6) — 2026-10-19

The drill wasn't scheduled: a staging-rebuild bug deleted `plane-stage`, database included, twice
on 10-19 (fixed by #1597 and #1604). Both recoveries restored from S3.

**What works:**

1. In `database.yaml`, set `bootstrap.recovery.source` to an `externalClusters` entry that points
   at the old `serverName` (`…-v1`), and **pin `recoveryTarget.backupID`** to the original base
   backup (`20261019T033306`). Without the pin, recovery picks the newest base backup in the path,
   which was an empty database's.
2. Set the archiving `serverName` to a new, empty path.
3. Make sure no cluster with that name is running, then let Flux create it. A cluster that already
   exists ignores the bootstrap section.
4. When the cluster is Ready, restart Plane's Deployments so they reconnect:
   `kubectl -n plane-stage rollout restart deploy`.
5. Check the data: log in at `/god-mode/`, open the workspace, and read a known ticket and its
   comments.

**Timings (second restore, 10-19):**

| Event                                           | Time (UTC) | Elapsed  |
| ----------------------------------------------- | ---------- | -------- |
| Cluster created by Flux                         | 16:14:00   | 0        |
| Restored instance started (after full-recovery) | 16:19:58   | 6 m 0 s  |
| Cluster Ready                                   | 16:20:08   | 6 m 8 s  |
| WAL archiving to `…-v5` healthy                 | 16:20:21   | 6 m 21 s |
| Site, API, admin and live all 200 via gateway   | ~16:21     | ~7 m     |

The database is small (about 20 MB); the time is mostly pod scheduling, the image pull and the
base-backup download. Data came back to the last archived WAL before the deletion (05:53Z), so
nothing was lost from before it.

**Lessons the drill taught (all fixed in #1595):**

- Flux applied the recovery spec only after an empty cluster already existed; the empty one had
  to be deleted.
- The archive path was changed while that empty cluster was running, so it wrote into the new
  path and spoiled it. Hence the "change it only while no cluster is running" rule.
- Empty clusters wrote base backups into the old path, and recovery picked the newest. Hence the
  `backupID` pin.

## 6. Troubleshooting

### HelmRelease fails on first install: migrator gave up

**Symptom:** on a fresh install or a restore into a new cluster, `plane-api-migrate-N` fails four
times and the HelmRelease shows `install retries exhausted`. The pods may all be running.

**Cause:** the image's `manage.py wait_for_db` never opens a connection, so it reports "Database
available!" while CNPG is still starting. `migrate` then fails and uses up the Job's
`backoffLimit: 3` (about 70 s).

**Fix in place:** the migrator's command is patched to open a real connection first, retrying for
about 12 minutes inside the 15-minute Helm timeout, before running the stock entrypoint. If it still
fails, the database never came up: check the CNPG cluster before touching the HelmRelease.

**If a release is already marked failed:** any spec change makes Flux try again, or run
`flux reconcile helmrelease plane -n plane-stage --reset`. An upgrade reruns `migrate`, which does
nothing on an up-to-date schema.

### Plane staging disappeared

The staging branch is rebuilt from master plus open PRs. If #1595 (or any PR that carries Plane)
conflicts with master, the rebuild drops it and Flux deletes `plane-stage`. Merge master into the
PR, then restore the database by section 5.

## 7. Production first install

**Activation** (operator steps; SOPS is operator-only). The overlay `apps/production/plane/` is
not yet listed in `apps/production/kustomization.yaml`, so merging it changes nothing live.

1. Create the three Secrets from their templates in `apps/production/plane/`, encrypt each with
   `sops -e -i`, and uncomment them in that directory's `kustomization.yaml`:
   - `secret-app-keys.yaml` (`plane-app-keys`): `SECRET_KEY` and `LIVE_SERVER_SECRET_KEY`, new
     values, not staging's. Never rotate `SECRET_KEY` afterwards.
   - `secret-doc-store.yaml` (`plane-doc-store`): MinIO root user and password, repeated as the
     `AWS_*` pair. Set before the first install; MinIO only reads it when its volume is new.
   - `secret-aws-creds.yaml` (`plane-aws-creds-secret`): the S3 backup credentials, same keys as
     the other production clusters.
2. Add `- plane` to `apps/production/kustomization.yaml` in the same PR.

**Checks before any real data goes in:**

- [ ] **Migrator:** `kubectl -n plane-prod logs job/plane-api-migrate-1` shows "Postgres not
      accepting connections yet" retries (or none, if CNPG was already up), then a successful
      `migrate`. Not a failed Job.
- [ ] **HelmRelease:** `kubectl -n plane-prod get helmrelease plane` is Ready, and its history has
      no failed install attempt.
- [ ] **Fresh `serverName`:** the cluster archives to `plane-db-production-cnpg-v1`, and that path
      held nothing before this install.
- [ ] **Backups healthy:** the `immediate` base backup completed
      (`kubectl -n plane-prod get backups`), and the cluster reports WAL archiving working
      (`kubectl cnpg status plane-db-production-cnpg-v1 -n plane-prod`, or the Cluster's
      `ContinuousArchiving` condition True).
- [ ] **MinIO volume:** the PV behind `pvc-plane-minio-vol-plane-minio-wl-0` has reclaim policy
      `Retain`.
- [ ] **Access:** `https://plane.burntbytes.com` loads on LAN and over the VPN, and not from the
      internet (plan step 8). The hostname is not in the Cloudflare tunnel config.
- [ ] **Admin:** create the instance admin at `/god-mode/` straight away. Until then, anyone on
      the LAN who reaches the page first can claim it.

**Not covered yet:** the MinIO bucket has no backup beyond its Retain volume. The repo has no
volume-snapshot schedule or bucket mirror today; see the production PR for the open item.

