<#
.SYNOPSIS
  Transcode disc rips to x265 and push them to the hestia media library.

.DESCRIPTION
  Single parameterised replacement for transcode-batch.ps1, transcode-batch2.ps1,
  transcode-batch3.ps1, transcode-extras.ps1 and chain-extras.ps1, which had all
  drifted apart while re-implementing the same push logic.

  The queue is DATA (a TSV file), not code, so adding a title never means editing
  this script.

  Two defects from the previous generation are fixed here:

  1. VALIDATION. The old scripts gated success on ffmpeg's exit code alone. On
     2026-08-04 ffmpeg exited 0 after 3m09s on a 137-minute film and a
     72MB / 108kbps file was pushed and marked DONE, then the local copy deleted.
     This script validates the OUTPUT before pushing: duration within tolerance,
     bitrate above a floor, AND a real decoded frame. Duration and bitrate alone
     are not enough - a broken stream can report the right duration.

  2. SUBTITLE BACKPRESSURE DEADLOCK - ROOT CAUSE, found 2026-08-29.
     Mapping subtitles into the ENCODE pass (`-map 0:s?` + `-c:s copy`) can make
     ffmpeg silently encode almost nothing. If any mapped output stream produces
     packets very rarely - a forced-narrative subtitle track with 8 cues across a
     137-minute film - its output queue never fills, so the demuxer never blocks.
     ffmpeg then reads the ENTIRE source into RAM (21.5 GB observed on a 40 GB
     input), reaches EOF, and shuts down CLEANLY with ~195,000 video packets
     still queued and never encoded.
     The result passes every naive check: exit code 0, correct container
     duration, video packets present across the whole timeline. Only 1671 of
     197,304 frames were actually encoded - and x265 encoded those 1671
     perfectly, at 5028 kb/s and QP 19.56. The encoder was never the problem.
     Fix: encode video+audio ONLY, then mux subtitles back in a separate
     `-c copy` pass where nothing can starve. Colour metadata, frame rate and
     rate control were all investigated and are all irrelevant - they were
     coincidences of the one title that happened to have a sparse track.

.PARAMETER Queue
  TSV file, one title per line: <source path><TAB><Library Name>
  Blank lines and lines starting with # are ignored.

.EXAMPLE
  .\transcode.ps1 -Queue .\queue.tsv
  .\transcode.ps1 -Queue .\queue.tsv -DryRun
  .\transcode.ps1 -Queue .\queue.tsv -WaitForIdle
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Queue,
  [int]$Bitrate    = 4700,   # kbps target
  [int]$MinKbps    = 1500,   # reject below this - the encode is broken
  [double]$DurTolPct = 1.0,  # output duration must be within this % of source
  [int]$MinFreeGB  = 60,     # refuse to start without this much headroom
  [int]$MinFrameKB = 100,    # a decoded frame smaller than this is near-blank
  [switch]$DryRun,           # validate the queue and exit, encode nothing
  [switch]$WaitForIdle       # wait for any other ffmpeg to finish first
)

$ErrorActionPreference = 'Continue'
$FFDIR = 'C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin'
$FF    = Join-Path $FFDIR 'ffmpeg.exe'
$FP    = Join-Path $FFDIR 'ffprobe.exe'
$HST   = 'truenas_admin@10.42.2.10'
$HBASE = '/mnt/main/family'
$WORK  = 'C:\media-work'

function Log($m) { Write-Output ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $m) }
function Probe($f, $entries, $stream) {
  $a = @('-v','quiet','-show_entries',$entries,'-of','default=noprint_wrappers=1:nokey=1')
  if ($stream) { $a = @('-select_streams',$stream) + $a }
  (& $FP @a $f 2>$null)
}
function Dur($f) { $d = Probe $f 'format=duration' $null; if ($d) { [double]$d } else { 0 } }
function OnHestia($rel) { (ssh -n -o BatchMode=yes $HST "sudo -n test -f '$HBASE/$rel' && echo yes") -match 'yes' }

foreach ($t in @($FF, $FP)) { if (-not (Test-Path $t)) { Log "ABORT: missing $t"; exit 1 } }
if (-not (Test-Path $Queue)) { Log "ABORT: queue not found: $Queue"; exit 1 }
New-Item -ItemType Directory -Path $WORK -Force | Out-Null

# ---- parse queue -------------------------------------------------------
$items = @()
foreach ($line in Get-Content $Queue) {
  $l = $line.Trim()
  if (-not $l -or $l.StartsWith('#')) { continue }
  $parts = $l -split "`t+"
  if ($parts.Count -lt 2) { Log "SKIP malformed queue line: $l"; continue }
  $items += [pscustomobject]@{ Src = $parts[0].Trim(); Name = $parts[1].Trim() }
}
if (-not $items) { Log 'ABORT: queue is empty'; exit 1 }

# ---- preflight: report everything before encoding anything -------------
Log "== PREFLIGHT ($($items.Count) titles) =="
$runnable = @()
foreach ($i in $items) {
  if (-not (Test-Path $i.Src)) { Log ("  MISSING src  : " + $i.Name + "  <= " + $i.Src); continue }
  $d = Dur $i.Src
  if ($d -le 0) { Log ("  UNREADABLE   : " + $i.Name); continue }
  $rel = "media/video/movies/$($i.Name)/$($i.Name).mkv"
  if (OnHestia $rel) { Log ("  SKIP on hestia: " + $i.Name); continue }
  $nsub = @(Probe $i.Src 'stream=index' 's').Count
  Log ("  QUEUED       : " + $i.Name + "  " + [math]::Round($d/60,1) + "min  subs=" + $nsub + " (muxed after encode, never mapped into it)")
  $runnable += [pscustomobject]@{ Src = $i.Src; Name = $i.Name; Dur = $d; Subs = $nsub }
}
Log ("== PREFLIGHT DONE: $($runnable.Count) to encode ==")
if ($DryRun) { Log 'DRY RUN - stopping here'; exit 0 }
if (-not $runnable) { Log 'nothing to do'; exit 0 }

if ($WaitForIdle) {
  while (Get-Process ffmpeg -ErrorAction SilentlyContinue) { Log 'another ffmpeg is running, waiting 60s'; Start-Sleep -Seconds 60 }
}

# ---- encode ------------------------------------------------------------
foreach ($it in $runnable) {
  $name = $it.Name
  $dest = "media/video/movies/$name"; $rel = "$dest/$name.mkv"
  $free = (Get-PSDrive C).Free / 1GB
  if ($free -lt $MinFreeGB) { Log ("ABORT low disk: " + [math]::Round($free,1) + "GB < ${MinFreeGB}GB"); break }

  $local = Join-Path $WORK "$name.mkv"
  if (Test-Path $local) { Remove-Item $local -Force; Log "removed stale local for $name" }
  Log ("START $name  src=" + [math]::Round($it.Dur/60,1) + "min  free=" + [math]::Round($free,0) + "GB" + $(if ($it.NeedsColour) { '  (+BT.709)' } else { '' }))

  # STAGE 1 - video + audio ONLY. Subtitles are deliberately NOT mapped here:
  # a sparse subtitle track defeats demuxer backpressure and silently drops
  # ~99% of frames (see header). `-tag:v hvc1` is also gone - it is an
  # MP4-family FourCC and meaningless in Matroska.
  # `-v warning -stats` (not -loglevel error): the failing run was invisible
  # precisely because ffmpeg's own frame=/time= line was suppressed.
  $va = Join-Path $WORK "$name.va.mkv"
  if (Test-Path $va) { Remove-Item $va -Force }
  $ffArgs = @('-nostdin','-y','-hide_banner','-v','warning','-stats','-i',$it.Src,
              '-map','0:v:0','-map','0:a:0',
              '-c:v','libx265','-b:v',"${Bitrate}k",
              '-c:a','aac','-b:a','160k','-ac','2',$va)
  & $FF @ffArgs
  if ($LASTEXITCODE -ne 0) { Log "FAIL encode (exit $LASTEXITCODE): $name"; continue }

  # ---- FRAME-COUNT GATE: the check that would have caught this on day one ----
  # ffmpeg exits 0 having encoded 0.85% of the frames, so exit code proves
  # nothing. Compare frames actually present against duration x frame rate.
  $fps = Probe $va 'stream=r_frame_rate' 'v:0'
  $num,$den = ($fps -split '/')
  $fpsVal = if ($den) { [double]$num / [double]$den } else { [double]$num }
  $expected = [math]::Round($it.Dur * $fpsVal)
  $actual = [int](Probe $va 'stream=nb_frames' 'v:0')
  if ($actual -le 0) {
    $cnt = & $FP -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of default=nw=1:nk=1 $va 2>$null
    $actual = [int]$cnt
  }
  $framePct = if ($expected -gt 0) { $actual / $expected * 100 } else { 0 }
  if ($framePct -lt 99) {
    Log ("FAIL frame count: $name has $actual frames, expected ~$expected (" + [math]::Round($framePct,2) + "%) -- keeping local. This is the subtitle-backpressure signature.")
    continue
  }
  Log ("  frames OK: $actual / ~$expected (" + [math]::Round($framePct,1) + "%)")

  # STAGE 2 - mux the subtitle tracks back in a pure copy pass, where a sparse
  # stream cannot starve anything.
  if ($it.Subs -gt 0) {
    & $FF -nostdin -y -hide_banner -v warning -i $va -i $it.Src -map '0:v:0' -map '0:a:0' -map '1:s' -c copy $local
    if ($LASTEXITCODE -ne 0) { Log "FAIL subtitle mux: $name -- keeping $va"; continue }
    Remove-Item $va -Force
  } else { Move-Item $va $local -Force }

  # ---- validation gate ------------------------------------------------
  if (-not (Test-Path $local)) { Log "FAIL no output file: $name"; continue }
  $outLen = (Get-Item $local).Length
  $outDur = Dur $local
  if ($outDur -le 0) { Log "FAIL unreadable output: $name"; continue }
  $durDelta = [math]::Abs($outDur - $it.Dur) / $it.Dur * 100
  $kbps = [math]::Round($outLen * 8 / $outDur / 1000)
  if ($durDelta -gt $DurTolPct) { Log ("FAIL duration delta=" + [math]::Round($durDelta,2) + "% : $name -- keeping local"); continue }
  if ($kbps -lt $MinKbps) { Log ("FAIL bitrate ${kbps}kbps < ${MinKbps}kbps : $name -- keeping local"); continue }
  # the check the old scripts lacked: prove there is a picture
  $png = Join-Path $WORK '_verify.png'
  Remove-Item $png -ErrorAction SilentlyContinue
  $mid = [int]($outDur / 2)
  & $FF -nostdin -y -hide_banner -loglevel error -ss $mid -i $local -frames:v 1 $png 2>$null
  if (-not (Test-Path $png)) { Log "FAIL no decodable frame at ${mid}s : $name -- keeping local"; continue }
  $pngKB = [math]::Round((Get-Item $png).Length / 1KB)
  Remove-Item $png -Force
  if ($pngKB -lt $MinFrameKB) { Log "FAIL frame at ${mid}s only ${pngKB}KB (near-blank) : $name -- keeping local"; continue }
  Log ("VALIDATED $name  " + [math]::Round($outLen/1GB,2) + "GB  ${kbps}kbps  dur_delta=" + [math]::Round($durDelta,3) + "%  frame=${pngKB}KB")

  # ---- push, verify byte count, only then delete local -----------------
  $tmp = "/tmp/mediapush_$([guid]::NewGuid().ToString('N')).mkv"
  scp -o BatchMode=yes $local "$($HST):$tmp" | Out-Null
  if ($LASTEXITCODE -ne 0) { Log "FAIL scp: $name -- keeping local"; ssh -n -o BatchMode=yes $HST "rm -f '$tmp'" | Out-Null; continue }
  ssh -n -o BatchMode=yes $HST "sudo -n mkdir -p '$HBASE/$dest' && sudo -n mv '$tmp' '$HBASE/$rel' && sudo -n chown 1028:100 '$HBASE/$rel' && sudo -n chmod 644 '$HBASE/$rel'"
  if ($LASTEXITCODE -ne 0) { Log "FAIL push: $name -- keeping local"; continue }
  $remote = (ssh -n -o BatchMode=yes $HST "sudo -n stat -c %s '$HBASE/$rel'") -join ''
  if ($remote.Trim() -eq "$outLen") { Remove-Item $local -Force; Log "DONE $name (hestia verified $remote bytes)" }
  else { Log "FAIL size mismatch: local=$outLen remote=$remote : $name -- keeping local" }
}
Log '== ALL DONE =='
