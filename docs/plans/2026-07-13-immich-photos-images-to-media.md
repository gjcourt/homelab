---
status: in-progress
last_modified: 2026-07-13
summary: "Consolidate the Immich photo library from family/images/photos onto the canonical family/media/photos (all media under family/media/ per the assimilation plan); retire family/images/*"
---

# Immich photos: `family/images/photos` → `family/media/photos`

## Why

All media lives under **`family/media/`** (photos, video, music) per the archive-assimilation
plan. Prod Immich, Jellyfin, and Navidrome already read from `family/media/*`. But the **photo
feeder** (`immich-photos-backup`) kept writing to the older **`family/images/photos`**, so from
**2026-07-10** every new phone photo landed in a directory Immich doesn't scan and stopped
appearing in the app.

This plan finishes the move so `family/media/photos` is the single canonical photo path and
`family/images/*` is retired.

## Incident summary (root cause, already mitigated)

- Prod Immich external-library PV `immich-photos-pv-prod` reads `/mnt/main/family/media/photos`
  (correct, since `03d83fc4`).
- The feeder wrote to `/mnt/main/family/images/photos` — commit `ce1ca7c3` *meant* to repoint it
  but left a stray hard-coded `DST_BASE="…/images/photos"` that overrode the media/photos default.
- **Fix:** PR #1108 removes the override (single env-overridable `DST_BASE` → `media/photos`). The
  live container was hot-patched and a corrected pass backfilled everything since Friday into
  `media/photos` (verified: `george/2026/07` 176 → 297 files, newest IMG_6273).
- **Audit:** video (Jellyfin) and music (Navidrome) are unaffected — their writers already target
  `family/media/`; the old `main/media/{movies,tv-shows}` paths are empty and `main/media/music`
  holds 16 stale files. Photos was the only diverging media type.

## Verify the ZFS layout FIRST (blocker for the destructive steps)

The on-disk layout is currently inconsistent and must be resolved on the box before any
`zfs rename`/`destroy`:

- `main/family/images` is a dataset (~445G). `main/family/images/photos` is a **child dataset**
  reporting only ~205K `refer` despite ~141k files visible under that path — a mount/shadow
  inconsistency to explain (is the child mounted? are the files in the parent dataset?).
- `main/family/media` is **not a dataset** — `media/photos` is a plain directory inside
  `main/family` holding the live library.

**Decision needed:** promote `family/media/photos` to a proper ZFS dataset (recommended — matches
the old `images/photos` dataset, gives independent snapshot/recordsize/atime settings) vs. leave it
a directory under `main/family` (covered by the `main/family` parent snapshot task).

Commands to run (read-only) before deciding:

```sh
ssh truenas_admin@10.42.2.10 'sudo zfs list -o name,used,refer,mounted,mountpoint -r main/family; \
  mount | grep family; \
  sudo du -sh /mnt/main/family/media/photos /mnt/main/family/images/photos'
```

## Reference inventory (everything still on `family/images/photos`)

| # | Reference | File | Repoint to | Apply method |
|---|---|---|---|---|
| 1 | Feeder `DST_BASE` | `images/immich-photos-backup/immich-photos-backup.sh` | `media/photos` | **DONE — PR #1108** (merge → CI image → bump compose digest → redeploy) |
| 2 | Feeder docs | `hosts/hestia/immich-photos-backup/README.md`, `docker-compose.yml` comment | `media/photos` | doc-only; land with the ZFS-dataset decision so "destination dataset" text is accurate |
| 3 | hestia dataset table | `hosts/hestia/README.md:99` | `media/photos` | doc-only; tracks the dataset decision above |
| 4 | Staging src PV | `apps/staging/immich/nfs-photos.yaml:81` | `family/media/photos` | PV `nfs.path` is **immutable** → suspend Flux (`apps-staging`) → delete PVC+PV → reapply → restart immich-stage |
| 5 | Staging 30d slice PV | `apps/staging/immich/nfs-photos.yaml:34` | `family/media/photos-staging-30d` | needs the 30d slice dir/dataset relocated under `media/`; same PV recreate |
| 6 | 30d-sync CronJob | `apps/staging/immich/cronjob-photos-30d.yaml` | `media/photos` src + `media/photos-staging-30d` dst | edit paths; verify next run Completes (it has been Failing) |
| 7 | alcatraz-pull `rrsync` root | `hosts/alcatraz/immich-photos-pull/README.md` + **alcatraz `authorized_keys`** | `/mnt/main/family/media/photos` | edit the `command="…rrsync -ro …"` restriction in alcatraz's `authorized_keys` (remote write to alcatraz), then the doc |

## Migration steps (ordered)

1. **[done]** Fix + backfill the feeder (PR #1108 + hot-patch). New photos flow to `media/photos`.
2. **Merge #1108**, let CI rebuild the image, bump the compose digest, redeploy on hestia so the
   scheduled 04:00 run uses the baked fix (the hot-patch is lost on container restart).
3. **Resolve the ZFS layout** (section above) and decide dataset-vs-directory for `media/photos`.
   If promoting to a dataset: create `main/family/media/photos`, migrate data
   (`zfs rename` if `images/photos` can be moved wholesale, else `rsync` + verify counts), attach a
   periodic-snapshot task.
4. **Repoint staging** (rows 4–6): PV recreate under suspended Flux, fix the CronJob paths, confirm
   a green 30d-sync run.
5. **Repoint alcatraz-pull** (row 7): update the `authorized_keys` rrsync root + doc; confirm a
   pull still succeeds and alcatraz stays a full backup.
6. **Update remaining docs** (rows 2–3) to match the final dataset reality.
7. **Retire `family/images/*`** — only after `media/photos` is confirmed complete (file-count and
   `du` parity with the old tree) **and** independently snapshotted/backed up. Destroy
   `main/family/images/photos` (+ the empty `main/family/videos`) datasets. This is the one
   irreversible step — do it last, with a fresh snapshot as the safety net.

## Safety / rollback

- Everything through step 6 is additive or reversible (PV recreates re-bind to existing data;
  `Retain` reclaim policy on prod PV means no data is deleted by k8s).
- Step 7 is the only destructive action. Gate it on: (a) `find | wc -l` and `du -sh` parity between
  `media/photos` and `images/photos`, (b) a current ZFS snapshot of `images/photos`, (c) 24h of
  confirmed new-photo flow into Immich via `media/photos`.
- The old `family/images/photos` dataset stays intact until step 7, so rollback = repoint back.

## Exit criteria

- [x] Feeder writes to `media/photos`; photos since 2026-07-10 imported into Immich.
- [ ] #1108 merged, image rebuilt, compose digest bumped, feeder redeployed.
- [ ] `media/photos` dataset-vs-dir decided and (if chosen) promoted + snapshotted.
- [ ] Staging PVs + 30d-sync CronJob on `media/`, next sync green.
- [ ] alcatraz-pull rrsync root on `media/photos`; alcatraz backup verified.
- [ ] All docs reference `media/photos`; no `family/images/photos` references remain in the repo.
- [ ] `family/images/*` datasets destroyed after parity + snapshot verification.
