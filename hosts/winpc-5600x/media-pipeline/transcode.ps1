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

  2. COLOUR METADATA. One title reproducibly encodes to a stream whose packets
     do not decode, while ffmpeg exits 0 and the duration reads correctly. The
     mechanism is NOT established - color_space=unknown is not sufficient on its
     own (another title is also unknown and encodes fine). What is established is
     that adding explicit BT.709 tags to otherwise-identical arguments produces a
     correct encode, on two separate runs. So this script injects them when a
     source lacks colour metadata: a validated workaround, and independently
     correct since BT.709 is right for HD Blu-ray. The validation gate above,
     not this, is the actual protection - it does not depend on knowing why.

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
  $cs = Probe $i.Src 'stream=color_space' 'v:0'
  $needsColour = (-not $cs) -or ($cs -match 'unknown') -or ($cs -match 'N/A')
  Log ("  QUEUED       : " + $i.Name + "  " + [math]::Round($d/60,1) + "min" + $(if ($needsColour) { '  [will inject BT.709 - source colour metadata missing]' } else { '' }))
  $runnable += [pscustomobject]@{ Src = $i.Src; Name = $i.Name; Dur = $d; NeedsColour = $needsColour }
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

  # NB: do not name this variable after the PowerShell automatic one - assigning
  # to it shadows the real one under [CmdletBinding()].
  $ffArgs = @('-nostdin','-y','-hide_banner','-loglevel','error','-i',$it.Src,
              '-map','0:v:0','-map','0:a:0','-map','0:s?',
              '-c:v','libx265','-b:v',"${Bitrate}k",'-tag:v','hvc1')
  if ($it.NeedsColour) { $ffArgs += @('-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709') }
  $ffArgs += @('-c:a','aac','-b:a','160k','-ac','2','-c:s','copy',$local)
  & $FF @ffArgs
  if ($LASTEXITCODE -ne 0) { Log "FAIL encode (exit $LASTEXITCODE): $name"; continue }

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
