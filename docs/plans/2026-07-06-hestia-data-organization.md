---
status: planned
last_modified: 2026-07-06
summary: "Dataset taxonomy and data-organization policy for hestia (TrueNAS pool `main`). Fixes the core family-vs-media-vs-archive confusion after the 2026-07-05/06 machine-recovery session. Defines three buckets — family/ (household's own content → Immich, videos, audio, docs), media/ (consumed Jellyfin media), archive/ (cold per-machine restore-only backups) — with a per-artifact home mapping, per-person uid/gid layout for the photo library, dataset-vs-subdir + ZFS property conventions, and a per-bucket snapshot/replication/integrity policy. Flags real gaps found live: main/archive (318G irreplaceable) has NO snapshots and NO quota; the main/media datasets have NO snapshots (media is ALREADY a dataset hierarchy at 1M recordsize — no promotion needed); archive children are plain dirs not per-machine datasets; the archive manifest must diff SOURCE-vs-destination before any drive wipe; photo-staging (82G) is transient scratch to reclaim."
---

# hestia data-organization plan

> **Planning doc only.** Large recovery jobs are still writing to
> `/mnt/main/archive/` (MIGHTY JOE staging, drive pulls) as of this writing. This
> document proposes structure and policy; it does **not** authorize moving or
> deleting anything currently in flight. Execution items are checklisted at the
> end and gated on George's review + the recovery jobs finishing.

> **Draft 2 (2026-07-06)** — revised after a 3-round critique + live re-survey:
> corrected the "media isn't a dataset" error (it already is, at 1M), fixed the MIGHTY
> JOE classification (it's ripped films → `media/`, not home video), hardened the
> archive manifest to a **source↔destination** diff, and front-loaded the
> `archive/` snapshot gap ahead of the drive-wipe window.

hestia = the homelab TrueNAS SCALE NAS (`truenas_admin@10.42.2.10`, passwordless
sudo, TrueNAS 26.x). Single data pool **`main`**, mounted at `/mnt/main`.

## 0. Live survey (2026-07-06)

Numbers below are from a direct `zfs list` / `du` survey, not from memory.

**Pool capacity:** `main` — **2.10 TiB used, 18.5 TiB available** (`zpool`: 29.1T
raw / 26.1T free / 10% allocated; the raw-vs-usable delta is RAIDZ parity +
reservation). Plenty of headroom, so the bias throughout is *keep faithful copies,
snapshot generously* rather than prune aggressively.

**Real ZFS datasets under `main`** (everything else in `/mnt/main` is a plain
directory inside the root dataset):

| Dataset | Used | recordsize | atime | mountpoint | notes |
|---|---|---|---|---|---|
| `main/family` | 334 G | 1M | off | `/mnt/main/family` | **quota 2T**; household's own content |
| `main/family/images` | 291 G | 1M | off | `/mnt/main/family/images` | Immich external library root |
| `main/family/images/photos` | 291 G | 1M | off | `/mnt/main/family/images/photos` | `<person>/YYYY/MM` |
| `main/family/videos` | ~0 | 1M | off | `/mnt/main/family/videos` | **new this session**, empty (MIGHTY JOE was reclassified → `media/`) |
| `main/media` | **1.13 T** | 128K\* | off | `/mnt/main/media` | **already a dataset** — parent holds ~0; data is in 1M children below |
| `main/media/movies` | 1.03 T | **1M** | off | `/mnt/main/media/movies` | already the correct large-file profile |
| `main/media/{music,tv-anime,tv-shows}` | 31G / 64G / ~0 | **1M** | off | — | all datasets, all 1M |
| `main/archive` | 318 G | 1M | off | `/mnt/main/archive` | **no quota, NO snapshots** (gap) |
| `main/homes` | 65 M | 128K | off | `/mnt/main/homes` | quota 1T; SMB user homes |
| `main/apps` | 59 M | 128K | off | `/mnt/main/apps` | |
| `main/k8s/*` | 145 G | volumes | — | — | iSCSI PVCs (out of scope) |
| `main/ix-apps/*` | 18.5 G | 128K | off | `/mnt/.ix-apps` | TrueNAS app engine (out of scope) |
| `main/backups` | ~0 | 128K | off | `/mnt/main/backups` | empty |

\* `main/media`'s own 128K recordsize is harmless — it stores no direct data
(`REFER` ~256K); every data-bearing child is already 1M.

**Plain directories in the root `main` dataset (NOT their own datasets):**

| Path | Size | Problem |
|---|---|---|
| `/mnt/main/ai`, `/mnt/main/downloads`, `/mnt/main/melodic-muse` | small | Loose root dirs; out of scope but noted. |

> **Correction (this was wrong in draft 1):** `media/` is **already** a ZFS dataset
> hierarchy — `main/media` plus `movies`/`music`/`tv-anime`/`tv-shows` children — and
> the data-bearing children are **already `recordsize=1M`**. The only real media gap is
> **no snapshot task**, not "promote to a dataset." All media-promotion language below
> is struck accordingly.

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

**Snapshots (~120 as of the survey; the exact count drifts as periodic tasks fire).** Auto periodic tasks cover **`main/family`** (and its
children) and **`main/homes`** only — 21 snapshots each: **daily** 05:00,
**weekly** 05:30 Sun, **monthly** 06:00 on the 1st.
**`main/archive` has ZERO snapshots; the `main/media` datasets have ZERO.** Neither
is protected. These are the two biggest policy gaps this plan closes — and both are a
**snapshot-task** problem, *not* a dataset-creation problem.

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
│   ├── videos/                 [dataset]  home movies WE shot (empty so far — MIGHTY JOE was mis-IDed; it's media/)  ← per-person subdirs later
│   ├── audio/          (31G)   voice memos, recordings we own   ← propose promote to dataset
│   ├── documents/      (270M)  scans, PDFs, our paperwork  ← sub-folder schema: reference/2026-07-06-family-documents-schema.md
│   ├── literature/     (10G)   ebooks / writing
│   ├── projects/       (1.7G)  personal project files
│   ├── admin/ · misc/          misc household
│   └── (iphone-recovery/ — retire; was a transient mount)
│
├── media/  [dataset 1.13T]  CONSUMED media for Jellyfin   ← already datasets; needs SNAPSHOTS only
│   ├── movies/ [dataset,1M,1.03T] · music/ [1M] · tv-anime/ [1M] · tv-shows/ [1M]
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
| **iPhone-13 backup** `…/Apple/MobileSync/Backup/` (97G, encrypted) | stays inside `archive/winpc-5800x/` | **done** — camera roll (18,768 items) already extracted → `photo-staging/iphone13-cameraroll/`, awaiting Immich import | **yes** | Decrypt needs the **retained** backup password (`piano-…`); keep the raw backup cold. **If that password is lost, re-extraction is impossible** — gates the staging delete in §7.4. |
| **2012 iPhone backup** `oldmac-unibody/priority/MobileSync/` | stays inside `archive/oldmac-unibody/` | **yes** (extract) | **yes** | Oldest phone photos. |
| `gauss/` MacBook home (69G) | `archive/gauss/` (→ dataset) | photos only (`Pictures/`, iPhoto/Aperture) | **yes** | Extract from library packages, not Photos.app. |
| **Dropbox `Camera Uploads/`** (36G, 2010–2018) | `archive/dropbox-cloud/` | **yes** | **yes** | Prime source of pre-Immich photos. |
| Rest of `dropbox-cloud/` (docs, music, projects) | `archive/dropbox-cloud/` | no | **yes** | Non-photo → cold only. Genuinely-current docs George wants live → copy into `family/documents`. |
| **Recovered Messages photos** `photo-staging/messages-gauss/` (11G, 2013–2022) | → Immich, then delete from staging | **yes** | already covered by `gauss` cold copy | Transient extraction. |
| `photo-staging/iphone13-cameraroll/` | → Immich, then delete from staging | **yes** | raw backup is the cold copy | Transient. |
| `oldmac-unibody/priority/Pictures` (2012) | `archive/oldmac-unibody/` | **yes** | **yes** | |
| `oldmac-salvage/` (keychains, keepassx) | `archive/oldmac-salvage/` | no | **yes** | Secrets — keep, don't index. |
| **Capsule Time Machine** (Feb-2014 gauss) — *future* | `archive/capsule-tm-2014/` (→ dataset) | photos only, after mounting the sparsebundle | **yes** | May contain photos absent elsewhere. |
| **MIGHTY JOE** (NTFS drive) — *turned out to be 125 ripped feature films, **not** home video* | `media/movies/` (dedup vs existing) | no | no | **CONSUMED media**, not `family/`. Draft 1 misclassified this as home movies; live contents are Ocean's Eleven, City of God, the Bourne set, etc. Flow: cold-stage → sha256 byte-compare vs `media/movies` → move net-new → wipe stage. |
| Jellyfin movies/tv/music (already in `media/`) | `media/` (already a dataset hierarchy at 1M) | no | no | Consumed, replaceable. Needs a snapshot task, **not** promotion. |

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
- Purge stray `.DS_Store` (SMB/Finder litter) from the library periodically. One is
  live at the photos root **mis-owned `george:george`** (not `:users`) — exactly the
  drift to clean.

### Immich ↔ NFS uid mapping (why the ownership model actually works)
Immich reads `family/images` as an **external library** over its NFS PV
(`immich-photos-pv-prod`). The export is **not root-squashed** for the cluster, so the
container sees on-disk uids directly: files owned **1028**/**1027** with world-readable
**755 dirs / 644 files** are readable by the Immich pod *regardless of the pod's own
uid* — which is why "owner correct + 755/644" is sufficient and the month-folder is
cosmetic (Immich timelines by EXIF). Keep the export non-squashing (or squashed to a
uid that has read access); if that changes, the per-person ownership stops being
readable and the library goes dark.

### Layout
- Photos/videos: `family/images/photos/<person>/YYYY/MM/<original-name>`, year+month
  from **EXIF capture date** (fallback file mtime).
- Cold archive: **preserve the source's original folder structure verbatim** under
  `archive/<machine-id>/` — do not "tidy" it; faithfulness is the point.

### ZFS properties (defaults per bucket)
| Bucket | compression | recordsize | atime | rationale |
|---|---|---|---|---|
| `family/images`, `family/videos`, `family/audio`, `media/*` *(already set)* | lz4 | **1M** | off | Large, mostly-immutable files; 1M record + lz4 is the right large-file profile. |
| `archive/<machine>` — **per content, not blanket** | lz4 | 1M for large-backup machines; **128K** for config/secret dirs (`oldmac-salvage`, `_tools`) | off | Archive is **mixed**; don't 1M a tree full of tiny keychains / `.info` / configs. |
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
- **`media` is *already* a dataset** (`main/media` + `movies`/`music`/`tv-anime`/`tv-shows`
  children, all 1M). It needs only a **light snapshot class** added — no creation, no
  rsync. Add per-category quotas only if you want to cap a category.
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

### Archive manifest (prove the copy matches the SOURCE before wiping it)
A manifest of the *destination alone proves nothing* — it faithfully hashes a
**truncated** copy too (missing files just don't appear). Completeness =
**source manifest == destination manifest**. So take a manifest **on the mounted
source first**, the destination second, and reconcile before any wipe:
```bash
# 1) SOURCE-SIDE, while the drive is still mounted read-only (e.g. at /src).
#    Write to the PERSISTENT _manifests dir, NOT /tmp: a slow copy can run for DAYS,
#    and /tmp may be cleared (reboot / tmpwatch) before the reconcile runs — which
#    would destroy the source-of-truth. (Source mounted on another host? scp it over.)
find /src -type f -printf "%s\t%P\n" | sort > /mnt/main/archive/_manifests/<id>.src.filelist

# 2) DESTINATION, after the copy completes.
#    NOTE: the reconcile assumes a CONTENTS copy — `rsync /src/ <dst>/` (trailing slash).
#    A no-trailing-slash copy nests everything under src/ and the diff will mis-report.
cd /mnt/main/archive/<machine-id>
sudo find . -type f -printf "%s\t%P\n" | sort > /mnt/main/archive/_manifests/<id>.dst.filelist
sudo find . -type f -exec sha256sum {} + | sort > /mnt/main/archive/_manifests/<id>.sha256

# 3) RECONCILE — must be empty (identical relative paths + sizes on both sides):
diff /mnt/main/archive/_manifests/<id>.src.filelist /mnt/main/archive/_manifests/<id>.dst.filelist && echo COMPLETE

# 4) Only on COMPLETE: write summary + seal snapshot
{ echo "files: $(wc -l < /mnt/main/archive/_manifests/<id>.dst.filelist)";
  echo "bytes: $(du -sb . | cut -f1)"; echo "sealed: $(date -Iseconds)"; } \
  > /mnt/main/archive/_manifests/<id>.summary
sudo zfs snapshot main/archive/<machine-id>@sealed-$(date +%F)   # per-machine dataset once promoted
```
Commit `_manifests/` into the repo so the record survives the NAS. **The drive is safe
to wipe only when the source↔destination `diff` is empty** — not merely when a
manifest exists. (`--exclude` any legitimately-unwanted paths *symmetrically* on both
sides so the diff stays meaningful.)

**Existing gaps to close (execution) — snapshots FIRST, before any wipe:**
0. **NOW, before anything else: add a periodic snapshot task for `main/archive`.** It
   holds **318G of irreplaceable recovery data with zero snapshots** while source drives
   are being wiped this week. This is **decoupled** from the manifest/seal work below and
   must not wait for it.
1. **Add the `main/media` datasets to a light snapshot task** — they already exist; they
   just have zero coverage (no "promotion" needed).
2. Confirm alcatraz replication actually includes `main/family` recursively.

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

0. **NOW — snapshot the unprotected data** (`main/archive` + the `main/media`
   datasets). Irreplaceable cold data must not sit snapshot-less through the wipe
   window; this is independent of everything below and runs immediately.
1. **Finish in-flight copies** (MIGHTY JOE → cold stage → dedup into `media/movies`;
   any archive writes).
2. **Mine photos** from `photo-staging/` (iphone13 camera roll, messages-gauss) and
   from the archive sources per §3, into Immich via §6 (dedupe on).
3. **Verify import** (Immich shows the assets; counts reconcile) — *only then*:
4. **Reclaim `photo-staging/` (82G)** — transient scratch. Snapshot
   `archive@pre-staging-delete` first, then remove. The `gauss/` cold copy and the raw
   iPhone-13 backup remain the sources of truth — **but** re-extracting the iPhone-13
   roll depends on the **retained backup password** (`piano-…`); a lost password makes
   the raw backup inert, so delete only *after* the Immich import is verified (step 3).
5. **Write source↔destination manifests + seal snapshots** (§5) for each finished
   `archive/<machine-id>` **before any source drive is wiped** (the `diff` must be empty).
6. **Promote per-machine `archive/*` to datasets** (create, rsync-in, swap, verify
   manifest), then optionally `family/audio`. **`media` is already datasets — skip it.**
   When swapping any dataset under `family/images/*`, the new **mountpoint must equal
   the old path** or Immich's external-library NFS PV breaks — re-run an Immich Scan and
   confirm the library is intact after each swap.
7. **Retire transients:** rename `family/images/photos-staging-30d` → `_inbox`,
   retire the `iphone-recovery` mount, purge the (mis-owned) `.DS_Store` litter.
8. *(folded into step 0)* — the media + archive snapshot tasks are created up front,
   not last.

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
- **D5 — media per-category quotas.** *(Corrected: `media` is **already** a dataset
  with per-category children at 1M — nothing to promote.)* Only open question: add
  per-category **quotas** (movies/tv/music) to cap a category, or leave unquota'd?
  Unquota'd is simplest given 18.5T free.
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
