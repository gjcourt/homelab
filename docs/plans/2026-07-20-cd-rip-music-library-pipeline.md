---
status: planned
last_modified: 2026-07-20
summary: "Repeatable CD-rip pipeline: XLD rip -> verify -> tag -> rsync to hestia family share -> organize -> Navidrome scan"
---

# CD-Rip → Music Library Pipeline (repeatable runbook)

A repeatable, step-by-step pipeline for turning a CD (or a pile of already-ripped
files) into correctly-organized albums in the homelab music library that
**Navidrome** serves. It runs every rip session; the first invocation drains the
existing `~/Music` backlog on this Mac (~9.1 GiB, 532 FLACs).

This is the audio counterpart to the video pipeline (MakeMKV → ffmpeg → SMB
`/Volumes/family`) and the photo pipeline (`scripts/import-sd-photos.sh`). It
writes to the **same `family` share**, into `media/music`.

## Where music lives (the facts this plan is grounded in)

| Thing | Value |
| :--- | :--- |
| Music server | **Navidrome** (`navidrome-prod`, `music.burntbytes.com`) — Jellyfin serves video only |
| Library path Navidrome scans | NFS `10.42.2.10:/mnt/main/family/media/music` (ReadOnlyMany, `apps/production/navidrome/nfs-music.yaml`) |
| Same tree, writable from this Mac | SMB `//george@hestia/family` → **`/Volumes/family/media/music`** |
| Canonical layout | `Artist/Album/[Disc NN/]## Title.ext` (tag-driven) |
| Transfer | `rsync` over the mounted SMB share (correct ownership as the login user, no SSH/sudo) |

Navidrome mounts the library **read-only**, so it can never mutate it — all
writes come from this pipeline. Writing as the SMB login user avoids the
`chown`/sudo dance the SSH photo path needs.

> **Note / open question — SMB enumeration.** During design, `/Volumes/family/media/music`
> listed as empty over SMB even though the merged tree is documented as populated
> (curated FLAC + archive dump, per the Phase-4a assimilation work). This is
> likely an SMB ACL/enumeration quirk on the `drwx------ family` mount, not an
> empty library. **Confirm the mount shows existing albums before the first
> `--commit` run** (`ls /Volumes/family/media/music`); if it is genuinely empty,
> re-check the share/subpath with the operator before writing.

## Pipeline overview

```
   CD                     this Mac (~/Music)                     hestia family share
  ┌────┐   1.rip    ┌──────────────────────┐  4.rsync   ┌───────────────────────────┐
  │ CD │──────────▶ │  XLD  →  .flac + .log │──────────▶ │ /Volumes/family/media/music│
  └────┘  (XLD)     │  2.verify (AccurateRip)│  +5.organize│  = /mnt/main/family/media/ │
                    │  3.tag   (Picard)     │            │    music (NFS, RO)         │
                    └──────────────────────┘            └────────────┬──────────────┘
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

## Step 4+5 — Transfer + organize (`scripts/organize-music-rips.sh`)

One script does transfer **and** layout. **Dry-run first, always.**

```bash
# 1) Dry-run the whole backlog — prints the planned Artist/Album/Track layout
#    and lists any files it cannot place (missing tags). Writes NOTHING.
scripts/organize-music-rips.sh --src ~/Music --dest /Volumes/family/media/music

# 2) Fix anything in the SKIPPED list (Step 3), re-dry-run until the skip list is
#    only things you intend to leave behind.

# 3) Commit — rsync each file to its computed path, then verify the destination.
scripts/organize-music-rips.sh --src ~/Music --dest /Volumes/family/media/music --commit
```

What the script guarantees:

- **Source is read-only** — `rsync` copies; nothing in `~/Music` is moved or
  deleted. (Prune the Mac copy manually once Navidrome shows the albums.)
- **Dry-run by default** — no `--commit`, no writes.
- **Idempotent + resumable** — `rsync --checksum --ignore-times` skips
  byte-identical files (safe re-runs), `--partial` resumes interrupted copies.
- **Layout:** `<AlbumArtist>/<Album>/[Disc NN/]## Title.ext`; various-artists →
  `Various Artists/`; genuine multi-disc sets (verified in the dry-run against
  *The Essential Artie Shaw* → `Disc 01/01-01 …`) nest under `Disc NN/`;
  single-disc albums stay flat.
- **Destination verify (the silent-skip guard):** after copying it re-counts
  files + `du` bytes at the destination and asserts **every** planned file is
  physically present at its path — a low-bytes/high-speedup rsync summary cannot
  mask a skipped file. Exits non-zero if any planned file is missing.

Path mapping reminder: `/Volumes/family/media/music/<X>` on this Mac **is**
`/mnt/main/family/media/music/<X>` that Navidrome reads over NFS.

## Step 6 — Scan (Navidrome) + verify albums appear

Navidrome auto-scans on a schedule, but trigger it to see new albums immediately:

```bash
# Force a full rescan via the Subsonic API (token/creds from the Navidrome UI):
curl "https://music.burntbytes.com/rest/startScan.view?u=<user>&p=<pass>&v=1.16.1&c=riprunbook&f=json"

# ...or just click Rescan in the Navidrome web UI (fastest, no creds handling).
```

Then **verify**: open `music.burntbytes.com`, confirm the new artist/album shows
with cover art and the right track count/order. Spot-check a multi-disc album
(discs in order) and any various-artists album (single album, per-track artists).

Only after Navidrome shows the albums, prune the Mac-side copy from `~/Music`.

---

## First-run checklist (drain the `~/Music` backlog)

- [ ] `flac -t ~/Music/*.flac` — integrity pass on the backlog.
- [ ] Picard-tag the **31 untagged `Track NN`** files (or set them aside).
- [ ] Confirm `/Volumes/family` is mounted and shows existing albums under
      `media/music` (see the SMB-enumeration note above).
- [ ] `scripts/organize-music-rips.sh --src ~/Music --dest /Volumes/family/media/music`
      (dry-run) — review layout + skip list.
- [ ] Re-run dry-run until the skip list is acceptable.
- [ ] `… --commit` — transfer + organize; confirm the "OK: all N present" verify line.
- [ ] Trigger the Navidrome scan; confirm albums + art in the UI.
- [ ] Prune the transferred copies from `~/Music`.

## Tooling summary

| Step | Tool | Automated? |
| :--- | :--- | :--- |
| 1 Rip | XLD (Secure Ripper, FLAC, log+cue) | manual |
| 2 Verify | XLD AccurateRip + `flac -t` | manual |
| 3 Tag + art | MusicBrainz Picard | manual |
| 4 Transfer | `scripts/organize-music-rips.sh` (rsync over SMB) | **script** |
| 5 Organize | `scripts/organize-music-rips.sh` (tag-driven layout) | **script** |
| 6 Scan | Navidrome rescan (Subsonic API or UI) | manual |

## Open questions

- **SMB shows empty** `media/music` (see note) — permissions/enumeration quirk vs
  genuinely empty; confirm before first `--commit`.
- **Navidrome scan trigger creds** — decide whether to script `startScan.view`
  (needs a token in the runbook) or keep it a manual UI click (preferred; no
  secret handling, and SOPS/secrets stay operator-only).
- **Backlog `~/Music` also contains the Apple Music.app library** (`Music/…`); the
  script's `--src ~/Music` only walks audio files and will also see anything Apple
  has downloaded there. Point `--src` at a rips-only subfolder if that library
  grows, or keep rips out of `~/Music/Music/`.
