# media-pipeline (winpc-5600x)

Transcodes MakeMKV disc rips to x265 and pushes them into the hestia media
library that Jellyfin serves.

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

`libx265 -b:v 4700k -tag:v hvc1 -c:a aac -b:a 160k -ac 2 -c:s copy`, ~4.86 Mbps
ABR. Encodes at roughly 1.1x realtime on the 5600X (6c/12t), so a 2-hour film is
about 2 hours of wall time.
