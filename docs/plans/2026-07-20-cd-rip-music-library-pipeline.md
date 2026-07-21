---
status: planned
last_modified: 2026-07-20
summary: "Repeatable CD-rip pipeline: XLD rip -> verify -> tag -> organize -> rsync-over-SSH to hestia dataset -> Navidrome scan"
---

# CD-Rip → Music Library Pipeline (repeatable runbook)

A repeatable, step-by-step pipeline for turning a CD (or a pile of already-ripped
files) into correctly-organized albums in the homelab music library that
**Navidrome** serves. It runs every rip session; the first invocation drains the
existing `~/Music` backlog on this Mac (~9.1 GiB, 532 FLACs).

This is the audio counterpart to the photo pipeline (`scripts/import-sd-photos.sh`),
and it reuses that pipeline's transport **verbatim**: rsync over SSH to a
scratch dir on the dataset host, then one `sudo rsync --chown` into the
owner-only-writable library.

## Where music lives (the facts this plan is grounded in)

| Thing | Value |
| :--- | :--- |
| Music server | **Navidrome** (`navidrome-prod`, `music.burntbytes.com`) — Jellyfin serves video only |
| Library path Navidrome scans | NFS `10.42.2.10:/mnt/main/family/media/music` (ReadOnlyMany, mounted RO at `/music`, `ND_MUSICFOLDER=/music`; `apps/production/navidrome/nfs-music.yaml`) |
| Dataset host (writable, over SSH) | `truenas_admin@10.42.2.10` → path `/mnt/main/family/media/music` |
| Library ownership | **uid 1028 (george) : gid 100 (users), mode 755** — owner-only write |
| Scratch (truenas_admin-writable) | `/mnt/main/downloads/music-import` |
| Canonical layout | `Artist/Album/[Disc NN/]## Title.ext` (tag-driven) |
| Transfer | **rsync over SSH** — stage to scratch, then `sudo rsync --chown=1028:users` into the library (mirrors `import-sd-photos.sh`) |

Navidrome mounts the library **read-only**, so it can never mutate it — all
writes come from this pipeline.

### Why rsync-over-SSH, not the SMB mount

The first design used `rsync` to the mounted SMB share `/Volumes/family`. That
was changed to **rsync over SSH** because:

- **SMB is flaky**, and the SSH path is the already-proven transport the sibling
  photo pipeline uses.
- **macOS TCC blocks `/Volumes/family` for non-interactive / agent processes** —
  `ls /Volumes/family` returns `Operation not permitted` from a plain shell even
  though `stat` shows the mount is a populated `drwx------ george:staff` dir. A
  mount-based pipeline therefore can't run from a plain/automated shell.
- **rsync-over-SSH removes SMB entirely.** Source is local `~/Music` (readable
  everywhere); destination is the dataset over SSH. The script now runs cleanly
  from any shell.

The one cost is that the library is owned `george:users` mode 755 (owner-only
write) and the SSH login is `truenas_admin`, so — exactly like the photo
pipeline — we can't write into it directly. The script stages to a
`truenas_admin`-writable scratch dir, then runs a single `sudo rsync --chown`
(passwordless sudo is available on the box; `ssh -t` lets it prompt otherwise)
to place the files with the correct owner uid, which it derives dynamically via
`stat -c %u`.

> **RESOLVED — the "SMB looked empty" question.** During the initial design
> `ls /Volumes/family/media/music` appeared empty. That was the **macOS TCC
> block above, not an empty or ACL'd share.** Over SSH the dataset is confirmed
> **populated (76 artist directories)** and `stat` shows the SMB mount itself is
> a populated 16 KB `drwx------ george:staff` dir. The library is there; nothing
> to re-check before writing.

## Pipeline overview

```
   CD                 this Mac (~/Music)                  dataset host (SSH)
  ┌────┐  1.rip   ┌───────────────────────┐  4.rsync   ┌────────────────────────────┐
  │ CD │────────▶ │ XLD → .flac + .log    │  over SSH  │ scratch: /mnt/main/downloads│
  └────┘  (XLD)   │ 2.verify (AccurateRip)│──────────▶ │   /music-import             │
                  │ 3.tag    (Picard)     │  +5.organize│        │ sudo rsync --chown │
                  │ 5.organize (local     │            │        ▼                    │
                  │   Artist/Album tree)  │            │ /mnt/main/family/media/music│
                  └───────────────────────┘            │   (NFS RO → Navidrome)      │
                                                        └────────────┬───────────────┘
                                                                     │ 6.scan
                                                                     ▼
                                                              Navidrome (navidrome-prod)
```

Steps 1–3 and 6 are **manual** (checklist below). Steps 4–5 are the script
`scripts/organize-music-rips.sh` (idempotent, `--dry-run` by default, verifies
the destination).

---

## Step 1 — Rip (XLD)

Use **XLD** (X Lossless Decoder) on this Mac. One-time settings:

- **Output format:** FLAC, compression level 8 (level does not affect fidelity).
- **Ripper mode:** *CDParanoia III / XLD Secure Ripper* — read the disc in secure
  mode with re-reads.
- **AccurateRip:** enabled (Preferences → General → "Query AccurateRip").
- **Log + cue:** enable "Save log file" and "Save cuesheet"; keep the per-album
  `.log` next to the FLACs (the backlog already has 34 such logs). These are the
  rip-quality receipt.
- **Filename format:** the backlog uses `## Artist - Title` flat in `~/Music`;
  either that or XLD's per-album folder is fine — the organizer keys off **tags**,
  not filenames.

**Known bad-sector / slip behaviour:** on a scratched disc XLD will report
inaccurately-ripped tracks or drift/slip errors in the log. Do **not** propagate
those — clean the disc and re-rip, or accept only if AccurateRip later confirms
the track (Step 2). A rip that XLD flags and AccurateRip cannot vouch for is a
reject.

## Step 2 — Verify (AccurateRip / checksums)

Reject bad rips **before** they reach the library:

- In the XLD log, confirm every track shows **"Accurately ripped (confidence N)"**
  or a clean CRC match. Tracks marked "No match" / "Rip may not be accurate" get
  re-ripped or dropped.
- For discs not in the AccurateRip database (rare/old jazz pressings — several in
  this backlog), require XLD Secure Ripper's own **CRC + a clean second pass**
  (no read errors in the log) instead.
- Optional integrity gate before transfer:
  `flac --test *.flac` (or `find . -name '*.flac' -exec flac -t {} +`) — every
  file must decode cleanly.

## Step 3 — Tag + cover art (MusicBrainz Picard)

The organizer is **100% tag-driven**, so tags must be right *before* transfer.

- Open the rip in **MusicBrainz Picard**, "Scan" / "Lookup", match to the correct
  MusicBrainz release, **Save**. This normalizes `ALBUM`, `ARTIST`,
  `ALBUMARTIST`, `TITLE`, `TRACKNUMBER`, `DISCNUMBER`/`TOTALDISCS`, and sets
  `COMPILATION=1` for various-artists releases.
- **Cover art:** enable the *Cover Art* option to embed front cover into each
  file (and optionally save `cover.jpg` in the album folder). Navidrome shows
  embedded art or a `cover.*` file.
- **Album artist matters most:** the organizer folders by `ALBUMARTIST`. For a
  various-artists album, Picard sets `ALBUMARTIST=Various Artists` +
  `COMPILATION=1`, which the script buckets under `Various Artists/`. `feat.`
  guests belong in the track `ARTIST`/`TITLE`, **not** the album artist — Picard
  does this correctly by default.
- **Backlog reality:** a scan of the 532 backlog FLACs found **31 completely
  untagged** files (the `## Track NN.flac` group — likely discs that failed
  MusicBrainz lookup). The organizer will **report and skip** these; run them
  through Picard first, then re-run (the script is idempotent).

## Step 4+5 — Organize + transfer (`scripts/organize-music-rips.sh`)

One script does layout **and** transfer over SSH. **Dry-run first, always.**

```bash
# 1) Dry-run the whole backlog — prints the planned Artist/Album/Track layout
#    and lists any files it cannot place (missing tags). Writes NOTHING.
scripts/organize-music-rips.sh --src ~/Music

# 2) Fix anything in the SKIPPED list (Step 3), re-dry-run until the skip list is
#    only things you intend to leave behind.

# 3) Commit — build the organized tree locally, rsync it to the host scratch
#    dir over SSH, sudo rsync --chown into the library, then verify over SSH.
scripts/organize-music-rips.sh --src ~/Music --commit
```

Defaults: `--host truenas_admin@10.42.2.10`, `--dest /mnt/main/family/media/music`
(override with `--host` / `--dest`, or `MUSIC_HOST` / `MUSIC_DEST` /
`MUSIC_SCRATCH` env vars).

What the script guarantees:

- **Source is read-only** — the organized tree is built by **hardlinking** the
  source files (near-instant, no extra disk since it's the same filesystem as
  `~/Music`), so nothing in `~/Music` is moved or deleted. Prune the Mac copy
  manually once Navidrome shows the albums.
- **Dry-run by default** — no `--commit`, no writes, no SSH mutation.
- **rsync over SSH, in two hops (mirrors `import-sd-photos.sh`):**
  1. `rsync -a --checksum --partial --append-verify` the local organized tree to
     the `truenas_admin`-writable scratch dir `/mnt/main/downloads/music-import`.
  2. `ssh -t … sudo rsync -a --checksum --partial --chown=<uid>:users` from
     scratch into the owner-only-writable library. The owner uid is derived
     dynamically (`stat -c %u`, currently **1028**), so files land as the library
     owner (`george:users`), not `truenas_admin`.
- **Idempotent + resumable** — `--checksum` skips byte-identical files (safe
  re-runs), `--partial --append-verify` resumes interrupted transfers.
- **Layout:** `<AlbumArtist>/<Album>/[Disc NN/]## Title.ext`; various-artists →
  `Various Artists/`; genuine multi-disc sets (verified in the dry-run against
  *The Essential Artie Shaw* → `Disc 01/01-01 …`) nest under `Disc NN/`;
  single-disc albums stay flat.
- **Destination verify over SSH (the silent-skip guard):** after placement it
  runs `ssh host 'du -sh <dest>' + file count`, then lists the destination tree
  over SSH and asserts **every** planned relative path is physically present — a
  low-bytes/high-speedup rsync summary cannot mask a skipped file. Exits
  non-zero (and prints the missing paths) if any planned file is absent.
- **Scratch is left in place** after a successful run and the cleanup command is
  printed (`ssh <host> 'rm -rf <scratch>'`) so you can inspect before removing.

## Step 6 — Scan (Navidrome) + verify albums appear

Navidrome auto-scans on a schedule, but trigger it to see new albums immediately:

```bash
# Force a full rescan via the Subsonic API (token/creds from the Navidrome UI):
curl "https://music.burntbytes.com/rest/startScan.view?u=<user>&p=<pass>&v=1.16.1&c=riprunbook&f=json"

# ...or just click Rescan in the Navidrome web UI (fastest, no creds handling).
```

**Recommended: use the manual UI Rescan** (no creds handling; keeps the
Subsonic token out of the runbook and out of any script — secrets stay
operator-only). The scripted `startScan.view` above is documented only as an
option; prefer the UI click.

Then **verify**: open `music.burntbytes.com`, confirm the new artist/album shows
with cover art and the right track count/order. Spot-check a multi-disc album
(discs in order) and any various-artists album (single album, per-track artists).

Only after Navidrome shows the albums, prune the Mac-side copy from `~/Music`.

---

## First-run checklist (drain the `~/Music` backlog)

- [ ] `flac -t ~/Music/*.flac` — integrity pass on the backlog.
- [ ] Picard-tag the **31 untagged `Track NN`** files (or set them aside).
- [ ] Confirm SSH to the dataset host: `ssh truenas_admin@10.42.2.10 'ls /mnt/main/family/media/music | head'`
      (no SMB mount needed).
- [ ] `scripts/organize-music-rips.sh --src ~/Music` (dry-run) — review layout + skip list.
- [ ] Re-run dry-run until the skip list is acceptable.
- [ ] `… --commit` — organize + transfer over SSH; confirm the "OK: all N present" verify line.
- [ ] Trigger the Navidrome scan (UI Rescan); confirm albums + art in the UI.
- [ ] `ssh truenas_admin@10.42.2.10 'rm -rf /mnt/main/downloads/music-import'` — remove scratch.
- [ ] Prune the transferred copies from `~/Music`.

## Tooling summary

| Step | Tool | Automated? |
| :--- | :--- | :--- |
| 1 Rip | XLD (Secure Ripper, FLAC, log+cue) | manual |
| 2 Verify | XLD AccurateRip + `flac -t` | manual |
| 3 Tag + art | MusicBrainz Picard | manual |
| 4 Transfer | `scripts/organize-music-rips.sh` (rsync over SSH → scratch → `sudo rsync --chown`) | **script** |
| 5 Organize | `scripts/organize-music-rips.sh` (tag-driven layout) | **script** |
| 6 Scan | Navidrome rescan (UI Rescan; scripted Subsonic optional) | manual |

## Open questions

- **Navidrome scan trigger** (unresolved by design choice) — script
  `startScan.view` (needs a Subsonic token in the runbook) vs. a manual UI
  Rescan click. **Recommendation: manual UI** — keeps secrets operator-only and
  out of any committed script. Left open for the operator to decide per taste.
- **Backlog `~/Music` also contains the Apple Music.app library** (`Music/…`); the
  script's `--src ~/Music` only walks audio files and will also see anything Apple
  has downloaded there. Point `--src` at a rips-only subfolder if that library
  grows, or keep rips out of `~/Music/Music/`.
