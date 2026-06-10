# Hestia as SOT for family/ + homes/

Status: draft
Last modified: 2026-06-01

## Architecture goal

Make hestia the **source of truth** for everything currently under `alcatraz:/volume1/family/*` and `alcatraz:/volume1/homes/*`. Alcatraz keeps its role as the **phone-photo upload target** and a passive **secondary copy** (snapshotted independently). Daily delta-sync pulls phone uploads into hestia; everything that reads this data (Immich, Jellyfin, future SMB shares, future backups) reads from hestia, not alcatraz.

```
phones  ──upload──▶  alcatraz:/volume1/family/images/photos
                              │
                              │ daily rsync 04:00
                              ▼
                       hestia:/mnt/main/family/...   ←── Immich, Jellyfin, etc.
                              │
                              │ ZFS snapshots (daily/14, weekly/8, monthly/12)
                              ▼
                       hestia self-redundancy
```

Why hestia-as-SOT: simpler reads (CSI + NFS both terminate on hestia), unified snapshot policy under TrueNAS, alcatraz becomes a single-purpose phone target with no production dependents.

## Inventory (measured 2026-06-01)

`/volume1/family/` — 1.6 TB total, breakdown:

| Subdir | Size | Migration disposition |
|---|---|---|
| `admin/` | 55 MB | rsync to `/mnt/main/family/admin/` |
| `audio/` | 33 GB | rsync to `/mnt/main/family/audio/` |
| `documents/` | 320 MB | rsync to `/mnt/main/family/documents/` |
| `images/` | 425 GB | **already on hestia** at `/mnt/main/backups/immich-photos/`. Rename ZFS dataset → `/mnt/main/family/images/photos/`. Top-up rsync to catch any drift since last sync. |
| `literature/` | 11 GB | rsync to `/mnt/main/family/literature/` |
| `misc/` | 29 MB | rsync to `/mnt/main/family/misc/` |
| `projects/` | 315 MB | rsync to `/mnt/main/family/projects/` |
| `video/` | 1.2 TB | **skip in this plan** — already on hestia at `/mnt/main/media/{movies,tv-shows,tv-anime}/`. Follow-up plan covers the consolidation (see Future plans). |
| `#recycle/` | 2 GB | skip (Synology Recycle Bin) |
| `@eaDir/` | 8 KB | skip |

`/volume1/homes/` — ~425 GB total, breakdown:

| User | Size | Migration disposition |
|---|---|---|
| `george/` | 173 GB | rsync to `/mnt/main/homes/george/` |
| `mara/` | 252 GB | rsync to `/mnt/main/homes/mara/` |
| `manager/` | 41 MB | rsync (DSM admin's home; small but include for completeness) |
| `admin/`, `docker/`, `melodic-muse-app/` | empty | skip — nothing to migrate |
| `truenas-backup/` | 4 KB | skip (the rsync agent's own home; meaningless on hestia) |

**Net-new data to migrate ≈ 470 GB.** Plus the in-place rename of the 425 GB photos dataset (no copy, just `zfs rename`). Wall-clock estimate: ~2-3 hours over gigabit for the new content.

## In scope

- `alcatraz:/volume1/family/{admin, audio, documents, images, literature, misc, projects}/` → `hestia:/mnt/main/family/{...}/`
- `alcatraz:/volume1/homes/{george, mara, manager}/` → `hestia:/mnt/main/homes/{...}/`
- Rename existing `main/backups/immich-photos` dataset → `main/family/images/photos` (preserves snapshots; updates daily sync script's DST; repoints Immich NFS PV)

Excluded by rsync:
- `@eaDir/` (Synology indexer metadata)
- `.DS_Store` (macOS metadata)
- `Thumbs.db` (Windows metadata)
- `#recycle/` (Synology Recycle Bin) — known harmless I/O errors on stale entries here, excluding it sidesteps them
- `*.tmp`, `*.part` (in-flight transfer artifacts)

## Out of scope (explicit non-goals for this plan)

- **Layout improvements.** The existing alcatraz tree (`family/images/photos`, `family/video/movies`, etc.) carries forward as-is. Follow-up plans address consolidation (e.g., flattening `family/images/photos` → top-level `photos/`, or unifying `family/video/*` with the existing `/mnt/main/media/*` Jellyfin mounts).
- **Decommissioning alcatraz.** Alcatraz keeps running as phone-upload target + secondary copy. A future plan covers the full retire decision once hestia has been the SOT long enough to trust.
- **/volume1/photo/ migration.** Niccolo + Wedding folders go in a separate one-shot plan; they're a different logical collection and not on the daily sync path.
- **Reverse-direction backup** (hestia → alcatraz). Phase 1 keeps current direction. Phase 2 may add a reverse mirror if we want alcatraz as a true offsite-style backup of hestia's SOT.

## Phase 1 — provision destination (operator + agent, ~10 min)

1. **DSM ACL grants on alcatraz** (operator, one-time): `truenas-backup` already has Read on the `family` shared folder root (verified 2026-06-01 — `ls /volume1/family/` works). Need to grant Read on:
   - `homes` shared folder (currently truenas-backup can list the parent but most user homes return empty totals to it, suggesting partial denial)
   - `photo` shared folder (already granted in a prior session)

   In DSM: Control Panel → Shared Folder → for each → Edit → Permissions → set truenas-backup = Read → Save.

   Verify:
   ```bash
   ssh truenas_admin@10.42.2.10 \
     'ssh -i ~/.ssh/id_ed25519_alcatraz -o UserKnownHostsFile=~/.ssh/known_hosts truenas-backup@10.42.2.11 \
        "du -sh /volume1/family/* /volume1/homes/* 2>&1 | grep -v Permission"'
   ```
   All subdirs should show real sizes (not zero, not denied).

2. **Two new ZFS datasets via midclt** (agent can do via SSH to hestia):
   - `main/family` — compression=lz4, recordsize=1M, atime=off, quota=2T (covers current 1.6T minus skipped video/recycle/eaDir = ~470 GB net-new + 425 GB renamed photos + growth headroom)
   - `main/homes` — compression=lz4, recordsize=128K, atime=off, quota=1T (covers current 425 GB + ~2× headroom; smaller recordsize because home dirs have lots of small files where 1M wastes space)

3. **Three periodic-snapshot tasks per dataset** (agent via midclt):
   - daily/14 at 05:00
   - weekly/8 Sun at 05:30
   - monthly/12 1st at 06:00

   Same staggering as the existing `main/backups/immich-photos` snapshots — fires after the planned 04:00 daily rsync.

## Phase 2 — rename the existing photos dataset (agent, ~1 min)

The 425 GB of photos already on hestia at `/mnt/main/backups/immich-photos/` is the same content we want at `/mnt/main/family/images/photos/`. ZFS rename moves the mountpoint without copying any bytes and preserves snapshot history.

```bash
# Inside the new main/family dataset (created in Phase 1), make the parent path:
ssh truenas_admin@10.42.2.10 'midclt call pool.dataset.create \
  "{\"name\":\"main/family/images\",\"type\":\"FILESYSTEM\",\"atime\":\"OFF\",\"compression\":\"LZ4\"}"'

# Rename the existing dataset into the new location:
ssh truenas_admin@10.42.2.10 'midclt call -job pool.dataset.rename \
  "main/backups/immich-photos" \
  "{\"new_name\":\"main/family/images/photos\"}"'
```

After this:
- The dataset mountpoint changes from `/mnt/main/backups/immich-photos` → `/mnt/main/family/images/photos`
- Snapshots come along
- The `immich-photos-backup` container will fail its next 04:00 cron run (its compose still bind-mounts the old path). Phase 3 fixes that.

## Phase 3 — initial bulk sync of net-new content (operator-driven, ~2-3h)

For everything that isn't the already-migrated photos. Run from the existing `immich-photos-backup` container so the SSH key, host key, and rsync image are reused. Use `tmux` since this is long.

```bash
# On hestia, in tmux (~2h wall-clock for ~470 GB over gigabit):
sudo docker exec $(sudo docker ps -q --filter name=immich-photos-backup) \
  rsync -avh --info=progress2 \
        --exclude='@eaDir' --exclude='.DS_Store' --exclude='Thumbs.db' \
        --exclude='#recycle' --exclude='*.tmp' --exclude='*.part' \
        --exclude='video/'  \
        -e "ssh -i /root/.ssh/id_ed25519_alcatraz -o UserKnownHostsFile=/root/.ssh/known_hosts" \
        truenas-backup@10.42.2.11:/volume1/family/ \
        /mnt/main/family/

# Homes (separate so failure of one doesn't kill both):
sudo docker exec $(sudo docker ps -q --filter name=immich-photos-backup) \
  rsync -avh --info=progress2 \
        --exclude='@eaDir' --exclude='.DS_Store' --exclude='Thumbs.db' \
        --exclude='#recycle' --exclude='*.tmp' --exclude='*.part' \
        --exclude='admin/' --exclude='docker/' --exclude='melodic-muse-app/' --exclude='truenas-backup/' \
        -e "ssh -i /root/.ssh/id_ed25519_alcatraz -o UserKnownHostsFile=/root/.ssh/known_hosts" \
        truenas-backup@10.42.2.11:/volume1/homes/ \
        /mnt/main/homes/

# Top-up rsync the photos to catch any drift since the last 04:00 daily cron
# (alcatraz is still the SOT until Phase 5 cuts over):
sudo docker exec $(sudo docker ps -q --filter name=immich-photos-backup) \
  rsync -avh --info=progress2 --delete \
        --exclude='@eaDir' --exclude='.DS_Store' --exclude='Thumbs.db' \
        -e "ssh -i /root/.ssh/id_ed25519_alcatraz -o UserKnownHostsFile=/root/.ssh/known_hosts" \
        truenas-backup@10.42.2.11:/volume1/family/images/photos/ \
        /mnt/main/family/images/photos/
```

Don't run `--delete` on the bulk seeds (`family/` minus video, `homes/`) — the destination is new, nothing to delete, and `--delete` is risky during a first pull. The photos top-up DOES use `--delete` since that destination already matches and we want it to stay in sync with the alcatraz authoritative copy.

## Phase 4 — switch immich-photos-backup script to the new path + extend to full family/

The current `images/immich-photos-backup/immich-photos-backup.sh` syncs into `/mnt/main/backups/immich-photos/`. After Phase 2's rename, that path no longer exists — the dataset is now at `/mnt/main/family/images/photos/`. Without a script update, the next 04:00 cron fails.

PR changes:
- `images/immich-photos-backup/immich-photos-backup.sh`:
  - Change `DST` from `/mnt/main/backups/immich-photos/` → `/mnt/main/family/images/photos/`
  - Keep `--delete` (already present)
  - Keep all other flags (excludes, chacha20-poly1305 cipher, etc.)
- `hosts/hestia/immich-photos-backup/docker-compose.yml`:
  - Change bind mount from `/mnt/main/backups/immich-photos:/mnt/main/backups/immich-photos` → `/mnt/main/family:/mnt/main/family` (broader so the same container can also sync other family/ subdirs in the future)

After the build + digest-pin + deploy cycle, the cron at 04:00 fires against the new path.

**Stretch (optional, can land in same PR or follow-up):** extend the script to also sync the rest of `family/` (admin, audio, documents, literature, misc, projects) on the same schedule. Alcatraz isn't the SOT for those, but a daily safety mirror is cheap and means hestia stays current if any of those dirs get written from DSM-side apps.

The old `main/backups/immich-photos` dataset is empty after the rename — destroy it once Phase 5 is verified clean:
```bash
ssh truenas_admin@10.42.2.10 'midclt call pool.dataset.delete main/backups/immich-photos'
ssh truenas_admin@10.42.2.10 'midclt call pool.dataset.delete main/backups'  # if parent is empty
```

## Phase 5 — repoint Immich's NFS PV from alcatraz to hestia

Today Immich's `immich-photos-pv-prod` mounts NFS from `10.42.2.11:/volume1/family/images/photos`. After phases 1-3, hestia has the same content at `/mnt/main/family/images/photos/`. Same playbook as the Jellyfin NFS PV repoint (PR #765):

1. Update `apps/production/immich/nfs-photos.yaml`:
   - `nfs.server`: `10.42.2.11` → `10.42.2.10`
   - `nfs.path`: `/volume1/family/images/photos` → `/mnt/main/family/images/photos`
2. Stop Immich (`kubectl scale deploy immich-server -n immich-prod --replicas=0` etc. for all immich-* deploys mounting the PV)
3. Delete the old PV (Retain policy preserves the alcatraz NFS export — alcatraz is unchanged)
4. Apply the new PV via Flux (let it reconcile)
5. Scale Immich back up
6. Smoke test: open Immich web UI, browse a recent album, confirm files load

## Phase 6 — verification + retention period (~1 week soak)

Before treating hestia-SOT as durable:

- Verify daily rsync delta lands (compare `du -sh /volume1/family/images/photos` on alcatraz vs `du -sh /mnt/main/family/images/photos` on hestia — should be within 1% of each other).
- Verify ZFS snapshot tasks fire (`midclt call pool.snapshottask.query` shows recent successful runs, `zfs list -t snapshot main/family` shows entries).
- Verify Immich serves correctly from hestia (file load, album list, upload-from-mobile test).
- Verify no SMB/CIFS clients on the LAN still expect to read from alcatraz; if any do, repoint them too.

After ~1 week clean: hestia is confirmed SOT and the migration is complete. Alcatraz keeps its narrow role as phone-upload target + passive copy.

## Future plans (out of this scope, listed for context)

- **Video consolidation (Option C)**: `/volume1/family/video/` is 1.2 TB and ALREADY on hestia at `/mnt/main/media/*` (Jellyfin's source). This plan deliberately skips it to avoid duplicating 1.2 TB. The follow-up plan picks one of: move `main/media` → `main/family/video` + repoint Jellyfin PVs (clean canonical location, third PV repoint), OR ZFS clone (zero-copy but adds complexity), OR keep the divergence and accept the layout mismatch. Recommendation: do the rename + Jellyfin PV repoint — the playbook is well-trodden by now (Jellyfin NFS in PR #765, Immich NFS in Phase 5 of this plan).
- **Layout improvement PR**: flatten / reorganize the `family/` subtree on hestia for cleaner naming (e.g., `photos/family/`, `documents/`, etc.). Keep alcatraz layout for migration simplicity here, restructure once stable.
- **Reverse-direction backup**: hestia → alcatraz daily, so alcatraz becomes the offsite-style backup. Pairs nicely with the "alcatraz is phone target" role since it'd preserve full history on both sides.
- **/volume1/photo/ migration**: pull Niccolo + Wedding photos into hestia as a one-shot. They're not on a daily sync path.
- **Alcatraz retirement**: full decom, once a year or two of clean hestia-SOT operation proves the model.

## Risk + rollback

**Risk**: misconfigured Immich PV after phase 4 → Immich shows 0 photos / 500 errors. **Rollback**: re-apply the old PV manifest pointing back to alcatraz (the Retain policy means the old PV manifest still references the unchanged NFS export). Recovery time: minutes.

**Risk**: incomplete initial bulk sync leaves hestia missing recent uploads. **Rollback**: NOT needed — alcatraz is still authoritative until phase 4 cutover. Re-run the rsync until counts/sizes match before proceeding.

**Risk**: ZFS dataset quota too tight, rsync fails mid-flight. **Rollback**: increase quota via midclt (`pool.dataset.update`), re-run rsync.

## PRs (this plan)

- **PR 1 (this doc)** — the plan itself. Reviewable, no code changes to running systems.
- **PR 2 (Phase 4)** — `images/immich-photos-backup/immich-photos-backup.sh` DST change + `hosts/hestia/immich-photos-backup/docker-compose.yml` bind-mount widen. Optionally extends to sync the rest of `family/`. Build kicks off → follow-up PR pins the new digest, like every other image change.
- **PR 3 (Phase 5)** — `apps/production/immich/nfs-photos.yaml` server + path update. Same shape as PR #765 (Jellyfin NFS repoint).

Phases 1, 2, 3, 6 are operator/agent ops on hestia + alcatraz (TrueNAS midclt + DSM UI + rsync runs). No PR needed for those — captured here as the runbook.
