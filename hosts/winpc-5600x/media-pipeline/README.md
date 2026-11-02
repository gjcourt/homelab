# media-pipeline (winpc-5600x)

Transcodes MakeMKV disc rips to x265 and pushes them into the hestia media
library that Jellyfin serves.

## Importing CD rips (`import-music.ps1`)

**Run it from the checkout, detached.** A foreground SSH run dies with the session:
on 2026-09-19 that killed a run right after `scp`, leaving 147 files staged and nothing
in the library.

```powershell
cd C:\Users\George\src\homelab
git pull --ff-only
# dry run: reports what would import, transfers nothing
powershell -ExecutionPolicy Bypass -File hosts\winpc-5600x\media-pipeline\import-music.ps1 -DryRun
```

```powershell
# real run - DETACHED, survives the SSH session
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = 'cmd.exe /c "cd /d C:\Users\George\src\homelab && powershell -NoProfile -ExecutionPolicy Bypass -File hosts\winpc-5600x\media-pipeline\import-music.ps1 > C:\media-work\import.log 2>&1"'
}
```

What the script guarantees, and why each guard exists:

| Guard | Exists because |
| :--- | :--- |
| **UTF-8 console encoding, asserted** | PowerShell decodes native output in the OEM codepage. ffprobe emits UTF-8, so every non-ASCII tag was mangled at step 1 — mangled folder names *and* broken dedup. The assert refuses to run rather than fail open. |
| **ASCII placeholders in transit** | `scp` on Win32-OpenSSH carries filenames in the console codepage. Names stage as `~uXXXX~` and are restored on hestia from a UTF-8 **manifest file**; if a placeholder survives, the run aborts before the library is touched. |
| **Per-disc dedup** | A multi-disc album lands as `Album [Disc 1]`/`[Disc 2]`; comparing the bare title never matched and re-sent gigabytes every run. A half-imported album now imports only its missing discs. |
| **Completeness gate** | London Calling disc 1 was deleted on the strength of a check that proved tags existed, not that the album was whole. |
| **Filename budget in the staged form** | The 255-byte limit applies to the name that actually travels, which placeholders make longer. |

**Do not clear `C:\Rips` until a run reports 0 incomplete.**

## Usage

```powershell
# check what would run, encode nothing
.\transcode.ps1 -Queue .\queue.tsv -DryRun

# real run; -WaitForIdle blocks until any other ffmpeg finishes
.\transcode.ps1 -Queue .\queue.tsv -WaitForIdle
```

Long jobs must be **detached via WMI** — `Start-Process` is killed when the SSH
session closes:

```powershell
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = 'cmd.exe /c "C:\media-pipeline\run-transcode.bat"'
}
```

## The queue is data

`queue.tsv` is `<source path><TAB><Library Name>[<TAB><Library Dir>]`. Adding a
title never means editing the script.

The optional third column is the destination directory relative to
`media/video`. Omitted, it is `movies/<Library Name>` — the only layout the
script supported before 2026-10-05, so existing movie queues are unchanged. TV
episodes need it, because Jellyfin wants `Show (Year)/Season NN/` with the
`SxxEyy` in the filename:

```text
C:\Video\WORMD1543255\B1_t00.mkv	Through the Wormhole (2010) - S01E01	tv/Through the Wormhole (2010)/Season 01
```

The directory must sit under `movies/`, `tv/`, `tv-anime/` or `music-videos/`. Names and
directories containing `'`, `..`, `\`, `:` or the other Windows-reserved
characters are **skipped with a log line**: both are interpolated into
single-quoted remote shell commands and Windows paths, so a stray apostrophe
used to mean a failed push at best. Write `Mission - Impossible`, not
`Mission: Impossible`.

Two rules for picking titles:

- **Pick the source by duration, not filename.** The main feature is not always
  `_t00`.
- **Get the name from Radarr, and match any existing hestia directory exactly.**
  Radarr writes `The Tale of The Princess Kaguya`; the existing directory is
  `The Tale of the Princess Kaguya`. Using Radarr's capitalisation would create
  a second folder.

## Why this replaced five scripts

`transcode-batch.ps1`, `-batch2`, `-batch3`, `-extras` and `chain-extras.ps1`
each re-implemented the same push logic and had drifted apart. Two defects were
found on 2026-08-29 and are fixed here.

### 1. Success was ffmpeg's exit code alone

On 2026-08-04, ffmpeg exited 0 after **3m09s** on a 137-minute film. A
72 MB / 108 kbps file was pushed to hestia, marked `DONE`, and the local copy
deleted. It sat in the library for 25 days.

The gate now requires, before pushing:

| Check | Why it is not redundant |
| :--- | :--- |
| duration within `-DurTolPct` of source | catches truncation |
| bitrate above `-MinKbps` | catches the 108 kbps case |
| **a real decoded frame above `-MinFrameKB`** | catches a stream that reports the right duration but has no picture |

The third check is the load-bearing one. The broken file passed *both* of the
first two — it reported a correct 137-minute duration. `ffprobe -frames:v 1`
also exits 0 having decoded nothing, so that is not a substitute: you must
extract a frame and check its size.

On any failure the local copy is **kept**, never pushed and never deleted.

### 2. Subtitle backpressure deadlock — ROOT CAUSE

**Mapping subtitles into the encode pass can make ffmpeg silently encode almost
nothing.** Found 2026-08-29 after four wrong theories.

`-map "0:s?"` with `-c:s copy` maps every subtitle track into the *encoding*
process. If any mapped output stream produces packets very rarely, its output
queue never fills, so **the demuxer never blocks**. ffmpeg reads the entire
source into RAM, hits EOF, and shuts down *cleanly* with the video queue still
full.

*The Tale of the Princess Kaguya* has four PGS tracks, one of which is a
**forced-narrative French track carrying 8 subtitle cues across 137 minutes**:

```
Input stream #0:9 (subtitle):     8 packets read
Input stream #0:0 (video):   132753 packets read;  1671 frames decoded
Output stream #0:0 (video):                        1671 frames encoded
encoded 1671 frames in 152.90s (10.93 fps), 5028.15 kb/s, Avg QP:19.56
ffmpeg RSS at time of failure: 21.5 GB   (healthy run: 0.9 GB)
```

**1,671 of 197,304 frames — 0.85%.** And x265 encoded those 1,671 *perfectly*, at
5028 kb/s and QP 19.56. **The encoder was never the problem.** The file reads as
108 kbps only because 1,671 frames are smeared across a 137-minute timeline.

It passes every naive check: **exit code 0**, correct container duration, video
packets present across the whole timeline.

**Fix:** encode video + audio only, then mux subtitles back in a separate
`-c copy` pass, where a sparse stream cannot starve anything.

**Things that were investigated and are irrelevant** — all coincidences of the
one title that happened to have a sparse track: colour metadata
(`color_space=unknown`), frame rate (`24/1` vs `24000/1001`), rate control
(CRF vs ABR), `-tag:v hvc1`, source integrity, and disk space. BT.709 tags
"fixed" 60-second clips twice — clips are short enough that the queue never
blows up, so *any* clip test passes and none of them predict full-file
behaviour.

**Why it hid for 25 days:** `-loglevel error` suppressed ffmpeg's own
`frame= … time= … speed=` line. One look at `speed=53.8x` on a 137-minute film
would have ended it immediately.

### The frame-count gate

Exit code proves nothing here, so the gate compares frames actually present
in the encode against the **source's** video packet count (`ffprobe
-count_packets`) and fails below 99%. That is the check that would have caught
this on day one. Counting the source re-reads it once, which costs a minute or
two against an encode measured in hours.

⚠️ **It was `duration × r_frame_rate` until 2026-10-05**, which fails every
soft-telecined NTSC DVD. The MPEG-2 stream holds 23.976 progressive frames with
pulldown flags, while the container — and the encode, which inherits the tag —
reports 29.97. March of the Penguins came out with 115,408 of 115,408 frames
over the full 4,813 s, and the gate called it 80% and refused to push it.
`duration × rate` remains only as a fallback if the source count can't be read.

## Encoder settings

`libx265 -b:v 4700k`, ~4.86 Mbps ABR for video. Encodes at roughly 1.1x realtime
on the 5600X (6c/12t), so a 2-hour film is about 2 hours of wall time.

**Audio: every track, at its own channel count.** `-map 0:a`, then `-c:a:N aac`
with the bitrate scaled per stream — 96k mono, 160k stereo, 64k/channel above
that (5.1 → 384k, 7.1 → 512k). No `-ac`, so the source layout survives.

⚠️ **This was `-map 0:a:0 … -ac 2` until 2026-09-20** — the *first* audio stream
only, downmixed to stereo. The Tale of the Princess Kaguya carries
`eng/eng/jpn/jpn/fra` in DTS 5.1; the library copy came out as one 2-channel
English AAC track. For a Ghibli film that silently discards the original
Japanese audio, and it did the same to **every title this script has ever
encoded**. Nothing complained: the file played, the duration matched, the
bitrate passed.

So there is now an **audio-track gate**, for the same reason the frame-count
gate exists — ffmpeg exits 0 while dropping streams, and the exit code proves
nothing. The output must carry the same number of audio tracks as the source
*and* the same channel counts, or the encode is rejected and the local copy
kept. A drop and a downmix are both failures.

Re-encoding a title already in the library needs `-Replace`; without it
preflight skips anything already `OnHestia`.

A push lands at `<name>.mkv.incoming` inside the destination, is hashed **there**,
and only swapped onto the final name once it matches — a rename within one
dataset, so atomic. This used to `mv` straight onto the final path and hash
afterwards: harmless for a new title, destructive for a re-encode, because a
corrupt transfer had already replaced a good library file by the time the hash
disagreed. A failed verify now deletes the `.incoming` and leaves the library
untouched.

---

# import-music.ps1 — CD rips → the music library

Separate pipeline, same box. EAC + Picard write well-tagged `.flac` **flat** into
`C:\Rips` (`NN Title.flac`, several albums interleaved), so grouping comes from
**tags**, never from filenames or directory layout.

```powershell
.\import-music.ps1 -DryRun     # report what would import, touch nothing
.\import-music.ps1
```

read tags → group → dedup against the live library → organise → `scp` to a hestia
scratch dir → `rsync` into the library with `--chown=george:users --chmod=D755,F644`.

**No Kubernetes dependency.** The import is finished when rsync lands the files on
hestia — that is the source of truth. Navidrome is a downstream *consumer*: it
mounts the library read-only over NFS and rescans on `ND_SCANSCHEDULE=1h`, so it
updates itself. `kubectl -n navidrome-prod rollout restart deployment/navidrome`
only makes it immediate, and this box has no kubeconfig anyway.

**It never deletes from `C:\Rips`.** Clearing source rips is operator-only and
deliberately manual.

## Guards, each earned on 2026-08-29

| Guard | What went wrong without it |
| :--- | :--- |
| **255-*byte* filename cap** | Linux caps filenames at 255 **bytes**, not characters. A Marvin Gaye medley track came to 259 bytes (curly apostrophes cost 3 each in UTF-8); `scp` failed `Bad message` on that one file and left the album incomplete. Navidrome is tag-driven, so truncating loses nothing. |
| **Windows trailing dot** | Windows forbids a directory ending in `.`, so `Harry Connick, Jr.` staged as `Harry Connick, Jr`. Restored on hestia via a rename manifest. |
| **Colon → ` - `** | A naive `:`→`-` gives `True Love- A Celebration`; the library's 221 existing albums use space-dash-space (`Jazz Steps Out - Rare Masters`). |
| **Unicode duplicate guard** | An existing artist dir silently becomes a *second* folder if the incoming `é` differs (NFC vs NFD). The rsync dry-run is inspected: `.d..t` on an existing artist means merge, `cd+++` means create — and the script **aborts** rather than making a duplicate. |
| **Dedup on artist AND album** | "The Montreux Years" is a *series*. Title-only matching would have skipped a genuinely new Monty Alexander album because Nina Simone's was present. |
| **Byte-count verification** | Transfer is checked file-for-file before anything touches the library. |

Dedup normalises away punctuation, so a tag reading `True Love: A Celebration of
Cole Porter` correctly matches the on-disk `True Love - A Celebration of Cole
Porter` instead of re-importing it.

## Afterwards, on the Mac

Refresh `~/src/music-library/owned_albums.txt` so the SFPL borrow queue stops
re-borrowing what you now own — **union it, never overwrite**. Folder names are
sanitised while that list carries real punctuation (`Amazing Grace: The Complete
Recordings` vs `Amazing Grace- …`), and regenerating from the filesystem silently
drops entries. That list is not in git; it lives with the SFPL tooling.

---

# verify-before-delete.ps1 — proving a rip is redundant

`import-music.ps1` never deletes from `C:\Rips`, and `transcode.ps1` never
deletes a disc rip. Clearing them was an operator judgement call with no
evidence behind it. This is the evidence.

```powershell
.\verify-before-delete.ps1 -Mode Music              # report only
.\verify-before-delete.ps1 -Mode Music -Delete      # reclaim what is proven
.\verify-before-delete.ps1 -Mode Video -SourceDir D:\Rips
```

One question per local file: **is there a file in the library holding this
file's content?**

## sha256 of a FLAC is not a checksum of its audio

Editing a tag rewrites the file. On 2026-09-20 eleven Daft Punk remixes were
reported unproven although the library held the same eleven tracks under the
same names — the library copies were **exactly 52 bytes larger**, one extra
metadata block, and the audio was bit-identical. On the file hash alone the
verifier refuses to bless anything retagged since import, and that set only
grows.

So FLAC gets a second, weaker-looking but actually stronger proof: the
**STREAMINFO audio checksum**, an MD5 of the *unencoded* audio written by the
encoder at a fixed offset. Reading it costs 42 bytes and decodes nothing, and
tag edits cannot change it. There is no equivalent for mp3 or m4a, so those stay
on sha256 alone.

The two are recorded as **different verdicts** — `VERIFIED` and
`VERIFIED_AUDIO` — so the ledger never claims a byte-identical copy it does not
have. `-RequireExactBytes` turns the fallback off.

## Matching is by content, never by path

The obvious implementation re-derives the library path from the tags
(`DiscAlbum` → `LinuxName` → `TruncName` → `AsciiSafe`) and looks for it. That
puts a *delete* decision downstream of the same name-mangling that produced
three mojibake folders in a single run on 2026-09-19. A hash match needs none
of it: if the bytes are in the library, the local copy is redundant regardless
of what either side calls the file.

## The two modes prove different things

| | Music | Video |
| :--- | :--- | :--- |
| Library holds | a byte-identical **copy** | a **transcode** — a different file by design |
| Therefore | hash identity is the proof | no source hash can ever match |
| Proof used | sha256 of the rip found in a full library index, or the FLAC audio checksum | the ledger's own `LANDED` row, **re-checked**: is that file still there, and does it still hash to the recorded value? |
| Cost | index the whole music library (~76 GB, a few minutes) | hash only the landed files — indexing 1.6 TB to check a few films would be absurd |

Video also handles local files with **no `LANDED` row** — anything that landed
before the ledger existed. Refusing those forever is not an answer, so it falls
back to content, exactly as the music path does: an encoded output that was
pushed is byte-identical to the library copy.

**Size is the prefilter that makes that affordable.** A stat-only pass over the
library is instant (`hash=0`), and only candidates of exactly the right size are
then hashed — usually one file, never 1.6 TB. Nothing is ever judged *on* size:
a match of the right number of wrong bytes is precisely the failure
`transcode.ps1` stopped accepting when it replaced its size check with a hash.

### The `LANDED` row's `Src` is what a delete keys on

For video the ledger row is the only link between a rip and its transcode, so
the `Src` column decides **which local file** a verified library copy clears.
Until 2026-10-14 `transcode.ps1` wrote `Src=$i.Src` — the preflight loop's
variable, still holding the **last queued file** — instead of `$it.Src`. Every
title in a multi-title run was recorded against that one path:

| Effect | Seen |
| :--- | :--- |
| Real sources never matched, so they were never cleared | Godzilla, Mad Max, Marty Supreme, Kaguya — all recorded as `C:\Video\OLDBOY\title_t00.mkv` |
| **Fail-open**: if the last queued title failed while an earlier one landed, the failed title's source would be "verified" against the other film and deleted | not hit — both deletions made under the bug (Oldboy 10/5, Wormhole S01E08 10/6) happened to be the last item *and* the last to land |

The fix uses `$it.Src` and removes `$i` after preflight, so a regression writes
an empty `Src`, which the verifier skips. Rows already written are corrected by
**appending** rows with the true path (`ledger-corrections/`) — the ledger is
append-only and later rows win per `Src`. The verifier still re-hashes the
library file before trusting any of them, so a wrong correction row blocks; it
cannot clear anything by itself.

Applying a correction file is a manual step, done once after its PR merges.
Copy the file to hestia, then append it under the **same lock** `ledger.ps1`
takes, so it cannot interleave with a row a running script is writing:

```bash
# on hestia, with the .tsv copied to /tmp
sudo flock /mnt/main/archive/_inventory/rips/ledger.tsv.lock \
  -c 'cat /tmp/2026-10-15-transcode-src.tsv >> /mnt/main/archive/_inventory/rips/ledger.tsv'
```

Then dry-run `verify-before-delete.ps1 -Mode Video` before any `-Delete`.
Correction files must stay LF-only with 13 tab-separated fields, the ledger
schema; a trailing CR rides into the `note` field, and the verifier silently
skips a row with fewer than 12 fields.

## The library index, and why it is not in the ledger

`index-library.sh` runs on hestia and emits `sha256 <TAB> bytes <TAB> relpath`.

The ledger stays a **movement log** — it records things happening, not an
inventory. So the inventory is a separate snapshot file, and a `LIBRARY_INDEXED`
row carries **its sha256**. Index plus anchor is a claim you can re-check later,
and editing the index breaks the anchor.

That anchor is computed from the copy that was actually **used to judge**, not
from the summary hestia reported. A fetch that truncates therefore aborts the
run instead of silently deleting rips against a short index.

Two more deliberate choices:

- **Files are hashed from stdin** (`sha256sum < "$f"`). Passing the path lets
  `sha256sum` escape backslashes and newlines by rewriting the output line —
  corrupting exactly the rows you would least want wrong.
- **A file that cannot be read emits `MISSING`**, it does not vanish from the
  output. A verifier must be able to tell "not there" from "not asked about".

## Deletion is album-granular, and the album comes from tags

`-Delete` removes an album only when **every** file in it is proven; one
unverified track keeps the whole album.

⚠️ **A folder is not an album.** `C:\Rips` is flat — Picard writes several
albums interleaved into it — so grouping by directory puts every rip in one
group, and a single unproven track blocks the entire box. The first run showed
exactly that: 11 unproven Daft Punk remixes held back 301 files that were
provably in the library. Grouping therefore reads `album_artist`/`album`/`disc`
with ffprobe, the same way the import does. Only the *grouping* uses tags —
matching stays on content, and none of the library-naming stack is involved.

The gate itself is not negotiable. On 2026-08-29 a 2xCD rip moved with only disc
2 present and the source was deleted anyway; disc 1 is still gone. Per file,
"the bytes are in the library" was true of everything that landed — which is
precisely how deleting only the proven files destroys an album. Files with no
usable tags are judged alone, never folded into a group they cannot be
attributed to. Each deletion is preceded by a
`DELETE_SAFE` row and followed by `LOCAL_DELETED`, so the ledger records the
authorisation before the act — and the run re-checks that the files are actually
gone before claiming the space back.

Without `-Delete` the script is read-only: it writes ledger rows and an audit
TSV (`<RUN_ID>.verify.tsv`, kept both locally and on hestia) and touches nothing
else.

---

# rescue-disc.ps1 — damaged discs: image first, decrypt second

MakeMKV reads and decrypts a disc in one pass. A damaged area stalls that pass,
and when it fails the next attempt starts again from sector 0. This script splits
the work in two:

1. **`ddrescue` images the disc to an ISO.** Pass 1 (`-n`) copies everything that
   reads cleanly and skips damage. Pass 2 (`-r N`) retries only what is still
   missing. Progress lives in a **map file**, so re-running the same command
   resumes. You can clean the disc or move it to another drive between runs, and
   only the missing sectors are read again.
2. **MakeMKV decrypts the ISO** (`-Mkv`) into `C:\Video\<Name>`.
   Without `-Mkv` the run stops after imaging. Ripping later means re-running
   with `-Mkv` **with the same disc still loaded**: the drive probe and identity
   check run on every invocation, before ddrescue and MakeMKV.

```powershell
cd C:\Users\George\src\homelab\hosts\winpc-5600x\media-pipeline
.\rescue-disc.ps1 -Name SPIDERVERSE -Mkv -Detach      # returns at once; survives SSH
Get-Content -Wait C:\media-work\rescue\SPIDERVERSE.log  # follow it
```

Imaging a BD-50 takes 1–2 hours. `-Detach` relaunches the script through
`Win32_Process Create`, like the other long jobs here, because `Start-Process`
dies with the SSH session. **To retry after cleaning the disc, run the exact same
command.**

Files in `C:\media-work\rescue\`: `<Name>.iso` (the image, ~50 GB), `.map`
(ddrescue progress), `.disc` (which disc this is), `.log` (every run, appended).

## Guards

| Guard | Why |
| :--- | :--- |
| **Disc identity on resume** | Resuming is the point, so the dangerous mistake is resuming with the *wrong disc* loaded: ddrescue would fill one film's gaps with another film's sectors and report success. The first run records the volume label and size in `<Name>.disc`. A resume against a different disc aborts. A map with no `.disc` file needs `-Force`. |
| **One run at a time** | Two runs would fight over one drive or one map. The lock is an **open file handle** (`.rescue.lock`), not a PID file, so the OS releases it when the process dies and a crash never leaves a stale lock. |
| **Completeness from the map, not the exit code** | ddrescue exits 0 when it *finishes*, not when it recovers everything. The script sums every block not marked `+` in the map itself, so the count doesn't depend on the wording of ddrescue's summary. The result is logged and written to the ledger. |
| **No `-s` size cap** | Measured on Spider-Verse: the device is 288 sectors larger than `Win32_CDROMDrive.Size`, which reports the filesystem, not the medium. Capping at it would cut off the end of the disc, where UDF keeps an anchor. The Windows size is only the identity check. |
| **Rip into `<Name>.partial`** | The folder is renamed to `<Name>` only when makemkvcon succeeds, so a failed rip never leaves half a title where `transcode.ps1` would pick it up. An existing `<Name>` needs `-Force`. |
| **Native stderr under `Stop`** | Windows PowerShell turns a native command's stderr into ErrorRecords under `2>&1`, and with `$ErrorActionPreference='Stop'` the first progress line would kill the run. Native calls run under `Continue` and are judged by exit code. |
| **`-HestiaHost`, not `-LedgerHost`** | `ledger.ps1` sets `$script:LedgerHost = $null` when dot-sourced, which silently blanked a parameter of that name. |
| **Ledger** | `RUN_START`, `IMAGED` (with `unrecovered=<bytes>`), `RIPPED`, `RUN_END` / `RUN_ABORT` go to the shared hestia ledger. |

## Why Cygwin, not WSL

WSL2 cannot see the optical drive without USB passthrough (`usbipd`), and
passthrough detaches the drive from Windows, so MakeMKV loses it. Cygwin reads
the Windows drive in place. Install once, no admin needed:

```powershell
curl.exe -sSLo C:\media-work\setup-x86_64.exe https://cygwin.com/setup-x86_64.exe
C:\media-work\setup-x86_64.exe --quiet-mode --no-admin --root C:\cygwin64 `
  --site https://mirrors.kernel.org/sourceware/cygwin/ --packages ddrescue
```

Cygwin numbers `/dev/srN` by Windows device index, not drive letter. On this box
`/dev/sr0` is a phantom (`No such device`) and the BD-RE is `/dev/sr1`, so the
script reads one sector from each `srN` and requires exactly one to answer.

## When imaging won't help

**ddrescue does not decrypt.** The ISO is a raw copy, encryption included. If a
**clean** disc fails at the start with AACS or key errors, that's copy
protection, not damage. The BH16NS40 runs in **LibreDrive mode**
(`makemkvcon -r info disc:0` prints `Using LibreDrive mode`), which covers most
of that case.

Delete `<Name>.iso` once the transcode has landed. The script refuses to start
with less than `-MinFreeGB` (default 55) free, or less than the disc plus its rip.
