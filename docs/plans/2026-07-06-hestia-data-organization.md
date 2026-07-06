---
status: planned
last_modified: 2026-07-06
summary: "Dataset taxonomy and data-organization policy for hestia (TrueNAS pool `main`). Fixes the core family-vs-media-vs-archive confusion after the 2026-07-05/06 machine-recovery session. Defines three buckets — family/ (household's own content → Immich, videos, audio, docs), media/ (consumed Jellyfin media), archive/ (cold per-machine restore-only backups) — with a per-artifact home mapping, per-person uid/gid layout for the photo library, dataset-vs-subdir + ZFS property conventions, and a per-bucket snapshot/replication/integrity policy. Flags real gaps found live: main/archive has NO snapshots and NO quota; media/ is not a ZFS dataset; archive children are plain dirs not per-machine datasets; photo-staging (82G) is transient scratch to reclaim."
---

# hestia data-organization plan

> **Planning doc only.** Large recovery jobs are still writing to
> `/mnt/main/archive/` and `/mnt/main/family/videos/` as of this writing. This
> document proposes structure and policy; it does **not** authorize moving or
> deleting anything currently in flight. Execution items are checklisted at the
> end and gated on George's review + the recovery jobs finishing.

hestia = the homelab TrueNAS SCALE NAS (`truenas_admin@10.42.2.10`, passwordless
sudo, TrueNAS 26.x). Single data pool **`main`**, mounted at `/mnt/main`.

## 0. Live survey (2026-07-06)

Numbers below are from a direct `zfs list` / `du` survey, not from memory.

**Pool capacity:** `main` — **2.10 TiB used, 18.5 TiB available** (~20.6 TiB
usable; the "~26 TB" figure is raw). Plenty of headroom, so the bias throughout is
*keep faithful copies, snapshot generously* rather than prune aggressively.

**Real ZFS datasets under `main`** (everything else in `/mnt/main` is a plain
directory inside the root dataset):

| Dataset | Used | recordsize | atime | mountpoint | notes |
|---|---|---|---|---|---|
| `main/family` | 334 G | 1M | off | `/mnt/main/family` | **quota 2T**; household's own content |
| `main/family/images` | 291 G | 1M | off | `/mnt/main/family/images` | Immich external library root |
| `main/family/images/photos` | 291 G | 1M | off | `/mnt/main/family/images/photos` | `<person>/YYYY/MM` |
| `main/family/videos` | ~0 | 1M | off | `/mnt/main/family/videos` | **new this session**, receiving MIGHTY JOE now |
| `main/archive` | 317 G | 1M | off | `/mnt/main/archive` | **no quota, NO snapshots** (gap) |
| `main/homes` | 65 M | 128K | off | `/mnt/main/homes` | quota 1T; SMB user homes |
| `main/apps` | 59 M | 128K | off | `/mnt/main/apps` | |
| `main/k8s/iscsi/*` | 145 G | volumes | — | — | democratic-csi iSCSI PVCs (out of scope) |
| `main/ix-apps/*` | 18.5 G | 128K | off | `/mnt/.ix-apps` | TrueNAS app engine (out of scope) |
| `main/backups` | ~0 | 128K | off | `/mnt/main/backups` | empty |

**Plain directories in the root `main` dataset (NOT their own datasets):**

| Path | Size | Problem |
|---|---|---|
| `/mnt/main/media` | **~1.2 T** (movies 1.1T, tv-anime 65G, music 31G, tv-shows 0) | **Not a dataset** → inherits root's 128K recordsize (wrong for large media), gets **no independent snapshot policy or quota**. |
| `/mnt/main/ai`, `/mnt/main/downloads`, `/mnt/main/melodic-muse` | small | Loose root dirs; out of scope but noted. |

**`main/family/` subtree** (all plain subdirs unless marked *dataset*):
`images/` *(dataset, 291G)*, `videos/` *(dataset, new)*, `audio/` (31G),
`literature/` (10G), `projects/` (1.7G), `documents/` (270M), `admin/` (48M),
`misc/` (26M), `iphone-recovery/` (transient mount, ~empty).
So **documents and audio homes already exist** as subdirs — this plan formalizes
them rather than inventing new ones.

**`main/family/images/`:** `photos/` *(dataset)*, `artwork/`,
`photos-staging-30d/` (empty — intended transient inbox).

**`main/family/images/photos/`** — per-person, verified live:

| dir | uid | gid | mode |
|---|---|---|---|
| `george/` | **1028** | **100** (`users`) | `755` |
| `mara/` | **1027** | **100** (`users`) | `755` |

Layout under each person is `YYYY/MM` (`george/` has `2013`…`2026`, months
`01`–`12`), confirmed live.

**`main/archive/` children** (all plain dirs — **none are their own dataset**):

| dir | size | what |
|---|---|---|
| `winpc-5800x/George/` | 108 G | Windows PC home. Contains `Apple/MobileSync/Backup/` = **97 G iPhone-13 backup** (GUID `00008110-…`, encrypted). |
| `gauss/` | 69 G | Retina MacBook home (`Pictures` 6.8G incl iPhoto/Aperture, `Dropbox` 16G, etc.) |
| `dropbox-cloud/` | 57 G | Dropbox cloud pull. Contains `Camera Uploads/` = **36 G, 2010–2018 photos**. |
| `oldmac-unibody/` | 3.7 G | Dying 2011–2014 MacBook. `priority/MobileSync/` = **2012 iPhone backup**; `priority/Pictures` (Aug 2012). |
| `oldmac-salvage/` | 392 K | keychains + keepassx only. |
| `photo-staging/` | **82 G** | **Transient scratch**: `iphone13-cameraroll/`, `messages-gauss/` (11G, 2013–2022), `extract.log`, `*.done` markers. |
| `_tools/` | 18 K | `ibackup.py` (the iOS-backup extractor used this session). |

> **Naming discrepancy to reconcile:** this dataset is `winpc-5800x` but the
> companion plan `2026-07-05-mac-laptop-archive.md` calls the machine
> `winpc-5600x`. Pick one machine-id and rename before it calcifies (see Open
> Decision D6).

**Snapshots (122 total).** Auto periodic tasks cover **`main/family`** (and its
children) and **`main/homes`** only — 21 snapshots each: **daily** 05:00,
**weekly** 05:30 Sun, **monthly** 06:00 on the 1st.
**`main/archive` has ZERO snapshots. `media` (not a dataset) has zero.** These are
the two biggest policy gaps this plan closes.

---

## 1. Taxonomy — the three buckets (the decision people get wrong)

Every blob of data on hestia is exactly one of three kinds. The kind determines the
dataset it lives in, its ZFS properties, and its snapshot/backup policy. **When in
doubt, ask "is this *ours*, is it *consumed*, or is it a *machine backup*?"**

### `family/` — the household's **own** content (authored/captured by us)
Photos we took, videos we shot, documents we wrote, audio/scans we own. This is
**irreplaceable primary data** and gets the strongest protection (frequent
snapshots + offsite replication). Per-person where identity matters (photos), shared
where it doesn't (documents, literature). Immich indexes `family/images`.

### `media/` — **consumed** media for Jellyfin (not ours)
Movies, TV, anime, music ripped or downloaded for playback. **Replaceable** (can be
re-acquired), large sequential files. Light-to-no snapshots, no offsite copy needed.
Keeping this separate from `family/` is the whole point: a ripped Blu-ray and a
wedding video must never share a snapshot/backup class.

### `archive/` — **cold, faithful machine backups** (restore-only)
Bit-faithful copies of whole machines/drives: Windows PC, old MacBooks, Dropbox
cloud, iPhone backups. **You do not browse this as a library.** It exists so a
source drive can be wiped safely and so *photos inside it* can later be mined into
`family/`. Snapshot-on-write + a manifest for verifiability; it is itself a backup so
it does not need a second offsite copy of equal rigor (though the whole pool is
snapshotted).

> The failure mode to avoid: dumping a machine's photo folder straight into
> `family/images` **as a machine dump** (unsorted, wrong owner) — or conversely
> leaving living photos buried in `archive/` where Immich never sees them. The
> pipeline in §6 is exactly the bridge: archive is the faithful copy, Immich gets a
> **curated, deduped, EXIF-sorted** projection.

---

## 2. Target layout

```
/mnt/main
├── family/                     [dataset, quota 2T]  OUR content — max protection
│   ├── images/                 [dataset]  ── Immich external library root
│   │   ├── photos/             [dataset]  <person>/YYYY/MM, per-person uid
│   │   │   ├── george/         uid 1028 : gid 100 (users), 755
│   │   │   └── mara/           uid 1027 : gid 100 (users), 755
│   │   ├── artwork/            shared, non-photo images
│   │   └── _inbox/             transient import staging (rename of photos-staging-30d)
│   ├── videos/                 [dataset]  home movies (MIGHTY JOE, etc.)  ← propose per-person subdirs later
│   ├── audio/          (31G)   voice memos, recordings we own   ← propose promote to dataset
│   ├── documents/      (270M)  scans, PDFs, our paperwork
│   ├── literature/     (10G)   ebooks / writing
│   ├── projects/       (1.7G)  personal project files
│   ├── admin/ · misc/          misc household
│   └── (iphone-recovery/ — retire; was a transient mount)
│
├── media/              (~1.2T) CONSUMED media for Jellyfin   ← PROMOTE to dataset
│   ├── movies/ (1.1T) · tv-shows/ · tv-anime/ (65G) · music/ (31G)
│
├── archive/            [dataset]  COLD per-machine backups — restore-only
│   ├── winpc-5800x/    (108G)  ← promote to per-machine dataset (see D6 re: id)
│   ├── gauss/          (69G)   ← promote to per-machine dataset
│   ├── oldmac-unibody/ (3.7G)  ← promote to per-machine dataset
│   ├── oldmac-salvage/ (392K)
│   ├── dropbox-cloud/  (57G)
│   ├── capsule-tm-2014/        (future) Feb-2014 gauss Time Machine
│   ├── _manifests/             sha256 + file-count/size manifests, one per source
│   ├── _tools/                 ibackup.py etc.
│   └── photo-staging/  (82G)   TRANSIENT — reclaim after Immich import verified
│
├── homes/  [dataset, quota 1T]     SMB user homes (unchanged)
├── apps/ · backups/ · k8s/ · ix-apps/   infra (out of scope)
└── ai/ · downloads/ · melodic-muse/     loose root dirs (tidy later)
```

---

## 3. Where each recovered artifact belongs

"→ Immich" = mine photos/videos out via the §6 pipeline into
`family/images/photos/<person>/YYYY/MM`, deduped. "cold" = stays in `archive/`
faithful copy. Most photo-bearing sources are **both**: the cold copy is the
source of truth, Immich gets a curated projection.

| Artifact (current location) | Final home | Immich? | Cold archive? | Notes |
|---|---|---|---|---|
| Windows PC home `winpc-5800x/George/` | `archive/winpc-5800x/` (→ dataset) | photos only | **yes** | Machine backup. |
| **iPhone-13 backup** `…/Apple/MobileSync/Backup/` (97G, encrypted) | stays inside `archive/winpc-5800x/` | **yes** (extract camera roll via `ibackup.py`) | **yes** | Encrypted → needs backup password to extract. Keep the raw backup cold. |
| **2012 iPhone backup** `oldmac-unibody/priority/MobileSync/` | stays inside `archive/oldmac-unibody/` | **yes** (extract) | **yes** | Oldest phone photos. |
| `gauss/` MacBook home (69G) | `archive/gauss/` (→ dataset) | photos only (`Pictures/`, iPhoto/Aperture) | **yes** | Extract from library packages, not Photos.app. |
| **Dropbox `Camera Uploads/`** (36G, 2010–2018) | `archive/dropbox-cloud/` | **yes** | **yes** | Prime source of pre-Immich photos. |
| Rest of `dropbox-cloud/` (docs, music, projects) | `archive/dropbox-cloud/` | no | **yes** | Non-photo → cold only. Genuinely-current docs George wants live → copy into `family/documents`. |
| **Recovered Messages photos** `photo-staging/messages-gauss/` (11G, 2013–2022) | → Immich, then delete from staging | **yes** | already covered by `gauss` cold copy | Transient extraction. |
| `photo-staging/iphone13-cameraroll/` | → Immich, then delete from staging | **yes** | raw backup is the cold copy | Transient. |
| `oldmac-unibody/priority/Pictures` (2012) | `archive/oldmac-unibody/` | **yes** | **yes** | |
| `oldmac-salvage/` (keychains, keepassx) | `archive/oldmac-salvage/` | no | **yes** | Secrets — keep, don't index. |
| **Capsule Time Machine** (Feb-2014 gauss) — *future* | `archive/capsule-tm-2014/` (→ dataset) | photos only, after mounting the sparsebundle | **yes** | May contain photos absent elsewhere. |
| **MIGHTY JOE videos** (NTFS drive, copying now) | `family/videos/` | n/a (Immich can index videos too — decide D2) | it *is* the primary copy | **OURS**, not `media/`. Home movies. |
| Jellyfin movies/tv/music (already in `media/`) | `media/` (→ promote to dataset) | no | no | Consumed, replaceable. |

---

## 4. Conventions

### Photo-library ownership (per-person uid/gid model)
- Each person owns their tree: `george/` = uid **1028**, `mara/` = uid **1027**,
  both gid **100** (`users`), directories **755**, files **644**.
- Immich mounts `family/images` read-oriented over NFS as an **external library**
  and indexes by **EXIF date** — the `<person>/YYYY/MM` folders are *organization
  for humans*, not what builds the timeline. Getting the month slightly wrong is
  cosmetic; getting the **owner** wrong is not (it breaks the per-person model and
  future per-user Immich sharing).
- After dropping files in, fix ownership explicitly, e.g.:
  ```bash
  sudo chown -R 1028:100 /mnt/main/family/images/photos/george/2014
  sudo find /mnt/main/family/images/photos/george/2014 -type d -exec chmod 755 {} +
  sudo find /mnt/main/family/images/photos/george/2014 -type f -exec chmod 644 {} +
  ```
- Purge stray `.DS_Store` (SMB/Finder litter) from the library periodically.

### Layout
- Photos/videos: `family/images/photos/<person>/YYYY/MM/<original-name>`, year+month
  from **EXIF capture date** (fallback file mtime).
- Cold archive: **preserve the source's original folder structure verbatim** under
  `archive/<machine-id>/` — do not "tidy" it; faithfulness is the point.

### ZFS properties (defaults per bucket)
| Bucket | compression | recordsize | atime | rationale |
|---|---|---|---|---|
| `family/images`, `family/videos`, `family/audio`, `media/*`, `archive/*` | lz4 | **1M** | off | Large, mostly-immutable files; 1M record + lz4 is the right large-file profile. |
| Mixed/small (`documents`, `homes`, `apps`) | lz4 | 128K (default) | off | Many small files. |

lz4 everywhere (cheap, never hurts). `atime=off` everywhere (avoids write
amplification on read).

### New dataset vs. subdirectory — the rule
Make a **new child dataset** (not just a `mkdir`) when the child needs an
**independent** snapshot schedule, quota, or recordsize. Concretely:
- **Per-machine archive dirs → each its own dataset** (`archive/winpc-5800x`,
  `archive/gauss`, …) so a machine's snapshot/manifest is independent and a
  finished source can be snapshotted + frozen without touching others. (The
  mac-laptop-archive plan already assumes this for *new* machines; §7 back-fills the
  ones created this session as plain dirs.)
- **`media` → promote to a dataset** (`main/media`, recordsize 1M, its own light
  snapshot class). Optionally per-category children if you want per-category quota.
- **`family/audio` (31G) → consider promoting to a dataset** (media-like large files,
  distinct snapshot value). `documents`/`literature`/`projects` can stay subdirs of
  `family` (they inherit family's strong snapshot policy, which is what you want).
- A plain **subdir** is correct when the child should *inherit* the parent's policy
  (e.g. `documents/` under `family/`).

> Note: converting an existing plain dir to a dataset is **not** a `zfs rename` — you
> create the dataset then `rsync` the dir into it and swap. Do this only when the
> recovery writes are done and per resource, verifying with a manifest first (§7).

### Naming
- Datasets/dirs: lowercase kebab, machine-ids stable (`winpc-5800x`, `gauss`,
  `oldmac-unibody`, `oldmac-salvage`, `dropbox-cloud`, `capsule-tm-2014`).
- Reserved `_`-prefixed helper dirs inside `archive/`: `_tools/`, `_manifests/`.

---

## 5. Snapshot / backup / integrity policy per bucket

| Bucket | Snapshots | Offsite / replication | Integrity |
|---|---|---|---|
| **`family/*`** | **Keep** the existing periodic task (daily 05:00 / weekly Sun / monthly 1st). This is irreplaceable primary data. | **alcatraz replication** + the **immich-photos-backup duplex sync** as the offsite copy. | Immich's own DB + periodic scrub. |
| **`media/*`** | **Light or none** (weekly, short retention) once it's a dataset — it's replaceable. | None needed. | ZFS scrub only. |
| **`archive/*`** | **Snapshot-on-write**: after a source finishes copying and its **manifest verifies**, take one immutable snapshot per machine dataset (`archive/<id>@sealed-YYYY-MM-DD`) and hold it. Optionally a light periodic on top while a machine is still being appended to. | It *is* a backup; covered by pool-level snapshots. A second offsite of the huge cold set is **Open Decision D1**. | **Manifest per source** (see below) — the load-bearing safety check. |

### Archive manifest (verify completeness before wiping any source drive)
Before a physical source drive is erased, its `archive/<id>/` copy must have a
committed manifest so completeness is provable, not assumed:
```bash
# Per machine, once copy is complete:
cd /mnt/main/archive/<machine-id>
sudo bash -c '
  find . -type f -printf "%s\t%p\n" | sort > /mnt/main/archive/_manifests/<machine-id>.filelist
  find . -type f -exec sha256sum {} + | sort > /mnt/main/archive/_manifests/<machine-id>.sha256
  { echo "files: $(wc -l < /mnt/main/archive/_manifests/<machine-id>.filelist)";
    echo "bytes: $(du -sb . | cut -f1)";
    echo "sealed: $(date -Iseconds)"; } > /mnt/main/archive/_manifests/<machine-id>.summary
'
sudo zfs snapshot main/archive/<machine-id>@sealed-$(date +%F)
```
Commit `_manifests/` (or a copy) into the repo so the record survives the NAS. Only
after the manifest exists and matches is the source drive safe to wipe.

**Existing gaps to close (execution):**
1. **Add a periodic snapshot task for `main/archive`** — it currently has **none**.
2. **Add `main/media`** (as a dataset) to a light snapshot task once promoted.
3. Confirm alcatraz replication actually includes `main/family` recursively.

---

## 6. Immich integration (how new photos reach the library)

Immich runs the **external-library** model against `family/images` (external lib at
`photos.burntbytes.com`, read over an NFS PV). The library is **files on disk**;
Immich does not own or move them.

**To add photos (the standard loop):**
1. Land files in a person's tree: `family/images/photos/<person>/YYYY/MM/…`
   (drop into `family/images/_inbox/` first if you need to sort/dedupe).
2. **Fix ownership** to that person's uid:gid + 755/644 (§4).
3. In Immich, run a **Scan** of the external library — new files get indexed and
   placed on the timeline by EXIF date. No move/copy happens; Immich reads in place.

**Deduping when mining archives (so cold copies don't double-count):**
- Photos already imported are already in the library. Before importing a batch from
  `archive/*` or `photo-staging/*`, **sha256-dedupe against the live library** (the
  method already used this session) so re-imports of the same shot are dropped:
  ```bash
  # Build a hash index of what's already imported, then only copy new hashes.
  find /mnt/main/family/images/photos -type f -exec sha256sum {} + \
    | awk '{print $1}' | sort -u > /tmp/have.hashes
  # For each candidate, skip if its sha256 is in have.hashes; else EXIF-sort in.
  ```
- Immich also does its own perceptual/asset dedupe, but content-hashing at the file
  layer keeps the on-disk library clean, which is what external-library mode exposes.

---

## 7. Consolidation & cleanup

Ordered, each step gated on the prior verifying. **None of this runs while recovery
jobs are still writing** (§0 warning).

1. **Finish in-flight copies** (MIGHTY JOE → `family/videos`; any archive writes).
2. **Mine photos** from `photo-staging/` (iphone13 camera roll, messages-gauss) and
   from the archive sources per §3, into Immich via §6 (dedupe on).
3. **Verify import** (Immich shows the assets; counts reconcile) — *only then*:
4. **Reclaim `photo-staging/` (82G)** — it is transient scratch. Delete it (or, if
   nervous, snapshot `archive/photo-staging@pre-delete` first, then remove). The raw
   iPhone backups and `gauss/` cold copies remain the sources of truth, so nothing
   unique is lost.
5. **Write manifests + seal snapshots** for each finished `archive/<machine-id>`
   (§5) before any source drive is wiped.
6. **Promote to datasets** (create dataset, rsync-in, swap, verify manifest):
   `main/media` first (biggest policy win), then per-machine `archive/*`, then
   optionally `family/audio`.
7. **Retire transients:** rename `family/images/photos-staging-30d` → `_inbox`,
   retire the `iphone-recovery` mount, purge `.DS_Store`.
8. **Add snapshot tasks** for `main/archive` (and `main/media` post-promotion).

---

## 8. Open decisions for George

- **D1 — Cold-archive offsite.** The cold set is ~340 G and growing (winpc 108G,
  gauss 69G, dropbox 57G, + future Capsule). Keep a **second offsite copy** of the
  full cold archive, or trust pool snapshots + the fact it's already a backup of
  still-existing (for now) sources? Cheap to decide once, expensive to reverse after
  drives are wiped.
- **D2 — Videos in Immich?** `family/videos` (home movies) — index in Immich
  alongside photos (one timeline, one face-search), keep separate for Jellyfin, or
  both? Affects whether `videos` sits under the Immich external-library root.
- **D3 — Prune vs. keep whole machine dumps.** `winpc-5800x/George` is a full home
  incl. `AppData` (8.4G), caches, `.docker`, tool configs. Keep the machine
  bit-faithful forever, or, after sealing a manifest, prune obvious junk
  (caches/AppData) to shrink the cold set? Recommend **keep faithful** given 18.5T
  free — but confirm.
- **D4 — Documents/audio homes.** `family/documents` (270M) and `family/audio`
  (31G) exist. Promote `audio` to its own dataset (recommended)? Is there
  *current* (non-archival) paperwork in `dropbox-cloud` that should be lifted into
  `family/documents` as live data rather than left cold?
- **D5 — media promotion + per-category.** Promote `media` to a dataset (recommended
  yes). Per-category child datasets (movies/tv/music) for independent quota, or one
  `media` dataset? One is simpler; children only if you want quotas.
- **D6 — machine-id reconciliation.** `winpc-5800x` (on disk) vs `winpc-5600x` (mac
  plan). Confirm the real CPU and rename the dataset **before** it's referenced
  further.
- **D7 — Snapshot cadence for archive.** Sealed-on-write only, or also a light
  periodic (e.g. weekly, 4-deep) while machines are still being appended? Default
  proposal: sealed-on-write + weekly-4 until sealed.

---

## Appendix — quick command reference

```bash
# Buckets at a glance
sudo zfs list -o name,used,avail,quota,recordsize,mountpoint main -r | grep -vE 'iscsi|ix-apps|.system'

# Which datasets have snapshot coverage
sudo zfs list -t snapshot -o name | sed 's/@.*//' | sort | uniq -c | sort -rn

# Per-person ownership audit of the photo library
sudo find /mnt/main/family/images/photos -maxdepth 1 -mindepth 1 -printf '%u:%g %m %p\n'
```

**Related plans:** `docs/plans/2026-07-05-mac-laptop-archive.md` (the per-machine
capture pipeline that feeds `archive/` and Immich) — this doc defines *where things
land and how they're protected*; that doc defines *how bytes get off each machine*.
