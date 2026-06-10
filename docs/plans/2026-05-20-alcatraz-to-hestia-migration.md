---
status: in-progress
last_modified: 2026-05-21
summary: "Migrate non-photo data (~870 GiB iSCSI + 3 TiB NFS media) off alcatraz onto hestia ZFS"
---

# Migrate non-image data: alcatraz → hestia (+ photo backup)

## Context

Alcatraz (Synology DS-class NAS, 10.42.2.11, BTRFS on /volume1) is currently the primary block-storage backend for ~870 GiB of Kubernetes PVCs across 75 PVCs/37 namespaces, plus the read-only NFS source for 3 TiB of Jellyfin/Navidrome media and the read-write NFS source for 5 TiB of Immich photos. Hestia (TrueNAS SCALE 26.x, 10.42.2.10, ZFS on `main`, ~21 TiB free, EPYC 8324P + DDR5) already runs democratic-csi (deployed 21 days ago) and exposes three TrueNAS-backed storage classes (`truenas-iscsi`, `truenas-iscsi-ephemeral`, `truenas-iscsi-ssd`), but is currently underutilized.

The migration goals are:

1. **Eliminate alcatraz as a block-storage dependency** so the cluster runs entirely on hestia ZFS storage (lower-power, ECC, snapshottable, compression-friendly). Block-storage move only — NOT the 5 TiB Immich photo library, which stays on alcatraz NFS as the primary copy.
2. **Move the Jellyfin/Navidrome NFS media** (3 TiB of movies/TV/anime/music) to hestia datasets, where snapshots and the broader filesystem feature set are richer than Synology BTRFS.
3. **Establish a durable, snapshot-backed backup** of the Immich photo library from alcatraz → hestia, providing point-in-time recovery without changing the primary read path. Photos remain authoritative on alcatraz; hestia becomes a daily-refreshed backup with deep snapshot retention.

User intent confirmed via clarifying questions:
- Discard Loki logs (40 GiB) + Immich model-cache (20 GiB) — both regenerate from scratch and aren't worth a migration window.
- Migrate immich-upload-pvc (500 GiB) — it's not a temp/staging area, it's active user uploads.
- Decommission alcatraz LUNs immediately after each app's successful cutover (no parachute period).
- Sequencing: all staging first, then all prod (conservative — full staging soak before any prod risk).
- Phase 2 backup: rsync pull + ZFS snapshots on hestia (no Restic complexity for now).

---

## Scope

**In scope (Phase 1):**
- All 75 PVCs using `synology-iscsi` storage class → re-provision on `truenas-iscsi`
- 4 of 5 static NFS PVs from alcatraz (jellyfin movies, jellyfin tv-shows, jellyfin tv-anime, navidrome music) → repoint to hestia
- Decommission of alcatraz iSCSI LUNs after per-app cutover succeeds
- Removal of `synology-csi` controller after all consumers migrate
- Removal of `synology-iscsi-monitor` (alcatraz-specific) after no PVCs remain

**In scope (Phase 2):**
- TrueNAS dataset + NFS mount path for backup destination
- SSH key trust hestia → alcatraz (or reverse — see plan)
- Hestia-side cron-driven rsync of `/volume1/family/images/photos` → hestia ZFS dataset
- ZFS snapshot retention policy on the backup dataset (14d daily / 8w weekly / 12m monthly)
- Prometheus exporter to surface backup freshness + size delta to existing dashboards

**Out of scope:**
- Immich photo PRIMARY storage path (stays on alcatraz NFS — user request)
- Loki (40 GiB) and immich-model-cache (20 GiB) — discarded, not migrated
- 3-2-1 off-site backup of photos — explicit phase 3 / future work
- Synology DSM-side service migration (Synology Photos app, Synology Drive — those stay on alcatraz alongside the photo library)
- Synology NAS decommissioning entirely — keeps running as the photo source and as the backup-source-of-truth

---

## Inventory snapshot (2026-05-20)

| Category | Count | Total Size | Storage |
|---|---|---|---|
| iSCSI PVCs to migrate | 73 (75 − 2 discarded) | ~810 GiB | synology-iscsi → truenas-iscsi |
| iSCSI PVCs to discard | 2 (Loki, immich-model-cache) | ~60 GiB | — |
| NFS PVs to migrate | 4 of 5 | ~3 TiB (1 TiB × 3 + 1 TiB music) | alcatraz NFS → hestia NFS |
| NFS PV to keep on alcatraz | 1 | ~5 TiB | immich-photos (Phase 2 backup target) |

Largest individual PVCs to plan around:
- `immich-prod/immich-upload-pvc` — 500 GiB (single largest iSCSI PVC; needs pre-staged copy)
- `monitoring/storage-loki-0` — 40 GiB (DISCARD)
- `immich-stage/immich-upload-pvc` — 100 GiB
- `immich-prod/immich-model-cache-pvc` — 20 GiB (DISCARD)

---

## Critical files referenced

| File | Purpose |
|---|---|
| `apps/base/jellyfin/media/nfs-media.yaml` | 3 jellyfin NFS PVs — update `nfs.server` from `10.42.2.11` to `10.42.2.10`, update paths to hestia exports |
| `apps/production/navidrome/nfs-music.yaml` | navidrome music NFS PV — same |
| `apps/production/immich/nfs-photos.yaml` | **DO NOT TOUCH** (stays on alcatraz) |
| `infra/controllers/democratic-csi/values.yaml` | TrueNAS storage classes already defined — `truenas-iscsi` is the migration target |
| `infra/controllers/synology-csi/` | Remove from cluster after migration completes |
| `apps/base/<app>/storage.yaml` (where present) | PVC manifests — switch `storageClassName: synology-iscsi` → `truenas-iscsi` |
| CNPG cluster manifests under `apps/{base,production,staging}/<app>/` | `spec.storage.storageClass` field swap |
| `apps/base/synology-iscsi-monitor/` | Remove after migration |
| `apps/base/homepage/services.yaml` + `apps/production/homepage/services.yaml` | Remove `10.42.2.11:5001` DSM tile reference (optional, post-Phase-1) |

---

## Phase 1 — Migrate non-photo data off alcatraz

Operates strictly **staging-first**: every staging namespace migrates and soaks (~1 week) before any prod namespace is touched. Per-environment cutover is risk-graduated within each environment: discardables first (cheap), CNPG re-bootstraps next (operator-driven), small config PVCs (straightforward kubectl-pv-migrate), then NFS media (background rsync), then the 500 GiB immich-upload-pvc last (longest copy window).

### Phase 1.0 — Pre-flight (one-time, before any staging cutover)

**Hestia setup:**
1. Create ZFS datasets on hestia for NFS media. Suggested layout:
   ```
   main/media/movies   (1.5 TiB quota — current 1 TiB + headroom)
   main/media/tv-shows (1.5 TiB)
   main/media/tv-anime (1.5 TiB)
   main/media/music    (1.5 TiB)
   ```
   Compression on (lz4 default), `recordsize=1M` (large media files), `atime=off`.
2. Configure NFS exports for the cluster's Lab VLAN (`10.42.2.0/24`):
   - `/mnt/main/media/movies` → `rw=10.42.2.0/24,sync,no_subtree_check`
   - Match for tv-shows, tv-anime, music
   - Use the same `nfsvers=3, rsize=1048576, wsize=1048576, hard, timeo=600, retrans=2, nolock` mount options as the existing alcatraz exports (see `apps/base/jellyfin/media/nfs-media.yaml:20-27`).
3. Verify cluster nodes can mount hestia NFS exports:
   ```
   kubectl run nfs-test --rm -it --image=alpine -- sh -c 'apk add nfs-utils && mount -t nfs 10.42.2.10:/mnt/main/media/movies /mnt'
   ```
4. Pre-stage tooling on hestia: `apt install rsync screen pv` (or equivalent).

**Cluster setup:**
1. Install `kubectl-pv-migrate` (Krew plugin) on this Mac. This is the canonical tool for "copy contents of PVC A → PVC B" across storage classes. Used per the cutover steps below.
2. Verify `truenas-iscsi` SC is healthy: create a 1 GiB test PVC, attach a busybox, write+read, delete.
3. Verify the truenas-api-proxy deployment is stable (it's how democratic-csi talks to TrueNAS WebSocket API). Reference: `infra/controllers/democratic-csi/truenas-api-proxy.yaml`.

**Repo prep (one PR):**
- Branch `migrate/alcatraz-to-hestia-phase-1-prep`
- Adds the plan document under `docs/plans/2026-05-20-alcatraz-to-hestia-migration.md` (this file, transplanted from the internal plan)
- No app changes yet

### Phase 1.1 — Staging cutover

**Order within staging (12 namespaces with synology-iscsi PVCs):**

1. **Discardables first** — delete Loki PVC + immich-stage model cache. Loki: scale loki StatefulSet to 0, delete `storage-loki-0` PVC, scale back to 1 — fresh start, no migration. Immich stage model-cache: delete the PVC and let immich-ml recreate it on truenas-iscsi (PVC manifest already updated in same PR).

2. **CNPG staging clusters** — golinks-stage, linkding-stage, memos-stage, vitals-stage, immich-stage (5 clusters):
   - PR 1: change `spec.storage.storageClass: synology-iscsi → truenas-iscsi` in each Cluster CR
   - Merge → Flux reconciles → CNPG operator notices storage class change → re-bootstraps replicas one at a time from primary via pg_basebackup (uses the same mechanism we used earlier to fix the flashcards-stage timeline divergence)
   - Per-cluster: `kubectl cnpg destroy <cluster> <instance-id>` for each replica to force rebuild
   - Verify cluster goes Ready=True before moving to next
   - 5 staging CNPG clusters × ~5 min each = ~25 min total operator time

3. **App config PVCs (small, staging)** — adguard-stage, audiobookshelf-stage, authelia-stage, hermes-stage, hermes-callee-stage, homeassistant-stage, jellyfin-stage, linkding-stage (data PVC), mealie-stage, memos-stage (data PVC), navidrome-stage, openwebui-stage, signal-cli-stage, snapcast-stage:
   - Per-PVC procedure using `kubectl-pv-migrate`:
     ```
     # 1. Scale workload to 0
     kubectl scale deploy/<app> -n <ns> --replicas=0

     # 2. Create new PVC on truenas-iscsi (separate manifest temporarily, or use pv-migrate's dest-create mode)
     # 3. Run pv-migrate
     kubectl pv-migrate migrate <old-pvc> <new-pvc> -n <ns>

     # 4. Update Deployment spec → reference new PVC name
     # 5. Delete old PVC (Retain policy keeps the PV around; that's fine)
     # 6. Scale workload back up
     ```
   - Alternative (simpler, GitOps-native): rename the new PVC to the old PVC's name, delete the old PVC's manifest from the repo, let Flux create the new one with the old name. Avoids needing to touch Deployment manifests. Tradeoff: pv-migrate needs both PVCs to exist concurrently with different names, so this is a 3-step PR sequence per app.
   - Suggested batching: do 3-5 staging apps per PR to keep blast radius small.

4. **NFS media (staging)** — jellyfin-stage uses the same PVs as jellyfin-prod (`jellyfin-movies-pvc`, etc.), so this happens in step 6 below as a single environment-spanning change.

5. **Immich-stage upload PVC** (100 GiB):
   - Run `kubectl-pv-migrate` in background; for 100 GiB on the LAN expect ~25–45 min
   - Stage immich pods scale to 0 during the copy
   - Cut over, scale up, verify uploads work

6. **Per-cutover decommission** — after each app's pod stabilizes on TrueNAS storage:
   - Capture the old Synology LUN ID before deleting the PV (`kubectl get pv <name> -o yaml | grep -i synology`)
   - Delete the orphaned PV in cluster
   - Delete the LUN on Synology via DSM UI or API (clears space immediately)

**Staging soak: 1 week.** Run normal app traffic, verify backups still complete (CNPG → S3 unaffected; backups are app-namespace-aware, not storage-class-aware), check Grafana dashboards for I/O latency regressions on TrueNAS-backed pods. The TrueNAS-backed Postgres clusters from earlier in this conversation are already proving the latency is acceptable.

### Phase 1.2 — Production cutover (mirror of 1.1)

Identical order to staging, applied to the 24 prod namespaces. Critical differences:

- **Coordination window**: stage each app cutover during expected low-traffic windows (early morning UTC).
- **immich-prod upload PVC (500 GiB)** is the longest single operation in this phase. Plan for a 2–4 hour copy window. Schedule for a weekend night. Use `kubectl-pv-migrate` with `--strategy=mnt2` (mount both, rsync between mounts) — fastest path. Immich uploads pause during the cutover; new uploads queue at the client and retry.
- **CNPG production clusters** (golinks-prod, linkding-prod, memos-prod, vitals-prod, immich-prod) — re-bootstrap one cluster at a time. Each cluster's S3 backup is unaffected (Barman Cloud Plugin is storage-agnostic, points at S3).
- **NFS media cutover** — pre-copy via rsync (run for days in background while alcatraz is still authoritative), then atomic swap:
  ```bash
  # Background, run on hestia, days before cutover:
  rsync -avh --progress --delete \
    /mnt/alcatraz-mount/family/video/movies/ /mnt/main/media/movies/
  # (repeat for tv-shows, tv-anime, music; navidrome music is RW so use --delete carefully)
  ```
  After initial sync, run a final delta sync immediately before swapping the PV manifests. PR updates `apps/base/jellyfin/media/nfs-media.yaml` and `apps/production/navidrome/nfs-music.yaml`:
  - `nfs.server`: `10.42.2.11` → `10.42.2.10`
  - `nfs.path`: `/volume1/family/video/movies` → `/mnt/main/media/movies` (etc.)
  - Delete the old PV first (Retain policy keeps the data on alcatraz), then apply the new PV
- **Decommission** continues immediately per-app.

### Phase 1.3 — Cleanup

After all 73 PVCs have migrated and all NFS PVs have repointed:
1. **Remove `synology-csi` controller** from cluster (PR deletes `infra/controllers/synology-csi/`).
2. **Remove `synology-iscsi-monitor`** (PR deletes `apps/base/synology-iscsi-monitor/`).
3. **Remove the alcatraz DSM tile** from homepage (`apps/production/homepage/services.yaml`).
4. **Update `docs/architecture/networking/README.md`**: alcatraz is no longer a block-storage backend, just the photo library + NFS source. The "Synology iSCSI" cross-service dependency in `AGENTS.md` is now stale and should be removed.
5. **Update `~/.claude/HOMELAB.md`**: alcatraz role description.

### Phase 1.4 — Migrate qBittorrent off alcatraz onto hestia

Independent of the in-cluster Kubernetes data migration, qBittorrent currently runs **on alcatraz** (Synology, as a Container Manager / Docker container) and is the consumer of the only documented WAN port-forward exception (TCP+UDP 9854 → 10.42.2.11; see `docs/architecture/networking/README.md` invariant 3 and PR #710). After this phase, qBittorrent runs as a **TrueNAS SCALE Custom App** on hestia, the port-forward repoints to 10.42.2.10, and alcatraz is left with only the Synology Photos library + 5 TiB NFS export.

Motivation:
- Consolidates the alcatraz dependency exception: today the perimeter forward exists *because* alcatraz hosts qBit; moving qBit to hestia means the alcatraz host has zero inbound exposure (it only serves NFS to LAN).
- Aligns with the "TrueNAS Custom Apps preferred" convention for hestia services (compose YAML in repo at `hosts/hestia/qbittorrent/`, operator copy/pastes into TrueNAS UI).
- Puts qBit alongside the Jellyfin media datasets created in Phase 1.0 — the qBit "Downloads" path can write directly into `main/media/movies` / `main/media/tv-shows`, eliminating the cross-host SMB/SCP hop that exists today for "snatched torrent → Jellyfin library."
- Reduces alcatraz responsibilities to a single role (photo NAS), simplifying the Phase 2 backup story.

**Steps:**

1. **Datasets on hestia (operator, TrueNAS UI):**
   - `main/apps/qbittorrent/config` (quota 5 GiB, lz4, `recordsize=128K`) — settings + .fastresume state
   - `main/apps/qbittorrent/downloads-incomplete` (quota 50 GiB, lz4, `recordsize=1M`) — in-progress downloads (separate from media datasets to avoid Jellyfin scanning partial files)
   - The completed-download target reuses `main/media/movies` / `main/media/tv-shows` / etc. that Phase 1.0 already created.

2. **Compose YAML in repo** at `hosts/hestia/qbittorrent/docker-compose.yaml` (the canonical source; operator copies to TrueNAS Custom App UI):
   - Image: `lscr.io/linuxserver/qbittorrent:5.x` (pinned digest)
   - Web UI: `0.0.0.0:8090`
   - Peer port: `9854` (TCP + UDP, host-port mapped so it's visible to the LAN)
   - Mounts: `config` → `/config`, `downloads-incomplete` → `/downloads-incomplete`, the four media datasets read-write under `/downloads/{movies,tv-shows,tv-anime,music}`
   - `PUID`/`PGID` aligned with the dataset ACLs

3. **Migrate config + seed state (one-time, with brief downtime):**
   - Pause qBit on alcatraz (Web UI → "Pause all").
   - Copy the entire alcatraz qBit data directory (typically `/volume1/docker/qbittorrent/config/qBittorrent/`) including `BT_backup/` (.fastresume + .torrent) and the SQLite DB to `main/apps/qbittorrent/config/` on hestia. Use rsync over SSH.
   - **Critical**: also copy the actual downloaded payloads to the same paths the new qBit will look for them. If existing alcatraz paths were `/downloads/movies/X.mkv`, the new hestia container needs them at `/downloads/movies/X.mkv` (which maps to `main/media/movies/X.mkv`). The `.fastresume` files have absolute paths baked in — if the paths don't match exactly, qBit will mark torrents as "Missing files" and lose the seed history.

4. **Cutover:**
   - Stop qBit on alcatraz.
   - Start the hestia Custom App.
   - Verify Web UI at `http://10.42.2.10:8090/` (LAN access).
   - Verify all torrents transition from "Stalled UP" → either Seeding/Stalled with the new port (no "Missing files" errors).

5. **UniFi port-forward repoint:**
   - Update the existing 9854 TCP+UDP forward rule: change the "Forward IP" from `10.42.2.11` to `10.42.2.10`. Keep port (9854), name, protocol.
   - Verify externally with yougetsignal: `9854 TCP/UDP` open on `67.180.218.139` → resolves to hestia.
   - Verify qBit's connection-status flips to `connected` (green firewall icon).

6. **Update docs:**
   - `docs/architecture/networking/README.md` (invariant 3 exception): change `alcatraz (Synology NAS, 10.42.2.11)` → `hestia (TrueNAS, 10.42.2.10)` in the qBittorrent port-forward exception paragraph.
   - `docs/operations/apps/jellyfin-library-picks.md` references — none direct, but the user-facing context shifts.
   - Add a runbook section to (new) `docs/operations/apps/qbittorrent.md`.

7. **Decommission qBit on alcatraz:**
   - Stop the Container Manager / Docker container.
   - Delete the container + its docker-compose file (keep the data dir on alcatraz for 7 days as rollback; then `rm -rf /volume1/docker/qbittorrent/`).

**Risks:**

| Risk | Mitigation |
|---|---|
| Path mismatch between alcatraz and hestia destroys seed history (`.fastresume` has absolute paths) | Pre-cutover dry-run: copy one torrent's payload, start qBit pointing at the new path, verify it loads as Seeding before bulk-copying |
| Cutover window with no qBit available leaves PTP ratio without a seeder | Pause qBit on alcatraz (don't stop) during config copy; only kill the alcatraz container *after* hestia qBit is verified working |
| Brief port-forward gap (UniFi rule update is atomic, but the new instance must be listening first) | Bring up hestia qBit before changing the UniFi rule; double-listen for ~30 seconds is harmless |
| Downloaded media access by Jellyfin breaks if directory layout changes | Media datasets already exist with the canonical layout from Phase 1.0; qBit's `/downloads/` mapping mirrors that layout one-to-one |

**Success criteria:**
- [ ] qBit Web UI reachable at `http://10.42.2.10:8090/` from LAN
- [ ] All previously-snatched torrents show as Seeding (not "Missing files")
- [ ] External port check (`yougetsignal`) returns "open" for `9854` against the WAN IP, routed to hestia
- [ ] qBit `connection_status` API returns `connected`
- [ ] Alcatraz has zero open inbound ports from the UCG-Fiber perimeter
- [ ] Jellyfin still successfully imports newly-completed torrents

This phase can be executed independently of the in-cluster migration (it touches different infrastructure: TrueNAS Custom Apps + UniFi config, not Kubernetes manifests). Recommended ordering: do it after Phase 1.2 completes (production iSCSI is fully on hestia), so all the hestia ZFS pool stress-testing has happened before qBit's I/O pattern lands on it.

---

## Phase 2 — Durable photo backup (alcatraz → hestia)

The Immich photo library (`/volume1/family/images/photos`, ~5 TiB) stays primary on alcatraz; this phase establishes a daily-refreshed mirror on hestia with deep snapshot retention. **No change to Immich's read path** — Immich keeps mounting alcatraz NFS. The hestia copy is purely a backup.

### Phase 2.1 — Backup dataset + retention

1. Create dataset `main/backups/immich-photos` on hestia.
   - Compression: `lz4` (default — photos are mostly JPEG so compression ratio is low, but zero cost when CPU is idle)
   - `recordsize=1M` (photos are large)
   - `atime=off`
   - Quota: 8 TiB (allows growth + snapshot overhead)
2. Configure ZFS auto-snapshot policy (use `zfs-auto-snapshot` or TrueNAS's built-in periodic snapshot task — TrueNAS UI is cleanest):
   - Daily snapshots, retain 14 days
   - Weekly snapshots, retain 8 weeks
   - Monthly snapshots, retain 12 months
3. Verify TrueNAS periodic-snapshot task is scheduled to run *after* the daily rsync completes.

### Phase 2.2 — Pull configuration

The "hestia pulls from alcatraz" direction is preferred over "alcatraz pushes to hestia":
- Keeps schedule + retention in one place (hestia)
- Alcatraz has fewer config touches (just needs to allow SSH from hestia and have rsync installed)
- If hestia is down, alcatraz keeps serving photos as usual (no impact on the primary read path)

Steps:
1. Generate SSH keypair on hestia (`ssh-keygen -t ed25519`).
2. Add the public key to alcatraz's authorized_keys (DSM → Control Panel → Terminal & SNMP → enable SSH → DSM admin account, restricted to specific commands via `command="..."` prefix in `authorized_keys` if practical).
3. From hestia, verify: `ssh truenas_admin@10.42.2.11 'ls /volume1/family/images/photos | head'`.
4. Initial seed (one-time, manual, run inside `screen`):
   ```bash
   # On hestia, take ~12 hours over gigabit for 5 TiB
   nice -n 19 rsync -avh --progress \
     --rsh="ssh -T -c chacha20-poly1305@openssh.com -o Compression=no -x" \
     truenas_admin@10.42.2.11:/volume1/family/images/photos/ \
     /mnt/main/backups/immich-photos/
   ```

### Phase 2.3 — Scheduled incremental sync

1. Create a hestia cron job at 04:00 daily (after CNPG S3 backups complete at 02:00):
   ```cron
   0 4 * * *  /usr/local/bin/immich-photos-backup.sh >>/var/log/immich-photos-backup.log 2>&1
   ```
2. Script `/usr/local/bin/immich-photos-backup.sh`:
   ```bash
   #!/bin/bash
   set -euo pipefail
   exec > >(tee -a /var/log/immich-photos-backup.log)
   exec 2>&1
   echo "=== $(date -u +%FT%TZ) START ==="
   rsync -avh --delete \
     --rsh="ssh -T -c chacha20-poly1305@openssh.com -o Compression=no -x" \
     --stats \
     truenas_admin@10.42.2.11:/volume1/family/images/photos/ \
     /mnt/main/backups/immich-photos/
   echo "=== $(date -u +%FT%TZ) END ==="
   ```
3. Commit the script source to `homelab/hosts/hestia/backup/immich-photos-backup.sh` so it's version-controlled. The operator copies it to hestia (operator-only step — no automated push to hestia).

### Phase 2.4 — Monitoring

1. Expose backup freshness as a Prometheus metric via the textfile collector on hestia (already running via node-exporter / IPMI exporter from earlier work):
   ```bash
   # End of backup script:
   echo "immich_photos_backup_last_success_seconds $(date +%s)" \
     > /var/lib/node-exporter/textfile/immich-backup.prom.tmp
   mv /var/lib/node-exporter/textfile/immich-backup.prom{.tmp,}
   ```
2. Add a Prometheus alert rule (in `infra/controllers/monitoring/` rules):
   ```yaml
   - alert: ImmichPhotoBackupStale
     expr: time() - immich_photos_backup_last_success_seconds > 36 * 3600
     for: 10m
     labels:
       severity: warning
     annotations:
       summary: "Immich photo backup hasn't completed in >36h"
   ```
   36h tolerates one missed daily run before alerting.
3. Add a Grafana panel to the existing hestia dashboard showing:
   - Last backup timestamp (single-stat)
   - Backup dataset size over time
   - Snapshot count + total snapshot storage on `main/backups/immich-photos`

---

## Verification

**Phase 1 success criteria (per-app, then aggregate):**
- [ ] No PVCs reference `synology-iscsi` storage class (`kubectl get pvc -A -o wide | grep -c synology-iscsi` returns 0)
- [ ] No NFS PVs reference `10.42.2.11` except `immich-photos-pv-prod` (`kubectl get pv -o yaml | grep -A2 "server: 10.42.2.11"` shows only Immich photos)
- [ ] All 5 CNPG clusters report `Ready=True, ContinuousArchiving=True` and have current S3 backups
- [ ] Jellyfin library scan completes without I/O errors on hestia NFS
- [ ] Navidrome library scan + playback verified
- [ ] Immich upload flow: end-to-end test (upload a photo via mobile app, confirm it lands on alcatraz NFS path)
- [ ] No CNPG instance is hosted on the alcatraz-backed PVC (LUNs deleted)
- [ ] Alcatraz DSM "iSCSI Manager → Target/LUN" page shows zero k8s-csi-pvc-* LUNs

**Phase 2 success criteria:**
- [ ] Initial seed completed, `du -sh /mnt/main/backups/immich-photos` matches alcatraz photo dir to within 1% (file count + bytes)
- [ ] First incremental cron run completes in <30 min (delta-only sync)
- [ ] ZFS snapshots present on backup dataset (`zfs list -t snapshot main/backups/immich-photos`)
- [ ] Grafana shows "last successful backup" timestamp updating daily
- [ ] Test restore: pick a random photo file, restore from a snapshot, byte-compare to source — passes
- [ ] Prometheus alert `ImmichPhotoBackupStale` is loaded and visible in Alertmanager (doesn't fire under healthy state)

---

## Risks and rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| Hestia ZFS pool fills | Low | Set explicit per-dataset quotas (1.5 TiB media, 8 TiB photo backup); monitor with existing node-exporter |
| CNPG re-bootstrap fails mid-cluster | Medium | One cluster at a time; CNPG operator handles single-instance failures; S3 PITR backup as last resort |
| Hestia iSCSI provisioning bug surfaces under load | Medium | Phase 1.0 includes pre-flight test; staging soak surfaces issues before prod; rollback is "leave PVC on synology-iscsi" until investigated |
| 500 GiB Immich upload PVC copy takes >4h, exceeds maintenance window | Medium | Pre-stage with rsync in background (Immich uploads on alcatraz NFS path stay live), then short atomic swap |
| Hestia → alcatraz SSH connectivity breaks (Phase 2) | Low | Prometheus alert fires after 36h; backup script logs to `/var/log/immich-photos-backup.log`; manual rsync invocation as fallback |
| Alcatraz NFS export config drifts (DSM update changes exports) | Low | Phase 2 doesn't change alcatraz exports; only consumes them. DSM updates would also affect Immich primary read, surfaced immediately |
| Decommissioned LUN reclamation prevents rollback | Accepted (user choice) | User explicitly chose immediate decommission; rollback is "restore from CNPG S3 backup" or "re-rsync from hestia copy" |

**Rollback granularity is per-app.** Staging cutover establishes confidence; if a specific app misbehaves on TrueNAS storage, the rollback for THAT app is a kubectl-pv-migrate in reverse (TrueNAS PVC → fresh Synology PVC) — but this requires keeping `synology-iscsi` SC available until staging soak completes. Plan keeps the SC alive through Phase 1.3.

---

## Open items for the implementing PR(s)

- **PR 1 (this plan)**: Write the plan to `docs/plans/2026-05-20-alcatraz-to-hestia-migration.md`. Add the index entry to `docs/plans/README.md`.
- **PR 2 (Phase 1.0 pre-flight)**: Hestia ZFS dataset creation (manifest in `hosts/hestia/`); pre-flight test artifacts.
- **PR 3..N (Phase 1.1 staging cutovers)**: One PR per ~3-5 apps batched, each batch: PVC manifest changes + (optional) namespace-scoped migration Job manifest if not using local pv-migrate.
- **PR (N+1)..(M) (Phase 1.2 prod cutovers)**: Same shape as staging PRs but per-prod-app.
- **PR M+1 (Phase 1.3 cleanup)**: Remove `synology-csi`, `synology-iscsi-monitor`, DSM tile from homepage. Update `AGENTS.md`, `docs/architecture/networking/README.md`, `HOMELAB.md`.
- **PR M+2 (Phase 2 backup infrastructure)**: Hestia backup script source + cron entry + Prometheus rule + Grafana dashboard panel.

Each PR follows the existing branch+PR convention; CI gates (`kustomize build`) must pass before merge.
