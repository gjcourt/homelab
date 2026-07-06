---
status: planned
last_modified: 2026-07-05
summary: "Archive multiple laptops to hestia, mirroring the executed Windows-PC archive. PRIMARY targets are VINTAGE 2010–2012 MacBooks (Snow Leopard/Lion-era), plus a still-older MacBook and a modern MacBook Air. Per machine: living photos → Immich george external library (EXIF-sorted, sha256-deduped, then scan) and a cold archive of everything else → its own ZFS dataset main/archive/<machine-id> (lz4 + snapshot). Vintage path leads with physical capture (Target Disk Mode / drive-pull, ddrescue for failing HDDs) over ancient-SSH network, extracts photos from iPhoto/Aperture packages (not Photos.app/osxphotos), and handles legacy SSH ciphers + HFS+/resource-forks. Modern-Mac path (osxphotos, iCloud rehydrate, FDA, brew rsync) kept as a separate section. Nothing on any source is ever deleted."
---

# Laptop fleet → hestia disk archive (vintage Macs first)

Mirror of the Windows-PC archive we executed, extended to a **fleet of laptops**.
This is a **plan only** — no bytes move until George reviews and greenlights, and
several machine facts are still **TBD** (George is confirming).

The primary near-term targets are **vintage 2010–2012 MacBooks** likely holding the
**oldest photos** — pre-dating the Dropbox Camera Uploads era and the iPhone
backup we already have. Their software stack (Snow Leopard / Lion / Mountain Lion /
Mavericks, iPhoto/Aperture, no APFS, no TCC, ancient SSH) invalidates most of the
modern-macOS assumptions, so the **VINTAGE MAC** section below leads. The modern
MacBook Air path is kept as a separate section because it's still useful.

## Goal (same shape as the Windows job, per machine)

Two outputs from each machine, **nothing deleted from the source**:

1. **Living photo library** — every photo/video pulled off and imported into the
   existing **Immich `george` external library** (`photos.burntbytes.com`),
   EXIF-date-sorted into `/mnt/main/family/images/photos/george/YYYY/MM`,
   **deduplicated (sha256) against the existing library**, then an Immich scan.
   (Shared pipeline — see [The photo pipeline](#the-photo-pipeline-shared).)
2. **Cold archive** — a faithful, snapshotted copy of everything else into a
   **new per-machine ZFS dataset** `main/archive/<machine-id>` (lz4 + post-verify
   snapshot), original folder structure preserved.

Environment (unchanged from the Windows run): hestia = `truenas_admin@10.42.2.10`,
TrueNAS 26.x, passwordless sudo, pool `main` (~26 TB free), **SSH/SFTP key-only
(password auth disabled)**. Archive lands on ZFS `compression=lz4` + a snapshot.

> ⚠️ **Concurrency.** A large archival transfer is *currently writing* to
> `/mnt/main/archive/` (the Windows job / follow-on). Every machine here gets its
> **own child dataset** so snapshots are independent; **do not** touch, list, or
> snapshot any `/mnt/main/archive/` subtree that isn't the machine you're working.

---

## The machine fleet

Cold archive lands **per-machine** under `main/archive/<machine-id>/`, each its own
ZFS dataset (independent compression + snapshots). Known machines:

| machine-id | Machine | LAN / access | Era / OS | Status | Notes |
|---|---|---|---|---|---|
| `winpc-5600x` | Windows desktop (Ryzen 5600X) | — | Windows | **DONE** | Executed; template for this plan. (Prior notes said 5800X; coordinator says 5600X — reconcile, see Q0.) |
| `macbook-2010` | MacBook ~2010–2012 | `10.42.4.26` — **currently off**: ping fails, ssh:22 closed | **TBD** (Snow Leopard→Mavericks likely) | **Primary, unreachable** | Model/year/OS/HDD-vs-SSD/health all TBD. Likely spinning HDD. |
| `macbook-older` | An even older MacBook | TBD (likely off) | **TBD** (older still) | **Primary, TBD** | Oldest photos expected here. Everything TBD. |
| `macbook-air-m3` | This host MacBook Air | `10.42.4.163` (online) | macOS 26.5.1, Apple M3 | Reachable; low-value | Modern path. ~20–40 GiB real payload after excludes. See modern section. |

**Per-machine TBD to collect before touching each (George is gathering):** exact
model / year, macOS version, **boot & drive health** (does it POST? beachball?),
**HDD vs SSD**, and **available ports/adapters** for Target Disk Mode (FireWire 800
vs Thunderbolt vs USB-C, and what cables/adapters exist). Do not invent these.

---

## VINTAGE MAC section (2010–2012 and older) — the primary path

Old laptops over Wi-Fi + ancient SSH are the **least reliable** way to move data,
and a ~2010 spinning drive may be **dying**. So we prioritize **physical capture**
and treat the drive as fragile.

### V1 — Capture method: physical first, network last

Preference order:

#### (a) Target Disk Mode (TDM) — preferred

Boot the old Mac holding **`T`** until the FireWire/Thunderbolt logo floats. It then
presents its internal disk as an **external volume** on a modern Mac — no login, no
old OS booted, no SSH. Mount it on the M3 Air (or any modern Mac) and rsync from the
mounted volume to hestia using the **modern, healthy** machine's tooling.

- **Cable/adapter chain is the friction.** A 2010–2012 MacBook exposes **FireWire
  800** (pre-2012) or **Thunderbolt** (2011–2012). The M3 Air has **USB-C /
  Thunderbolt 4**. The chain is typically:
  `FireWire 800 ↔ (Apple FW800→Thunderbolt 2 adapter) ↔ (Apple Thunderbolt 2→
  Thunderbolt 3 adapter) ↔ USB-C`. Confirm which adapters exist (TBD). A 2010
  white/unibody MacBook may have **FireWire 400 or none** — then use (b).
- Older machines with a Mini-DisplayPort-shaped **Thunderbolt** port still need the
  TB2→TB3 adapter. USB-A on the old Mac is **not** a TDM path (TDM is FW/TB only).

```bash
# On the MODERN Mac, once the old disk mounts (e.g. /Volumes/Macintosh HD):
SRC="/Volumes/Macintosh HD"
DEST=truenas_admin@10.42.2.10:/mnt/main/archive/macbook-2010/
caffeinate -dimsu /opt/homebrew/bin/rsync -aHAX --numeric-ids --partial \
  --info=progress2 --exclude-from=$HOME/vintage-exclude.txt \
  -e "ssh -i ~/.ssh/hestia_archive" "$SRC/" "$DEST" | tee ~/macbook-2010.log
```

`-AX` still matters: HFS+ carries **resource forks + xattrs + Finder metadata**
(see V4). The modern Mac's brew GNU rsync preserves them; do the copy from there.

#### (b) Pull the 2.5" drive

If TDM won't work (no compatible port/adapter, or the Mac won't boot far enough):
remove the internal **2.5" SATA drive** and connect it via a **USB-SATA adapter /
dock** to the modern Mac. It mounts as an external volume; rsync exactly as in (a).
2010–2012 MacBooks use a standard 2.5" SATA drive (a few Torx/Phillips screws).

#### (c) Failing-drive handling — image FIRST with ddrescue

If the drive is a **spinning HDD** (very likely this era) and shows **any** SMART
warning, clicking, or beachballing, **do not rsync off it repeatedly** — each pass
stresses a dying drive and can turn a recoverable disk into a dead one. Instead
**image it once** with `ddrescue`, then extract from the image.

```bash
# On a Linux box or the Mac (brew install ddrescue). Connect the old drive via
# USB-SATA (or the TDM volume's raw device). Identify it (diskN on macOS, sdX on
# Linux) — be certain, this reads a raw block device.
#   macOS:  diskutil list        Linux: lsblk
# Image with a mapfile so a re-run RESUMES and retries bad sectors gently:
ddrescue -d -r3 /dev/rdisk4 /mnt/main/archive/macbook-2010/disk.img \
  /mnt/main/archive/macbook-2010/disk.mapfile     # write straight to hestia (NFS/SMB) or local then push
# First pass copies the easy blocks fast; -r3 then retries the hard ones 3×.
```

Then **loop-mount the HFS+ image read-only** and run the photo pipeline + a
file-level rsync *from the mount* (so hestia holds both the raw `.img` for forensic
completeness **and** a browsable file tree):

```bash
# Linux (hestia or a helper): needs hfsprogs / the hfsplus kernel module.
losetup -fP --show /path/disk.img        # -> /dev/loopN, -P maps partitions
mount -t hfsplus -o ro /dev/loopNp2 /mnt/oldmac   # partition index varies; check `fdisk -l`
# macOS: hdiutil attach -readonly disk.img   (HFS+ mounts natively)
```

Notes: a modern Linux kernel mounts **HFS+** read-only reliably; **journaled** HFS+
sometimes needs `-o force,ro`. macOS mounts HFS+ natively (`hdiutil attach`).
**APFS does not apply** to this era. If the volume was FileVault-1 (rare, per-user
sparsebundle) or FileVault-2 (2011+ full-disk) encrypted, the image is ciphertext
and needs the unlock password — flag as a per-machine TBD.

### V2 — Network fallback (only if physical capture is impossible)

If the machine boots fine, has a healthy SSD/HDD, and no cable path exists, pull
over the network — but expect friction:

- **Enable Remote Login** on the old Mac: System Preferences → Sharing → **Remote
  Login** (on). Or `sudo systemsetup -setremotelogin on`.
- **Legacy SSH algorithms.** A 2010-era `sshd` offers ciphers/KEX that modern
  OpenSSH (on the M3 Air / hestia) **rejects by default** ("no matching key exchange
  method" / "no matching cipher"). Re-enable them explicitly **on the modern
  client**:

  ```bash
  ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 \
      -oHostKeyAlgorithms=+ssh-rsa \
      -oPubkeyAcceptedAlgorithms=+ssh-rsa \
      -oCiphers=+aes128-cbc \
      -oMACs=+hmac-sha1 \
      george@10.42.4.26
  ```

  Wrap the same `-o` flags into rsync's transport: `rsync -e "ssh -oKexAlgorithms=+…"`.
- **Old rsync protocol.** The vintage Mac's `/usr/bin/rsync` is ancient (protocol
  ~28–29). Point the modern side at it and let protocol negotiate down; if it fails,
  `rsync --rsync-path=/usr/bin/rsync --protocol=29 …`. If the old rsync is too
  broken, fall back to `scp -O -r` or `tar cf - … | ssh … 'cat > file.tar'`.
- **Pull TO the modern Mac or hestia, not the other way** — never run a long,
  drop-prone job *on* the dying laptop as the active endpoint. Direction:
  `rsync … george@10.42.4.26:/Users/ /staging/` then push staging → hestia. (Or
  rsync straight to hestia if the old Mac can reach it, but staging on the healthy
  machine is safer for resume.)
- Same **caffeinate**/no-sleep and **wired-preferred** concerns as any long
  transfer — but if you're at V2 you've already accepted the reliability hit.

### V3 — Photos on vintage Macs = iPhoto / Aperture (NOT Photos.app)

`osxphotos` and Photos.app **do not exist / do not apply** here. 2010–2012 Macs use
**iPhoto** (`~/Pictures/iPhoto Library`) and possibly **Aperture**
(`~/Pictures/Aperture Library`). Both are **packages**; originals live in dated
folders inside:

- iPhoto: `iPhoto Library/Masters/YYYY/…` (older iPhoto: `Originals/YYYY/…`).
- Aperture: `Aperture Library/Masters/YYYY/MM/DD/…`.

Extract originals by copying the `Masters/`/`Originals/` tree (they're real files),
then feed them into the **same** EXIF-sort + sha256-dedup + Immich pipeline
([below](#the-photo-pipeline-shared)). Also loose images anywhere under `~/Pictures`,
`~/Desktop`, `~/Documents`, old `Photo Booth` libraries, and any `.dmg`/`.zip` of a
prior machine's photos.

```bash
# From the TDM-mounted / drive-pulled / ddrescue-mounted OLD volume:
OLD="/Volumes/Macintosh HD/Users/<olduser>"
# Copy every real original out of the iPhoto/Aperture packages + loose images:
rsync -a --prune-empty-dirs \
  --include='*/' \
  --include='*.jpg' --include='*.jpeg' --include='*.png' --include='*.tif' \
  --include='*.tiff' --include='*.heic' --include='*.gif' --include='*.bmp' \
  --include='*.cr2' --include='*.nef' --include='*.dng' --include='*.raf' \
  --include='*.mov' --include='*.avi' --include='*.mp4' --include='*.m4v' \
  --include='*.3gp' \
  --exclude='*' \
  "$OLD/Pictures/" ~/oldmac-photo-export/
# Then run the shared pipeline on ~/oldmac-photo-export (EXIF sort + sha256 dedup).
```

**Expected date coverage + why dedup matters.** These machines likely hold the
**oldest** photos (roughly **pre-2012**), *complementary* to what we already have:

| Source | Approx era |
|---|---|
| Vintage MacBooks (iPhoto/Aperture) | **oldest — pre-2010 to ~2012** |
| Dropbox Camera Uploads | ~2010–2018 |
| iPhone backup (recovered) | ~2021–2024 |

There **will** be overlap at the boundaries (e.g. a 2010–2012 photo that's also in
Dropbox), so the **sha256 content dedup against the existing Immich library is
essential** — it prevents re-importing a photo we already pulled from Dropbox or the
iPhone backup, even if the filename differs. EXIF dates on very old scans/imports can
be missing or wrong; the pipeline falls back to file mtime and reports unsorted files
for manual triage (don't silently dump them in the wrong month).

### V4 — Filesystem quirks (no TCC to worry about, but HFS+ has its own)

- **No TCC / Full Disk Access** on this era — reading Mail/Messages/photos needs no
  privacy grant. Simpler than modern macOS. (If pulling live over SSH, normal Unix
  file perms still apply — run as an admin user or the file owner.)
- **HFS+**, not APFS. Copy with `-AX` to preserve **resource forks + extended
  attributes + Finder metadata** (labels, custom icons, type/creator codes). GNU
  rsync on the modern side handles the AppleDouble encoding; native `openrsync`
  would drop them.
- **Case-sensitivity**: HFS+ is usually case-**insensitive** but *can* be
  case-sensitive (HFSX). If two files differ only by case, a case-insensitive
  staging/destination collapses them — land the archive on ZFS (case-sensitive by
  default) and avoid an intermediate case-insensitive macOS volume for staging where
  possible. Note as a low-probability gotcha.
- **Old permissions / uids**: use `--numeric-ids`; don't remap to hestia's user
  table. These are archives, not live-served trees.
- Messages/Mail here are **iChat** (`~/Library/Application Support/iChat`) and
  **Mail** (`~/Library/Mail`, old `.mbox`/`.emlx`) — include them (see the shared
  include list); no chat.db yet on the oldest OSes.

### V5 — Vintage cold-archive dataset + verify

```bash
# Own dataset per old machine (independent snapshots; do NOT touch sibling datasets):
ssh truenas_admin@10.42.2.10 \
  "sudo zfs create -o compression=lz4 main/archive/macbook-2010 2>/dev/null; \
   zfs get -H -o value compression,mountpoint main/archive/macbook-2010"
# ... rsync from the mounted old volume (V1) ...
# Verify: dry-run reconcile ~0 to move, du/count spot-check, manifest, snapshot:
ssh truenas_admin@10.42.2.10 \
  "cd /mnt/main/archive/macbook-2010 && find . -type f -printf '%p\t%s\n' | sort \
   > MANIFEST-$(date +%F).tsv; \
   sudo zfs snapshot main/archive/macbook-2010@archived-$(date +%F)"
```

If the machine was ddrescue-imaged, keep **both** the raw `disk.img`+`mapfile` and
the extracted file tree in the dataset, and record the ddrescue rescue-rate (bytes
recovered / total) in the manifest so we know if any sectors were unreadable.

---

## MODERN MAC section (this host MacBook Air, macOS 26.x)

Kept because it's still useful — this is the original modern-macOS plan. Only run
this path against a **modern** machine (APFS, Photos.app, TCC). Facts measured on
this host 2026-07-05:

| Property | Value |
|---|---|
| Hostname / id | `Georges-MacBook-Air.local` → `macbook-air-m3` |
| Model / OS | `Mac15,12` (MacBook Air 15" M3) / macOS 26.5.1 |
| Data volume used | 459 GiB of 926 GiB — but real payload after excludes ~20–40 GiB |
| FileVault | On (transparent while unlocked — no special handling) |
| Photos library | `~/Pictures/Photos Library.photoslibrary`, 15 GiB |
| Native rsync | `openrsync` proto 29 — **no `-AX`/`--partial`**; use brew GNU rsync |
| Wired Ethernet | `en3`/`en4` USB/TB adapters present |

Modern-path decisions (full rationale unchanged from the first draft):

1. **rsync over rclone, GNU rsync from brew.** `/usr/bin/rsync` is `openrsync`
   (no `-A`/`-X`/`--partial`). `brew install rsync` → `rsync -aHAX --numeric-ids
   --partial --append-verify --info=progress2` over key-only SSH. rclone-SFTP = plan B.
2. **Photos via `osxphotos`.** `.photoslibrary` is a package + SQLite DB.
   `osxphotos export ~/mac-photo-export --download-missing --use-photokit --exiftool
   --update`, then the shared pipeline. Whole package also goes in the cold archive.
3. **iCloud "Optimize Mac Storage"** may leave originals as **dehydrated
   placeholders** (the #1 modern gotcha, analog of Windows cloud-placeholder reparse
   points). Rehydrate first (Photos → Download Originals, or `--download-missing`).
4. **Full Disk Access (TCC)** for the terminal/rsync binary or `~/Library/Mail`,
   `Messages/chat.db`, `Application Support`, Safari, Photos silently fail to read.
   Grant + verify: `head -c1 ~/Library/Messages/chat.db >/dev/null && echo FDA OK`.
5. **caffeinate -dimsu + wired + lid open on AC** against sleep-drop.
6. **FileVault** transparent while unlocked — no special handling.
7. **Time Machine** to a hestia SMB share is a fine *ongoing* complement, but it's
   opaque + macOS-only — not this browsable ZFS-tree + Immich job. Follow-on only.

Modern-path commands (dataset `main/archive/macbook-air-m3`):

```bash
brew install rsync osxphotos exiftool
DEST=truenas_admin@10.42.2.10:/mnt/main/archive/macbook-air-m3/
# dry-run for the REAL size estimate, then detached+caffeinated real run:
nohup caffeinate -dimsu /opt/homebrew/bin/rsync -aHAX --numeric-ids --partial \
  --append-verify --info=progress2 --exclude-from=$HOME/mac-archive-exclude.txt \
  -e "ssh -i ~/.ssh/hestia_archive" "$HOME/" "$DEST" > ~/macbook-air.log 2>&1 &
```

---

## The photo pipeline (shared)

Every machine's extracted photos funnel through the **same** steps into Immich, so
dedup is global across the whole fleet + Dropbox + the iPhone backup:

1. **Extract originals** to a local `*-photo-export/` dir — method differs by
   machine (iPhoto/Aperture `Masters/` copy for vintage; `osxphotos` for modern;
   direct file copy for loose images).
2. **EXIF-sort into `YYYY/MM`** reusing `scripts/import-sd-photos.sh` (exiftool
   `DateTimeOriginal > CreateDate > FileModifyDate`), which already dry-runs, reports
   the date distribution, and does a **name-collision** check with
   `rsync --ignore-existing`.
3. **sha256 content dedup** vs the existing library (adds to the name check —
   catches renamed dupes across Dropbox/iPhone/other machines):

   ```bash
   ssh truenas_admin@10.42.2.10 \
     "find /mnt/main/family/images/photos/george -type f -print0 | xargs -0 sha256sum" \
     | awk '{print $1}' | sort -u > ~/existing.sha256
   # hash the sorted export; import only files whose sha256 is NOT in existing.sha256
   ```

4. **Import net-new** into `/mnt/main/family/images/photos/george/YYYY/MM`, then
   **trigger an Immich external-library scan** of `george` so new files index.

Files with no readable date are **reported, not dumped** — manual triage keeps very
old undated scans out of the wrong month.

---

## Include / exclude

**Include (cold archive):** `~/Documents`, `~/Desktop`, `~/Downloads`, `~/Pictures`
(incl. iPhoto/Aperture/Photos packages — also feeds Immich), `~/Movies`, `~/Music`
(incl. the iTunes/Music library DB), code repos, and **selective `~/Library`**:
`Mail`, `Messages`/`iChat`, `Application Support` (minus caches), `Preferences`,
`Keychains` (encrypted — see Q4), `Safari`. Plus dotfiles: `~/.ssh` (Q4),
`~/.gitconfig`, shell rc, `~/.config`. On vintage machines, whatever exists of these
(paths shift by OS era).

**Exclude (junk / regenerable)** — rsync `--exclude-from`, paths relative to the
copied home/volume:

```text
# trash / system cruft
.Trash/
**/.DS_Store
._*
.Spotlight-V100/
.fseventsd/
.TemporaryItems/
.DocumentRevisions-V100/
# caches (regenerable)
Library/Caches/
Library/Containers/*/Data/Library/Caches/
Library/Group Containers/*/Library/Caches/
Library/Logs/
Library/HTTPStorages/
Library/Application Support/*/Cache/
Library/Application Support/CrashReporter/
# developer build junk (modern)
**/node_modules/
**/.venv/
**/__pycache__/
**/target/
**/.next/
**/dist/
**/build/
Library/Developer/Xcode/DerivedData/
Library/Developer/Xcode/iOS DeviceSupport/
Library/Developer/CoreSimulator/
# package-manager caches
Library/Caches/Homebrew/
.npm/
.cargo/registry/
.gradle/caches/
.cache/
# VM / container disk images (huge, regenerable)
Library/Containers/com.docker.docker/
.docker/
**/*.vmdk
**/*.qcow2
# regenerable Photos/iPhoto derivatives (originals extracted separately)
Pictures/Photos Library.photoslibrary/resources/
Pictures/Photos Library.photoslibrary/derivatives/
Pictures/iPhoto Library/Thumbnails/
Pictures/iPhoto Library/Previews/
Pictures/Aperture Library/Thumbnails/
Pictures/Aperture Library/Previews/
```

> The photo-library **packages are kept in the archive** (albums/edits survive), but
> their regenerable `Thumbnails/`/`Previews/`/`derivatives/` are dropped. Keep
> `Masters/`/`Originals/`/`originals/` + the library DB. Drop these excludes for a
> 100%-faithful package copy (Q5).

---

## Phases (per machine)

1. **Prereqs/inputs** — collect the per-machine TBD (model, OS, boot/drive health,
   HDD vs SSD, ports/adapters). Decide capture method (V1a/V1b/V1c or V2 for vintage;
   modern section for the Air). Pick `machine-id` + create its dataset.
2. **Prep** — vintage: assemble the TDM cable chain OR a USB-SATA adapter; if HDD is
   suspect, `brew/apt install gddrescue` and image first (V1c). Modern: brew rsync +
   osxphotos + FDA + iCloud rehydrate. Both: append this run's SSH key to hestia
   `truenas_admin` `authorized_keys` **preserving existing keys**.
3. **Cold-archive transfer** — rsync `-aHAX --numeric-ids --partial` from the
   mounted/imaged volume (vintage) or `$HOME` (modern), `--exclude-from`, detached +
   caffeinated, resumable. Never repeatedly rsync a dying drive — image it (V1c).
4. **Photo extraction → shared pipeline** — extract originals (iPhoto/Aperture
   `Masters/` for vintage; osxphotos for modern) → EXIF-sort → sha256 dedup vs
   existing Immich → import net-new → Immich scan.
5. **Verify + snapshot + manifest** — dry-run reconcile (~0 to move), du/count
   spot-check, write `MANIFEST-<date>.tsv` (+ ddrescue rescue-rate if imaged), then
   `zfs snapshot main/archive/<machine-id>@archived-<date>`. **Nothing on the source
   is deleted.**

---

## Rough estimates

- **Vintage payloads**: 2010–2012 MacBook internal drives were typically
  **160–500 GB HDDs**, often far from full — real photo/doc payload plausibly
  **tens of GiB**; a full ddrescue image is the whole drive capacity (budget up to
  the drive size on hestia; ~26 TB free makes it a non-issue).
- **Time**: TDM/drive-pull over FW800 (~80 MB/s) or SATA-USB (~100+ MB/s): a
  200 GB drive images in **~30–60 min**; a selective rsync of tens of GiB is
  **minutes**. A ddrescue pass over bad sectors can take **hours** (that's the
  point). Network (V2) over ancient SSH: slow + drop-prone, hence last resort.
- **Modern Air**: ~20–40 GiB after excludes, **~20–45 min** wired (small-file
  overhead dominates). Confirm all sizes with the Phase-3 dry-run.

---

## Open questions / inputs needed (TBD — not invented)

0. **Q0 — Windows machine id**: prior notes said `winpc-5800x`; coordinator says
   **5600X**. Which CPU / dataset name is correct? (Cosmetic; reconcile the label.)
1. **Q1 — Per-machine facts** for `macbook-2010` and `macbook-older`: exact model,
   year, macOS version, **boot status**, **HDD vs SSD**, **drive health** (SMART /
   beachballing), and **available TDM ports/adapters** (FW800 / Thunderbolt / USB-C
   chain). All currently TBD — drives the capture-method choice.
2. **Q2 — Physical access**: can we get the old Macs powered + a modern Mac + the
   right cables in one place (TDM), or should we plan to **pull the drives**?
3. **Q3 — Encryption**: were any of the old volumes FileVault-encrypted? If so we
   need the unlock password before an image is usable.
4. **Q4 — Sensitive dirs**: OK to archive `~/.ssh` private keys + `~/Library/
   Keychains` to hestia (yours, on your NAS)? Easy to exclude if not.
5. **Q5 — Photo-package fidelity**: drop regenerable `Thumbnails/Previews/
   derivatives`, or archive iPhoto/Aperture/Photos packages 100%?
6. **Q6 — Immich `george` library** root still `/mnt/main/family/images/photos/
   george/YYYY/MM` + external-scan trigger correct?
7. **Q7 — Order**: which old Mac first? (Suggest the more reachable/healthier one to
   validate the pipeline before touching a fragile drive.)
8. **Q8 — Time Machine** follow-on for the modern Air — set up separately, or skip?

---

## Not doing (scope guard)

- **No deletion** on any source machine, ever, in this plan.
- **No touching** other `/mnt/main/archive/` datasets — a concurrent job is writing
  there; each machine is confined to its own dataset.
- **No repeated rsync off a suspected-dying drive** — image once with ddrescue, then
  extract from the image.
- **No merge of album/edit structure** into Immich — flat originals import only;
  album structure survives via the cold-archive copy of the package.
