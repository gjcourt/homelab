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

### 2. libx265 can silently emit an undecodable stream

On this library exactly one title — *The Tale of the Princess Kaguya* — reproducibly
encodes to a stream whose **packets do not decode into frames**, while ffmpeg exits 0
and the container reports the correct 137-minute duration. It happened on 2026-08-04
and again, identically, on 2026-08-29 (3m09s/110MB then 3m23s/106MB, both 108 kbps).

**The mechanism is not established.** Recording what was actually tested, because
several plausible-sounding explanations were checked and disproved:

| Hypothesis | Verdict |
| :--- | :--- |
| Truncated / partial encode | **No** — output has video packets across the full 137 min |
| Wrong stream picked by `-map 0:v:0` | **No** — the source has exactly one video stream |
| Corrupt source | **No** — source has 24 fps of packets at every timestamp and extracts 2.3 MB frames |
| Disk-space cascade from the preceding failed push | **No** — reproduced later with 570 GB free |
| Missing colour metadata (`color_space=unknown`) | **Not sufficient** — *Marty Supreme* is also `unknown` and encodes healthily |
| Exactly-24 fps (`24/1`) vs `24000/1001` | Correlates across n=3, **not isolated** — the follow-up test changed two variables |

**What reliably works:** adding explicit BT.709 tags to the otherwise-identical real
arguments produced a correct 4.8 Mbps decodable encode on two separate runs. So the
script injects them when a source lacks colour metadata. That is a *validated
workaround*, not a proven fix for a understood cause — and it is independently
correct, since BT.709 is the right colour space for HD Blu-ray and those sources
genuinely are missing it.

**The validation gate, not the workaround, is the actual protection.** It does not
depend on knowing the cause: any encode that fails to produce a decodable frame is
refused, whatever produced it.

## Encoder settings

`libx265 -b:v 4700k -tag:v hvc1 -c:a aac -b:a 160k -ac 2 -c:s copy`, ~4.86 Mbps
ABR. Encodes at roughly 1.1x realtime on the 5600X (6c/12t), so a 2-hour film is
about 2 hours of wall time.
