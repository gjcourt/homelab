---
status: planned
last_modified: 2026-07-04
summary: "Consolidate Dropbox (Windows) + Google Drive into a self-hosted private cloud on hestia (Nextcloud recommended); rclone-based migration reused by the iPhone photo-recovery hunt"
---

# Own your files: consolidate Dropbox + Google Drive → self-hosted private cloud on hestia

> **Definition doc, not a build plan.** This scopes the work, recommends an
> architecture, and lays out a phased migration. No infra is provisioned by
> this PR. Several inputs are **TBD** and gated on George — see
> [§2](#2-current-state--inputs-to-gather) and [§8](#8-open-decisions-for-george).

## 1. Vision / goals

Retire two rented file silos — **Dropbox** (synced from a Windows machine) and
**Google Drive** — and bring their contents onto **hestia** (TrueNAS 26.x,
`10.42.2.10`) behind a self-hosted private-cloud app. Concretely:

- **Own the bytes.** Files live on hestia ZFS, snapshotted and backed by the
  same DR posture as the photo library, instead of on Dropbox/Google servers.
- **Keep the ergonomics that made those services sticky.** A Windows desktop
  sync client (files appear in a local folder, edits sync both ways), a
  Drive-like web UI with folder sharing/links, and single sign-on through the
  existing Authelia OIDC provider — no new password.
- **One canonical namespace.** Dropbox and Drive collapse into one tree on
  hestia, deduped, with structure preserved, reachable at (proposed)
  `cloud.burntbytes.com` through the existing Cloudflare tunnel + Cilium
  Gateway path that already fronts `photos.burntbytes.com`.
- **Downgrade, then cancel.** Once verified, drop Dropbox to the free tier /
  cancel, and drop Google to free-tier storage — the paid subscriptions are the
  thing this project eliminates.

This is the natural next step after the Immich (photos) and hestia-SOT
(`family/` + `homes/`) work: those made hestia authoritative for photos and
household files; this makes it authoritative for the *working document/file
set* that currently lives in Dropbox and Drive.

## 2. Current state + inputs to gather

hestia already backs a media-heavy app end-to-end — **Immich** on a CNPG
Postgres cluster (`apps/production/immich/database.yaml`) with a 5 TiB
NFS-backed library PV (`apps/production/immich/nfs-photos.yaml`), Authelia OIDC
(`immich` client in `apps/production/authelia/configuration.yaml`), a gateway
HTTPRoute (`apps/production/immich/httproute.yaml`) and a Cloudflare tunnel
entry (`apps/production/cloudflare-tunnel/config.yaml`). **This is the
reference pattern for the new app.** hestia's `main` pool had ~21 TiB free at
the time of the alcatraz→hestia migration; confirm current headroom before
sizing (see [§8](#8-open-decisions-for-george)).

Everything below marked **TBD** must come from George before Phase 0 starts.
The migration mechanics don't change based on these answers, but the storage
sizing, the Google-native export decision, and the bandwidth/time estimates do.

| Input | Why it matters | Value |
|---|---|---|
| **Dropbox data volume (GB)** | Sizes the data PVC + initial-load time | **TBD** |
| **Google Drive data volume (GB)** | Same | **TBD** |
| **What's in Dropbox** (docs, code, media, camera-upload photos?) | Dedup strategy; whether large-file handling matters; feeds the photo-recovery cross-link ([§9](#9-cross-link-iphone-saltwater-photo-recovery)) | **TBD** — expected to include an old **Dropbox Camera Upload** trove |
| **What's in Drive** (Google-native Docs/Sheets/Slides vs uploaded files) | Drives the export-format decision ([§4](#4b-google-drive--hestia)) | **TBD** |
| **Google account type** (personal `@gmail` vs Workspace) | Workspace unlocks admin export + affects API quotas + shared-drive access | **TBD** — George's is `gjcourt@gmail.com` (personal), but confirm no Workspace tenant is in play |
| **Shared drives / files shared *with* George** | rclone treats My Drive and Shared Drives as separate roots; shared-with-me needs an explicit decision (copy or skip) | **TBD** |
| **Windows Dropbox folder: fully locally synced or selective/online-only?** | If files are online-only placeholders, an SMB/local-folder copy silently skips them — must force full local sync first, or use the server-side rclone remote instead | **TBD** |
| **Windows machine specifics** (drive free space, whether it stays on) | Bounds the "copy the already-synced folder" option; long-path risk ([§4](#4-migration-strategy-per-source)) | **TBD** |

## 3. Target architecture — recommend a private-cloud app

### 3a. Options considered

| | **Nextcloud** | **Seafile** | **ownCloud OCIS** |
|---|---|---|---|
| Positioning | The private Dropbox/Drive — files + apps ecosystem | Large-file / high-throughput sync, block-dedup | Modern Go rewrite of ownCloud, single binary |
| Windows desktop sync client | Mature, first-class | Mature, first-class | Newer, less battle-tested |
| Drive-like web UI + share links | Yes (rich; comments, versions, public links, groups) | Yes (leaner) | Yes (clean, improving) |
| OIDC SSO (Authelia) | Yes (well-trodden; `user_oidc` app) | Yes | Yes (OIDC-native) |
| Datastore model | Files stored **as-is on the filesystem** (NFS/local) + metadata in Postgres | Files stored in an **internal block/object format** (not browsable on disk) | Files on disk (POSIX) + own metadata |
| Postgres fit (CNPG) | Native — Postgres is the recommended DB | Postgres supported | Uses embedded/own stores; less DB-centric |
| Big-file sync performance | Good; historically the weak spot but fine at this scale | Best-in-class (its whole reason to exist) | Good |
| Ecosystem / lock-in of *your bytes* | Files stay plain on the NFS share — trivially recoverable outside the app | Bytes live in Seafile's block store — recovery requires Seafile | Files stay plain on disk |

### 3b. Recommendation: **Nextcloud**

For *this* homelab, Nextcloud is the right default:

1. **It is the like-for-like replacement.** It replaces *both* products at once:
   Dropbox (Windows sync client + a synced local folder) **and** Drive
   (web UI, folder sharing, public links). Seafile matches the Dropbox sync
   half well but is a thinner Drive replacement; OCIS is the least mature client
   story.
2. **Files-on-filesystem matches the homelab's whole storage philosophy.**
   Nextcloud's primary data directory is just a POSIX tree — point it at an
   **NFS export from hestia**, exactly like Immich's library PV. The bytes are
   directly visible on the ZFS dataset, so ZFS snapshots, the alcatraz
   second-copy rsync, and `du`/`ls` verification all work *without going through
   the app*. Seafile's block store would make the bytes opaque to every one of
   those existing tools — a real regression against the "own your files" goal.
3. **CNPG is a first-class fit.** Nextcloud on Postgres is the documented,
   supported path; clone the Immich CNPG cluster shape
   (`apps/production/immich/database.yaml`) — 3 instances, `truenas-iscsi`
   storage, Barman Cloud → S3 WAL archiving, PodMonitor.
4. **Authelia OIDC is already proven** for this exact provider; add a `nextcloud`
   client alongside `immich`/`mealie`/`memos` and enable Nextcloud's `user_oidc`.

**When to reconsider:** if [§2](#2-current-state--inputs-to-gather) reveals the
data is dominated by very large files with heavy re-sync churn (VM images, big
media projects) where Seafile's block-level dedup/delta-sync is materially
better. Given the expected content (documents + code + a photo trove), Nextcloud
wins. Flagged as an open decision anyway ([§8](#8-open-decisions-for-george)).

### 3c. Concrete shape (cloned from Immich)

Namespaced `nextcloud-prod` (+ optional `nextcloud-stage`), following the repo's
`-prod`/`-stage` overlay convention.

| Concern | Approach | Reference to clone |
|---|---|---|
| App | Nextcloud (fpm or `apache` image) Deployment, readiness on `/status.php`, read-only rootfs where possible, PDB | `apps/base/immich/` layout |
| Database | CNPG `Cluster`, 3 instances, `storageClass: truenas-iscsi`, Barman→S3 backups + `ScheduledBackup` | `apps/production/immich/{database,objectstore,scheduledbackup}.yaml` |
| **File data** | Static **NFS PV** from hestia (`10.42.2.10`), `ReadWriteMany`, `Retain`, sized to data + headroom; new ZFS dataset e.g. `main/cloud/nextcloud` (lz4, `recordsize=1M`, `atime=off`) | `apps/production/immich/nfs-photos.yaml` |
| Config/state PVC | Small `truenas-iscsi` PVC for the Nextcloud `config/` + apps if not on NFS | `apps/production/immich/storage.yaml` |
| SSO | Authelia OIDC client `nextcloud` + `user_oidc` app config | `apps/production/authelia/configuration.yaml`, `apps/production/linkding/configmap-oidc.yaml` |
| Ingress (LAN) | HTTPRoute on `app-gateway-production`, hostname `cloud.burntbytes.com` | `apps/production/immich/httproute.yaml` |
| Ingress (WAN) | Cloudflare tunnel ingress entry for `cloud.burntbytes.com` → gateway | `apps/production/cloudflare-tunnel/config.yaml` |
| Redis | Nextcloud wants Redis for file-locking/cache at multi-replica; small in-namespace Redis or single-replica app to start | new (small) |
| Secrets | SOPS-encrypted DB creds + OIDC secret; **operator encrypts** (ship `.example`, let CI gate) | `apps/production/immich/secret-*.yaml` |

**Redis / locking caveat:** Nextcloud needs distributed file-locking (Redis)
before it can safely run >1 replica or serve concurrent desktop clients. Size
the app at 1 replica initially; add Redis + scale only once verified. Note also
that Nextcloud over **NFS** requires correct locking config (`filelocking` via
Redis, not the DB) — an explicit checklist item, not an afterthought.

## 4. Migration strategy per source

**Guiding rule for the whole migration: the third-party source stays
authoritative and read-only until hestia is verified.** No `--delete` against a
source; no cancelling any subscription until [§5](#5-phased-plan) Phase 5 passes.

Tooling: **rclone** is the backbone for both sources — it does server-side,
resumable, checksum-verifiable transfers and (critically) both remotes are
**reusable afterward** for ad-hoc pulls, including the photo-recovery hunt
([§9](#9-cross-link-iphone-saltwater-photo-recovery)). Run rclone from a
long-lived context on hestia (a `tmux`/`screen` session, or a short-lived
Kubernetes Job) writing directly onto the NFS dataset — same operational shape
as the existing `immich-photos-backup` rsync container.

### 4a. Dropbox → hestia

Three options, in recommended order:

1. **rclone `dropbox` remote (RECOMMENDED).** `rclone config` a Dropbox remote
   (OAuth; for a large/long migration create a Dropbox *app* to get a personal
   `client_id`/`client_secret` and avoid the shared-app rate limits), then:
   ```
   rclone copy dropbox:/ /mnt/main/cloud/nextcloud/<user>/Dropbox/ \
     --transfers=8 --checkers=16 --tpslimit=12 \
     --progress --log-file=/var/log/rclone-dropbox.log
   ```
   Server-side and resumable (rerun to catch deltas), independent of the Windows
   machine's state, and **verifiable** — `rclone check` compares Dropbox's
   content hashes against local files. This is the safe default and it doubles
   as the access path the photo-recovery project needs.
2. **Copy the already-synced Windows Dropbox folder** via the new Nextcloud
   Windows client or an SMB share on hestia. Only valid if the Windows folder is
   **fully locally synced** (not online-only placeholders — see the TBD in
   [§2](#2-current-state--inputs-to-gather)); online-only files copy as 0-byte
   stubs. Also exposed to Windows **260-char path-length** limits on deep trees.
   Use only as a cross-check, not the primary path.
3. **Dropbox export / "Download as zip".** Manual, not resumable, breaks on
   large accounts, loses nothing but is the least reliable. Fallback only.

### 4b. Google Drive → hestia

1. **rclone `drive` remote (RECOMMENDED).** Configure a Drive remote (for a
   large migration, create a Google Cloud project + OAuth client to raise API
   quotas above the shared default). Handle roots explicitly:
   - **My Drive**: `rclone copy gdrive: /mnt/main/cloud/nextcloud/<user>/GoogleDrive/`
   - **Shared drives**: each is a separate root — enumerate with
     `rclone backend drives gdrive:` and copy each wanted one. **TBD** which to
     include ([§2](#2-current-state--inputs-to-gather)).
   - **Shared-with-me**: optional, via `--drive-shared-with-me`. Decide copy vs
     skip (often skip — they're someone else's canonical copy).
   - **KEY DECISION — Google-native Docs/Sheets/Slides export format.** Native
     Google files have **no bytes to download** until exported. rclone's
     `--drive-export-formats` controls this. Options:
     - **Export to editable Office/ODF** (`docx,xlsx,pptx` or `odt,ods,odp`) —
       RECOMMENDED for owning the content; editable in Nextcloud/LibreOffice.
       *Lossy on complex formatting, comments, and revision history.*
     - **Export to PDF** — highest visual fidelity, but read-only.
     - **Keep as `.gdoc`/`.gsheet` shortcut links** — preserves the live Google
       doc but that defeats the "own your files" goal (still tethered to Google).
     Recommendation: export to Office formats **and** keep the source Drive
     read-only as the fidelity fallback until George confirms the exports are
     acceptable. This is a per-George decision ([§8](#8-open-decisions-for-george)).
2. **Google Takeout.** Bulk zip export of the whole account. Good for a
   one-shot archive and for Google-native files it applies its own export
   choices, but: multi-gigabyte zip splitting, no resume, manual, and
   Takeout-of-Drive can flatten/mangle folder structure. Use as a **belt-and-
   suspenders archive** captured *once* alongside the rclone migration, not as
   the primary path.

### 4c. Cross-cutting migration concerns

- **Verification (checksums).** After each source: `rclone check` (Dropbox
  content-hash / Drive MD5 where available) against the local tree; spot-check
  file counts + `du -sh`. Mirror the "within 1%" acceptance test used in the
  hestia-SOT plan. Google-native exports have no source hash — verify by
  count + manual open of a sample.
- **Structure preservation.** rclone preserves the folder tree as-is. Land each
  source under a clearly separated top-level prefix
  (`.../Dropbox/`, `.../GoogleDrive/`) so provenance is obvious and a later
  merge/flatten is a deliberate, separate step (same philosophy as the hestia-
  SOT plan keeping the alcatraz layout as-is first).
- **Dedup.** Dropbox and Drive likely overlap (same docs in both). Do **not**
  auto-merge. Keep them side-by-side, then run a read-only dup report
  (`rclone dedupe --dry-run`, or `jdupes`/`rmlint` on the NFS dataset) and let
  George decide. Dedup is a post-migration cleanup, never part of the copy.
- **Large files + Windows path-length.** Deep Dropbox trees synced from Windows
  can carry >260-char paths; rclone (server-side) sidesteps the Windows limit
  entirely — another reason to prefer it over the local-folder copy. Watch for
  filename-charset edge cases (`:` `?` `*` illegal on Windows) when the desktop
  client later syncs the merged tree *back* to Windows.
- **Rate limits.** Both APIs throttle; use `--tpslimit`/`--drive-pacer-*` and
  dedicated OAuth apps to lift shared quotas. Expect Drive especially to pace —
  build time estimates off measured throughput once volumes are known
  ([§2](#2-current-state--inputs-to-gather)).

## 5. Phased plan

Sequenced so the app is proven empty-then-loaded before any client cutover, and
the third-party sources stay live until the very end — the same conservative
"stand up → bulk seed → cut over → verify → decommission" shape as the
alcatraz→hestia and hestia-SOT plans.

```
P0  Inputs + decisions        Gather §2 TBDs; George resolves §8 decisions
P1  Stand up Nextcloud        ZFS dataset + NFS PV; CNPG DB; app; OIDC; gateway
                              + tunnel route. Verify empty app works end-to-end.
P2  Bulk-migrate Dropbox      rclone dropbox remote → NFS dataset; rclone check;
                              nextcloud occ files:scan. Dropbox stays read-only.
P3  Bulk-migrate Drive        rclone drive remote (+ export-format decision) →
                              NFS dataset; Takeout archive once; scan; verify.
P4  Windows sync client       Install Nextcloud desktop client on Windows;
    + cutover                 point it at the merged tree; confirm two-way sync;
                              stop using Dropbox/Drive as the working set.
P5  Verify + soak (~1–2 wk)   Counts/hashes match; sync round-trips; snapshots
                              fire; alcatraz second-copy includes the dataset.
P6  Decommission / downgrade  Downgrade Dropbox to free / cancel; drop Google to
                              free-tier storage. Keep both as read-only parachute
                              for a defined window (e.g. 30 days) before cancel.
```

- **P1 is the only "build" phase with cluster manifests**; it clones the Immich
  app shape. Each subsequent phase is operator-run rclone + `occ` (Nextcloud's
  CLI) work on hestia, captured here as the runbook — no further manifest churn
  except the OIDC/redis follow-ups.
- **Never merge P1 as one giant PR.** Split like the burntbytes rollout: (a)
  ZFS dataset + NFS PV + CNPG DB, (b) app Deployment + storage + gateway
  HTTPRoute, (c) OIDC client + tunnel route (the one WAN-exposing change,
  isolated for clean revert). Staging overlay optional but recommended for the
  OIDC/redis shakeout.
- After a large rclone copy, run `occ files:scan --all` so Nextcloud indexes
  files landed on the NFS share out-of-band (it doesn't watch the filesystem).

## 6. Backup / DR

The new dataset inherits the exact posture the photo library already has — this
is a major reason to store files plain-on-NFS rather than in an opaque app store.

- **ZFS snapshots on hestia.** Add periodic-snapshot tasks on
  `main/cloud/nextcloud` mirroring the family/photos policy: daily/14, weekly/8,
  monthly/12 (see `docs/plans/2026-06-01-hestia-photos-sot.md` §1). Snapshots
  give point-in-time recovery of any file/version independent of Nextcloud's own
  version history.
- **alcatraz second copy.** Extend the existing hestia→alcatraz (or
  alcatraz-pull) second-copy mechanism to include `main/cloud/nextcloud`, so the
  private-cloud data gets the same off-box redundancy as photos. Same
  rsync-pull + independent-snapshots model already in flight.
- **Database.** CNPG Barman Cloud → S3 WAL archiving + `ScheduledBackup` gives
  PITR for the Nextcloud metadata DB (clone Immich's `objectstore.yaml` +
  `scheduledbackup.yaml`). Note the DB holds *metadata/shares/state*; the file
  bytes are protected by ZFS+alcatraz above — both halves are needed for a full
  restore.
- **homelabscope monitoring.** The scheduled-job monitoring layer being added
  (`docs/plans/2026-07-04-homelabscope.md`, on the `feat/homelabscope` branch)
  should watch the rclone migration jobs and any recurring sync/verify job, plus
  a freshness metric for the alcatraz second-copy of this dataset — the same
  textfile-collector + Prometheus-alert pattern the alcatraz→hestia plan used for
  `immich_photos_backup_last_success_seconds`.

## 7. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Google Docs export fidelity loss** (formatting, comments, revision history) | High for complex docs | Export to Office **and** keep source Drive read-only as fidelity fallback; capture a Takeout archive once; George sign-off on samples before decommission |
| **Sync conflicts** once the Windows client is live over a merged tree | Medium | Single-writer cutover: stop editing in Dropbox/Drive *before* enabling desktop sync; Nextcloud conflict-copy files (`(conflicted copy)`) surface, don't silently lose data; verify Redis file-locking before multi-client use |
| **Initial-load capacity** — combined Dropbox+Drive exceeds planned dataset/pool headroom | Medium (until [§2](#2-current-state--inputs-to-gather) known) | Confirm current `main` pool free space and set an explicit dataset quota sized to data + ~50% headroom + snapshot overhead before P2 |
| **Bandwidth / time** for the initial pull | Medium | rclone is resumable; run in `tmux`; estimate from measured throughput once volumes known; schedule large pulls off-peak |
| **API rate limits** (Dropbox app limits, Drive quotas) | Medium | Dedicated OAuth apps to lift shared quotas; `--tpslimit`/`--drive-pacer`; accept longer wall-clock |
| **Nextcloud-over-NFS locking misconfig** → corruption/db-lock errors | Medium | Redis file-locking configured before multi-replica/desktop load; start at 1 replica; test concurrent edits in soak |
| **Cutover safety** — deleting source before hestia verified | Low (process-gated) | Sources kept read-only through P5; nothing cancelled until P6, and only after a defined parachute window |
| **Secrets handling** | Low | SOPS-encrypt DB/OIDC secrets (operator-only); ship `.example`, let CI gate — never commit plaintext |

## 8. Open decisions for George

1. **App choice** — Nextcloud (recommended) vs Seafile (only if data is
   dominated by huge, high-churn files) vs OCIS. Default: **Nextcloud**.
2. **Storage pool + size** — `main` vs `tank`; dataset quota. Needs the
   Dropbox+Drive GB totals ([§2](#2-current-state--inputs-to-gather)) and a
   current pool-free-space check.
3. **Google Docs handling** — export to editable Office/ODF (recommended) vs
   PDF vs keep-as-links. Accept the fidelity/ownership tradeoff.
4. **Full cutover vs keep-as-backup** — cancel Dropbox/Drive after the parachute
   window, or keep a paid tier as an ongoing off-site backup? (Recommendation:
   downgrade to free tiers, don't keep paying; alcatraz + ZFS is the redundancy.)
5. **Domain / exposure** — `cloud.burntbytes.com` proposed; WAN-exposed via the
   Cloudflare tunnel like `photos.burntbytes.com`, or LAN-only to start?
6. **SSO** — Authelia OIDC as the only login (recommended, matches every other
   app) vs also keeping Nextcloud-local accounts as a break-glass.
7. **Staging overlay** — stand up `nextcloud-stage` to shake out OIDC + NFS
   locking first, or go straight to prod given the reference pattern is proven?
8. **Redis / multi-replica** — start single-replica (simplest) and add Redis +
   scale later, or provision Redis up front?

## 9. Cross-link: iPhone saltwater photo recovery

A separate project — recovering old iPhone photos (a lost/water-damaged phone;
target era ~honeymoon / first year of marriage) — needs to search George's
Dropbox for old **Dropbox Camera Upload** backups from that period. **The rclone
`dropbox` remote this project configures in [§4a](#4a-dropbox--hestia) is the
exact access path that hunt needs**, so the two projects should share it:

- Configure the Dropbox remote **once**, here, with a persistent rclone config
  on hestia (and/or a dedicated Dropbox OAuth app so quotas/credentials are
  stable and reusable).
- The photo-recovery project can then `rclone lsf`/`rclone copy` the
  `Camera Uploads/` folder (or the whole tree, filtered by date/EXIF) without
  re-authing or standing up its own access.
- **Dependency note:** do the Dropbox-remote setup early (it's a P0/P2 artifact
  here) so the recovery hunt isn't blocked on it. Neither project should
  `--delete` or mutate Dropbox — both treat it strictly read-only, which is
  already this plan's cutover-safety rule.

## 10. Out of scope

- Collaborative office suite (Collabora / OnlyOffice) integration — a follow-up
  once the file layer is stable.
- Migrating **photos** into Nextcloud — photos stay in Immich; this project is
  the document/file working set only.
- Mobile (iOS/Android) Nextcloud client rollout — trivial once the server is up,
  not gated by this plan.
- Merging/flattening the `Dropbox/` + `GoogleDrive/` trees into one canonical
  layout — deliberately deferred to a post-migration cleanup (dedup report
  first), same "layout later" discipline as the hestia-SOT plan.
- Full teardown of the Google account (email, etc.) — only Drive/storage is in
  scope.

## 11. PRs (this plan)

- **PR 1 (this doc)** — the plan itself; no changes to running systems.
- **PR 2 (P1a)** — ZFS dataset + NFS PV manifest + CNPG `Cluster` + ObjectStore
  + ScheduledBackup (clone of `apps/production/immich/{database,objectstore,scheduledbackup,nfs-photos}.yaml`).
- **PR 3 (P1b)** — Nextcloud app: `apps/base/nextcloud/` + `apps/production/nextcloud/`
  (Deployment, config PVC, Service, PDB, HTTPRoute), wired into
  `apps/production/kustomization.yaml`; SOPS secret `.example`s.
- **PR 4 (P1c)** — Authelia `nextcloud` OIDC client + Nextcloud `user_oidc`
  config + Cloudflare tunnel ingress entry for `cloud.burntbytes.com` (the one
  WAN-exposing change, isolated for clean revert), + optional Redis.
- **P2–P6** — operator-run rclone + `occ` + snapshot/second-copy config on
  hestia; captured here as the runbook, no manifest PRs except the
  homelabscope monitoring hook.

Each PR follows the branch+PR convention; `kustomize build` must pass for every
affected overlay before merge.
