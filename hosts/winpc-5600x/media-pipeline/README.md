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

`queue.tsv` is `<source path><TAB><Library Name>`. Adding a title never means
editing the script. Two rules:

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
against `duration × frame_rate` and fails below 99%. That is the check that
would have caught this on day one, and it is cheap — ffmpeg already prints the
number.

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
