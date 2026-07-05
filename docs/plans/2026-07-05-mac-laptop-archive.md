---
status: planned
last_modified: 2026-07-05
summary: "Archive a Mac laptop's disk to hestia, mirroring the Windows-PC archive: (1) living photos → Immich george external library (EXIF-sorted, sha256-deduped, then scan), (2) cold archive of everything else → new ZFS dataset main/archive/<mac-hostname> (lz4 + snapshot). macOS specifics: brew GNU rsync over SSH (native is openrsync, no -AX/--partial), Full Disk Access for Mail/Messages/Photos, caffeinate against lid-sleep, wired Ethernet, and downloading iCloud 'Optimize Mac Storage' placeholders before the photo extract. Nothing on the Mac is ever deleted."
---

# Mac laptop → hestia disk archive

Mirror of the Windows-PC archive we just executed, adapted to macOS. This is a
**plan only** — no bytes move until George reviews and greenlights.

## Goal (same shape as the Windows job)

Two outputs from the Mac, nothing deleted from the source:

1. **Living photo library** — every photo/video pulled off the Mac and imported
   into the existing **Immich `george` external library** (`photos.burntbytes.com`),
   EXIF-date-sorted into `/mnt/main/family/images/photos/george/YYYY/MM`,
   **deduplicated (sha256) against the existing library** so nothing is imported
   twice, then an Immich library scan picks up the net-new files.
2. **Cold archive** — a faithful, snapshotted copy of *everything else*, into a
   **new ZFS dataset** on hestia (`main/archive/<mac-hostname>`, lz4 + a snapshot
   after verify), preserving the original folder structure. Windows landed at
   `/mnt/main/archive/winpc-5800x/`; the Mac lands at
   `/mnt/main/archive/georges-macbook-air/` (parallel path, this-host default —
   see open question Q1).

Environment (unchanged from the Windows run):

- hestia = `truenas_admin@10.42.2.10`, TrueNAS 26.x, passwordless sudo, pool
  `main` (~26 TB free), **SFTP + SSH enabled, password auth DISABLED (key-only)**.
- The archive lands on ZFS with `compression=lz4` and gets a post-verify snapshot.

> ⚠️ **Concurrency note.** A large data-archival transfer is *currently writing*
> to `/mnt/main/archive/` on hestia (the Windows job or a follow-on). This plan
> uses a **distinct child dataset** and must not touch, list-verify, or snapshot
> anything under `/mnt/main/archive/` that isn't the Mac's own subtree. Create
> `main/archive/georges-macbook-air` as its **own dataset** so its snapshots are
> independent.

## The specific Mac (measured 2026-07-05)

Facts pulled from the host this plan was written on:

| Property | Value |
|---|---|
| Hostname | `Georges-MacBook-Air.local` (dataset slug `georges-macbook-air`) |
| Model | `Mac15,12` (MacBook Air 15", M3) |
| macOS | 26.5.1 |
| LAN IP | 10.42.4.163 (this is the host Mac — see Q1) |
| Data volume used | **459 GiB** of 926 GiB (51%) |
| FileVault | **On** |
| Photos library | `~/Pictures/Photos Library.photoslibrary`, **15 GiB** |
| `~/Downloads` | 8.9 GiB · `~/Documents` 12 MiB · `~/Desktop` 712 KiB · `~/Movies` 0 · `~/Music` 0 |
| `~/Library/Caches` | 13 GiB (exclude) · `~/Library/Containers` 9.8 GiB (mostly exclude) |
| Native rsync | `openrsync protocol 29` — **not** GNU rsync (see Decision 1) |
| rclone / osxphotos | not installed; **brew present** (`/opt/homebrew/bin/brew`) |
| Wired Ethernet | `en3`/`en4` USB/TB Ethernet adapters present + Thunderbolt Bridge |

**The 459 GiB is misleading.** The identified user payload (`~/Documents`,
`~/Desktop`, `~/Downloads`, `~/Pictures`, code) is well under ~40 GiB. The rest is
`~/Library` caches, app containers, Docker/VM disk images, and system data — most
of which the exclude list drops. **Expect the real cold-archive payload to be tens
of GiB, not 459 GiB.** Confirm with a dry-run byte count (Phase 5) before trusting
any estimate. (This machine may hold little of value; the plan still stands as the
reusable template for a *personal* laptop with more on it — see Q1.)

---

## Key decisions

### Decision 1 — rsync-over-SSH (GNU rsync from brew), not rclone-SFTP

**Recommendation: `rsync -aHAX --numeric-ids --partial --info=progress2` over SSH**,
using **GNU rsync installed via Homebrew**, not the native binary.

- macOS 26 ships **`openrsync` (protocol 29)** as `/usr/bin/rsync`. openrsync does
  **not** implement `-A` (ACLs), `-X` (extended attributes / resource forks), or
  `--partial`/`--info=progress2`. Using it would silently drop macOS metadata and
  can't resume a dropped transfer — exactly what we need on a laptop that may sleep.
- `brew install rsync` gives **GNU rsync 3.x** with full `-AX`, `--partial`
  (resume a half-sent file), `--append-verify`, and `--info=progress2`. hestia's
  rsync is modern, so protocol negotiation is fine.
- `-AX` matters on macOS: it preserves **extended attributes and AppleDouble
  resource forks** (Finder tags, quarantine flags, custom icons). For a *faithful*
  cold archive this is the correct default. (`-H` keeps hardlinks; `--numeric-ids`
  avoids uid/gid remapping against hestia's user table.)
- **Resumability**: rsync is idempotent — re-running the same command after a drop
  transfers only what's missing/changed. That's the laptop-sleep insurance. rclone
  SFTP also resumes, but we already have the metadata-fidelity reason to prefer
  rsync, and rsync-over-SSH is one fewer moving part (no rclone config, no
  `remote:` mapping) than the Windows job needed.
- **Why not rclone here** (we used it on Windows): Windows had no good native
  rsync and file-metadata fidelity mattered less (NTFS ACLs weren't the point).
  On macOS the native-rsync trap + xattr/resource-fork fidelity flips the choice.
  rclone-SFTP remains a fine fallback if brew rsync misbehaves against hestia's
  sshd; note it as plan B.
- **SSH is key-only on hestia.** Append this Mac's public key to
  `truenas_admin`'s `authorized_keys` **preserving existing keys** (Phase 2), same
  as Windows. rsync then rides that SSH session (`rsync -e ssh`).

### Decision 2 — Photos: extract originals with `osxphotos`, feed the same EXIF-sort + sha256 dedup pipeline

- `~/Pictures/Photos Library.photoslibrary` is a **package**, not a folder of
  images. Originals live under `.../originals/` (UUID-named subfolders), but albums,
  edits, faces, and capture dates live in a SQLite DB (`database/Photos.sqlite`).
  Copying `originals/` raw would lose the human-readable filenames and the reliable
  capture date, and would mix in `.plist`/derivative junk.
- **Recommendation: `brew install osxphotos`** and export originals with their real
  filenames + sidecar metadata:

  ```bash
  osxphotos export ~/mac-photo-export \
    --download-missing --use-photokit \
    --exiftool \
    --skip-original-if-edited=false \
    --update
  ```

  `--exiftool` writes the library's capture date/GPS back into each exported file's
  EXIF so our existing EXIF-sort step (below) puts it in the right `YYYY/MM`.
  `--download-missing` pulls any iCloud-dehydrated originals (Decision 3).
  `--update` makes the export idempotent/resumable.
- Then feed the export dir through the **same pipeline the SD-card importer uses**
  (`scripts/import-sd-photos.sh`): exiftool sorts into `YYYY/MM`, and we import
  net-new only. That script already dedups **by path/name** (`rsync
  --ignore-existing` + a `comm` collision report). **This plan adds a sha256
  content dedup** on top, to match the Windows job and catch renamed duplicates:

  ```bash
  # Build a content-hash index of the existing library ONCE (on hestia):
  ssh truenas_admin@10.42.2.10 \
    "find /mnt/main/family/images/photos/george -type f -print0 \
     | xargs -0 sha256sum" | awk '{print $1}' | sort -u > existing.sha256
  # Hash the sorted export locally, import only hashes NOT already present.
  ```

  Net-new files (hash absent from `existing.sha256`) go to
  `/mnt/main/family/images/photos/george/YYYY/MM`; everything else is skipped.
- **Finally trigger an Immich library scan** of the `george` external library so
  the new files are indexed (external-library scan via Immich UI or API, same as
  the Windows import).
- The **whole `.photoslibrary` package is *also* captured in the cold archive**
  (Phase 3) as-is, so albums/edits/faces are recoverable even though the living
  library only gets flat originals. Belt and suspenders.

### Decision 3 — download iCloud "Optimize Mac Storage" placeholders FIRST (the #1 macOS gotcha)

Analogous to the Dropbox/OneDrive cloud-placeholder reparse points we hit on
Windows. If **Photos → Settings → iCloud → "Optimize Mac Storage"** is on, most
originals on disk are **dehydrated placeholders** — the 15 GiB on-disk is not the
full library. Copying them archives thumbnails, not photos.

- **Verify state** (Q3): Photos → Settings → iCloud. If it says "Optimize", the
  library is partially in the cloud.
- **Rehydrate before extract**, either:
  - Photos app → Settings → iCloud → **"Download Originals to this Mac"**, wait for
    the download to finish (can be large / slow), **or**
  - let `osxphotos export --download-missing` pull them on demand (preferred — it's
    scriptable and only fetches what it exports).
- Do **not** start the photo extract until originals are local, or the archive is
  silently incomplete.

### Decision 4 — Full Disk Access (TCC) for the binary that runs the copy

Reading `~/Library/Mail`, `~/Library/Messages/chat.db`, `~/Library/Application
Support`, Safari data, and the Photos library requires the terminal app (Terminal
/ iTerm) **and the rsync/osxphotos binaries** to have **Full Disk Access** under
**System Settings → Privacy & Security → Full Disk Access**. Without it, those
dirs **silently fail to read** (rsync logs permission errors at best, or just skips).

- Grant FDA to **Terminal (or iTerm)** — the process that spawns rsync inherits it.
- **Verify** before the real run:
  ```bash
  head -c1 ~/Library/Messages/chat.db >/dev/null && echo "FDA OK: chat.db readable"
  ls ~/Library/Mail >/dev/null 2>&1 && echo "FDA OK: Mail readable"
  ```
  If either fails, FDA isn't effective yet (a full quit+relaunch of the terminal
  is often required after toggling).

### Decision 5 — caffeinate + wired Ethernet (laptops sleep and drop transfers)

We hit exactly this on Windows (sleep dropped the transfer). A laptop is worse:
lid-close sleeps it.

- Wrap the long transfer in **`caffeinate -dimsu`** (`-d` display, `-i` idle,
  `-m` disk, `-s` system-on-AC, `-u` user-active). Run the transfer as an argument
  to caffeinate so caffeination lasts exactly the transfer's lifetime:
  ```bash
  caffeinate -dimsu rsync -aHAX ... 2>&1 | tee ~/mac-archive.log
  ```
- **Clamshell (lid closed)**: `caffeinate` keeps the *system* awake, but
  closing the lid on battery still sleeps. Keep the **lid open**, or run on AC power
  with an external display, for the duration. Simplest: leave it open on a desk.
- **Wired strongly preferred** for hundreds of GiB: use the `en3`/`en4` USB/TB
  Ethernet adapter, not Wi-Fi (Wi-Fi drops + is ~5–10× slower and flakier over
  multi-hour transfers). Confirm the Mac and hestia are on the same L2/routable
  path (10.42.x). Given the measured payload is likely tens of GiB, even Wi-Fi
  would finish, but wired removes the drop risk.

### Decision 6 — FileVault: no special handling

FileVault is **On**. It encrypts at rest but is transparent while the Mac is
unlocked and logged in — files read normally, rsync sees plaintext. **No special
handling for the copy.** (Worth stating so nobody worries about it.) The *archive
on hestia* is protected by ZFS-on-encrypted-pool / LAN perimeter, not FileVault.

### Decision 7 — Time Machine is a complement, not the plan

Time Machine to a hestia SMB share is a fine *ongoing* backup and worth setting up
separately, but it produces an opaque, macOS-only sparsebundle — **not** the
browsable, faithful, cross-platform ZFS tree + Immich-indexed photos this job
wants. **Primary plan = rsync-to-ZFS + Immich**, mirroring Windows. Mention TM only
as a follow-on for continuous protection.

---

## Include / exclude

### Include (cold archive)

- `~/Documents`, `~/Desktop`, `~/Downloads`
- `~/Pictures` (the whole `.photoslibrary` package + any loose images) — *also*
  feeds the Immich living-library import
- `~/Movies`, `~/Music` (note: **Music.app** stores its library at
  `~/Music/Music/Media.localized/` with an XML/DB index at `~/Music/Music/`; capture
  the whole `~/Music` tree so purchased/imported tracks + the library DB come along)
- Code repos: `~/src` (and any other repo roots) — **but** exclude build junk (below)
- `~/Library/` **selectively** — the personal data worth keeping:
  - `~/Library/Mail` (local mailboxes) — **first-class personal data**, needs FDA
  - `~/Library/Messages` incl. `chat.db`, `chat.db-wal`, `Attachments/` —
    **first-class personal data**, needs FDA
  - `~/Library/Application Support/` **minus** the cache-like children (exclude
    list trims the heavy junk)
  - `~/Library/Preferences/` (app settings / plists)
  - `~/Library/Keychains/` (login keychain — encrypted; archive for completeness,
    note it's only useful with the login password)
  - `~/Library/Safari/`, `~/Library/Containers/com.apple.Safari/` (history/bookmarks)
- Dotfiles: `~/.ssh` (keys — sensitive, but this is *your* archive on *your* NAS),
  `~/.gitconfig`, `~/.zshrc`/`~/.zprofile`, `~/.config`, `~/.aws`, `~/.kube`

### Exclude (junk / regenerable / huge-and-worthless)

Use an rsync `--exclude-from=exclude.txt`. Paths are relative to the transfer root
(`$HOME`):

```text
# --- trash / system cruft ---
.Trash/
.DS_Store
**/.DS_Store
._*
**/.localized
.Spotlight-V100/
.fseventsd/
.TemporaryItems/

# --- caches (regenerable, ~13 GiB here) ---
Library/Caches/
Library/Containers/*/Data/Library/Caches/
Library/Group Containers/*/Library/Caches/
Library/Application Support/*/Cache/
Library/Application Support/*/*Cache*/
Library/Application Support/CrashReporter/
Library/Logs/
Library/HTTPStorages/

# --- developer build artifacts ---
**/node_modules/
**/.venv/
**/venv/
**/__pycache__/
**/target/            # rust/maven
**/.next/
**/dist/
**/build/
Library/Developer/Xcode/DerivedData/
Library/Developer/Xcode/iOS DeviceSupport/
Library/Developer/CoreSimulator/          # simulator runtimes/devices, tens of GiB
Library/Developer/Xcode/Archives/         # keep? see Q5

# --- package-manager caches ---
Library/Caches/Homebrew/
.npm/
.cargo/registry/
.gradle/caches/
Library/Caches/pip/
.cache/

# --- VM / container disk images (huge, regenerable; archive separately if wanted) ---
Library/Containers/com.docker.docker/     # Docker.raw, often 20-60 GiB
.docker/
**/*.vmdk
**/*.qcow2

# --- cloud placeholders already handled via rehydrate; never copy sync temp ---
Library/CloudStorage/*/.*                 # provider temp/state
**/.dropbox.cache/

# --- iCloud Photos derivatives (originals go via osxphotos, not raw copy) ---
Pictures/Photos Library.photoslibrary/resources/
Pictures/Photos Library.photoslibrary/derivatives/
```

> The `.photoslibrary` is included in the archive but its **`resources/` and
> `derivatives/`** (regenerable thumbnails/renders) are excluded to save space,
> while `originals/` + `database/` are kept — so albums/edits are still
> reconstructable. If you'd rather archive the package 100% faithfully, drop those
> two exclude lines (costs a few GiB). See Q5.

---

## Phases (mirror the Windows execution)

### Phase 1 — Prereqs / inputs

Confirm on the target Mac and record in the run log:

- Hostname, macOS version, **data-volume used size** (`df -h /System/Volumes/Data`).
- **iCloud Photos**: on? "Optimize Mac Storage" or "Download Originals"? (Q3)
- FileVault status (informational; measured On).
- **Wired Ethernet available?** Which adapter (`en3`/`en4`)?
- **Which Mac** — this host (10.42.4.163) or a separate personal laptop? (Q1)
- Dataset slug from hostname: `georges-macbook-air`.

### Phase 2 — Prep

```bash
# tooling
brew install rsync osxphotos exiftool     # GNU rsync 3.x, not openrsync
/opt/homebrew/bin/rsync --version | head -1   # confirm "rsync  version 3.x"

# Full Disk Access: System Settings > Privacy & Security > Full Disk Access
#   -> add Terminal/iTerm, then FULLY QUIT + relaunch it. Verify:
head -c1 ~/Library/Messages/chat.db >/dev/null && echo "FDA OK"

# iCloud originals (if Optimize is on) — download before extract:
#   Photos > Settings > iCloud > Download Originals   (or rely on --download-missing)

# hestia: create the dataset (its OWN dataset, independent snapshots) + confirm key auth.
#   Do NOT touch other children of main/archive (concurrent job writing there).
ssh truenas_admin@10.42.2.10 \
  "sudo zfs create -o compression=lz4 main/archive/georges-macbook-air 2>/dev/null; \
   zfs get -o value -H compression,mountpoint main/archive/georges-macbook-air"

# Append THIS Mac's key to truenas_admin authorized_keys, preserving existing:
ssh-keygen -t ed25519 -f ~/.ssh/hestia_archive -N '' -C mac-archive   # if no key yet
ssh-copy-id -i ~/.ssh/hestia_archive.pub truenas_admin@10.42.2.10 || \
  cat ~/.ssh/hestia_archive.pub | ssh truenas_admin@10.42.2.10 \
    "cat >> ~/.ssh/authorized_keys"   # append, never overwrite
```

### Phase 3 — Cold-archive transfer (resumable, unattended)

```bash
DEST=truenas_admin@10.42.2.10:/mnt/main/archive/georges-macbook-air/
RSYNC=/opt/homebrew/bin/rsync

# Dry run first — see what would move + total bytes (this is the real size estimate):
caffeinate -dimsu $RSYNC -aHAX --numeric-ids --dry-run --stats \
  --exclude-from=$HOME/mac-archive-exclude.txt \
  -e "ssh -i ~/.ssh/hestia_archive" "$HOME/" "$DEST" | tee ~/mac-archive-dryrun.log

# Real run — detached + logged so a dropped SSH doesn't kill it. nohup keeps it
# alive if the terminal closes; caffeinate keeps the Mac awake; rsync --partial
# resumes any file interrupted by a blip. Re-run the same line to resume.
nohup caffeinate -dimsu $RSYNC -aHAX --numeric-ids --partial --append-verify \
  --info=progress2 --exclude-from=$HOME/mac-archive-exclude.txt \
  -e "ssh -i ~/.ssh/hestia_archive" "$HOME/" "$DEST" \
  > ~/mac-archive.log 2>&1 &
# watch: tail -f ~/mac-archive.log
```

Keep the lid open on AC for the duration. Re-running the command is safe and
resumes; nothing on the Mac is modified (rsync push is read-only on the source).

### Phase 4 — Photo extraction → EXIF sort → sha256 dedup → import → scan

```bash
# 1) Export originals (rehydrates iCloud placeholders, writes EXIF dates):
caffeinate -dimsu osxphotos export ~/mac-photo-export \
  --download-missing --use-photokit --exiftool --update

# 2) EXIF-sort + name-collision check + import net-new, reusing the repo tool.
#    Point it at the export dir as an "SD" source; dry-run first:
scripts/import-sd-photos.sh --person george --sd ~/mac-photo-export           # dry-run
#    then, after the sha256 gate below, --commit.

# 3) sha256 content dedup vs the existing library (adds to the name-collision check):
ssh truenas_admin@10.42.2.10 \
  "find /mnt/main/family/images/photos/george -type f -print0 | xargs -0 sha256sum" \
  | awk '{print $1}' | sort -u > ~/existing.sha256
#    hash the sorted export, import only files whose sha256 is NOT in existing.sha256
#    (extend import-sd-photos.sh's collision step, or filter before --commit).

# 4) Immich scan of the 'george' external library (UI: Administration > External
#    Libraries > george > Scan; or the Immich API) so net-new files get indexed.
```

The full `.photoslibrary` (originals + DB, minus derivatives) is *also* in the
Phase-3 cold archive, so albums/edits survive independently of the flat import.

### Phase 5 — Verify + snapshot + manifest (nothing deleted)

```bash
# Reconcile: a second dry-run should report ~0 to transfer (everything landed).
$RSYNC -aHAX --numeric-ids --dry-run --stats \
  --exclude-from=$HOME/mac-archive-exclude.txt \
  -e "ssh -i ~/.ssh/hestia_archive" "$HOME/" \
  truenas_admin@10.42.2.10:/mnt/main/archive/georges-macbook-air/ | tail -20

# Size/count spot-check (source vs dest), same shape as Windows:
du -sh ~ 2>/dev/null                      # source (minus excludes, approx)
ssh truenas_admin@10.42.2.10 \
  "du -sh /mnt/main/archive/georges-macbook-air; \
   find /mnt/main/archive/georges-macbook-air -type f | wc -l"

# Manifest (so future-you can diff): file list + sizes into the dataset.
ssh truenas_admin@10.42.2.10 \
  "cd /mnt/main/archive/georges-macbook-air && \
   find . -type f -printf '%p\t%s\n' | sort > MANIFEST-$(date +%F).tsv"

# Snapshot ONLY this dataset (independent of the concurrent archive job):
ssh truenas_admin@10.42.2.10 \
  "sudo zfs snapshot main/archive/georges-macbook-air@archived-$(date +%F)"
```

**Nothing on the Mac is deleted.** The Mac remains the live copy; hestia holds the
cold archive + the Immich-indexed living photos. Deletion, if ever, is a separate
explicit decision after George confirms the archive is good.

---

## Rough estimates

- **Cold-archive payload**: identified user data is tens of GiB (Pictures 15 +
  Downloads 9 + Docs/Desktop <1 + selective `~/Library` a few) → **likely
  20–40 GiB after excludes**, *not* the 459 GiB the volume reports. The dry-run in
  Phase 3 gives the real number — trust that over this estimate.
- **Time**: over wired GbE (~110 MB/s real), 40 GiB ≈ **6–8 min** of pure transfer;
  small-file overhead (many tiny Library files) dominates, so budget **20–45 min**.
  Over Wi-Fi, 1–3× that and drop-prone. Photo export + hashing adds **10–30 min**.
- **hestia headroom**: ~26 TB free vs tens of GiB — a non-issue.

---

## Open questions / inputs needed from George

1. **Q1 — Which Mac?** This plan measured *this* host (`Georges-MacBook-Air`,
   10.42.4.163), which holds little (~tens of GiB of real payload). Is the target
   this machine, or a **separate personal laptop** with the honeymoon/first-year
   era data? The plan is identical either way; only the hostname/slug, sizes, and
   iCloud state change. (Ties into the ongoing iPhone-backup / Dropbox photo
   recovery thread.)
2. **Q2 — Immich `george` external library**: confirm the on-disk root is still
   `/mnt/main/family/images/photos/george/YYYY/MM` and that a manual/scheduled
   external-library scan is the right trigger (matches the SD-import + Windows flow).
3. **Q3 — iCloud Photos "Optimize Mac Storage"**: on or off on the target Mac? If
   on, we must rehydrate first (Decision 3) and the 15 GiB on-disk understates the
   true library size.
4. **Q4 — Sensitive dirs**: OK to archive `~/.ssh` private keys and
   `~/Library/Keychains` to hestia? (They're yours on your NAS, but call it out —
   easy to exclude if you'd rather.)
5. **Q5 — `.photoslibrary` fidelity + Xcode Archives**: exclude the regenerable
   `resources/`+`derivatives/` (save a few GiB) or archive the package 100%? And
   keep or drop `~/Library/Developer/Xcode/Archives/` (shipped-app archives)?
6. **Q6 — Time Machine follow-on**: want a hestia SMB Time Machine target set up
   separately for ongoing protection, or is this one-shot archive enough for now?

---

## Not doing (scope guard)

- **No deletion** on the Mac, ever, in this plan.
- **No touching** other children of `/mnt/main/archive/` — a concurrent archive
  job is writing there; this job is confined to its own dataset.
- **No merge of Photos albums/edits** into Immich — only flat originals import;
  album structure is preserved via the cold-archive copy of the package.
