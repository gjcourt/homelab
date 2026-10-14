<#
.SYNOPSIS
  Rip a scratched disc in two steps: image it with ddrescue, then decrypt the image with MakeMKV.

.DESCRIPTION
  MakeMKV reads and decrypts in one pass. A damaged sector stalls it, and its retry
  setting is a single knob. If it fails, the next run starts again from sector 0.

  ddrescue is built for damaged media. The first pass copies every sector that reads
  cleanly and skips the bad ones. The retry passes then work only on what is still
  missing. All progress is kept in a map file, so a run can be stopped, the disc
  cleaned or moved to another drive, and the run resumed where it left off.

  MakeMKV then reads the finished ISO. ddrescue does NOT decrypt: the image is a raw
  copy of the disc, encryption included.

  Runs under Cygwin. WSL2 cannot see the optical drive without USB passthrough, and
  passthrough would take the drive away from MakeMKV.

.PARAMETER Name
  Label for this disc. Used for the image, map and output folder names.

.PARAMETER RetryPasses
  ddrescue -r count for the retry phase. Default 3.

.PARAMETER Mkv
  After imaging, run makemkvcon on the ISO into $VideoDir\<Name>.

.EXAMPLE
  # image only. Re-run the same command to resume or retry.
  .\rescue-disc.ps1 -Name SPIDERVERSE

.EXAMPLE
  # long run, so detach it. Start-Process dies with the SSH session.
  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = 'cmd.exe /c "cd /d C:\Users\George\src\homelab && powershell -NoProfile -ExecutionPolicy Bypass -File hosts\winpc-5600x\media-pipeline\rescue-disc.ps1 -Name SPIDERVERSE -Mkv > C:\media-work\rescue-SPIDERVERSE.log 2>&1"'
  }
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._-]+$')][string]$Name,
  [string]$WorkDir     = 'C:\media-work\rescue',
  [string]$VideoDir    = 'C:\Video',
  [int]$RetryPasses    = 3,
  [int]$MinFreeGB      = 55,
  [switch]$Mkv,
  [string]$Cygwin      = 'C:\cygwin64',
  [string]$MakeMkvCon  = 'C:\Program Files (x86)\MakeMKV\makemkvcon64.exe'
)
$ErrorActionPreference = 'Stop'
function Log($m) { Write-Output ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) }

$bash = Join-Path $Cygwin 'bin\bash.exe'
if (-not (Test-Path $bash)) { throw "Cygwin not found at $Cygwin. Install: setup-x86_64.exe --quiet-mode --no-admin --root $Cygwin --packages ddrescue" }
function Cyg([string]$cmd) {
  # Return output plus exit code. Callers decide what a failure means.
  $out = & $bash -lc $cmd 2>&1
  [pscustomobject]@{ Code = $LASTEXITCODE; Out = ($out -join "`n") }
}
function CygPath([string]$win) { (& (Join-Path $Cygwin 'bin\cygpath.exe') -u $win).Trim() }

New-Item -ItemType Directory -Force $WorkDir | Out-Null
$iso = Join-Path $WorkDir "$Name.iso"
$map = Join-Path $WorkDir "$Name.map"
$cIso = CygPath $iso; $cMap = CygPath $map

# ---- 1. find the drive ------------------------------------------------------
# Cygwin numbers /dev/srN by Windows CD-ROM device index, not drive letter, and on
# this box /dev/sr0 is a phantom device ("No such device"). Probe every srN with a
# one-sector read and require exactly one to answer, rather than hard-coding sr1.
$probe = Cyg 'for d in /dev/sr*; do dd if=$d of=/dev/null bs=2048 count=1 status=none 2>/dev/null && echo $d; done'
$devs = @($probe.Out -split "`n" | Where-Object { $_ -match '^/dev/sr\d+$' })
if ($devs.Count -ne 1) { throw "expected exactly one readable optical device, found $($devs.Count): $($devs -join ', '). Is a disc loaded?" }
$dev = $devs[0]
Log "drive: $dev"

# ---- 2. space ---------------------------------------------------------------
# A BD-50 image is up to 50 GB. A resumed run already has most of it on disk.
$have = if (Test-Path $iso) { (Get-Item $iso).Length } else { 0 }
$free = (Get-PSDrive ($WorkDir.Substring(0,1))).Free
if (($free + $have) / 1GB -lt $MinFreeGB) { throw ("{0:N0} GB free on {1}: below the {2} GB a Blu-ray image can need" -f ($free/1GB), $WorkDir.Substring(0,2), $MinFreeGB) }

# ---- 3. image ---------------------------------------------------------------
# -b 2048: the optical sector size, so ddrescue's block math matches the disc.
# Pass 1 (-n) skips the slow scraping phase and grabs everything easy first.
# Pass 2 (-r N) retries only what pass 1 left. Both read and update the same map,
# so any re-run of this script resumes instead of starting over.
if (Test-Path $map) { Log "resuming from existing map $map" }
Log '== ddrescue pass 1: copy everything that reads cleanly =='
$p1 = Cyg "ddrescue -b 2048 -n '$dev' '$cIso' '$cMap'"
$p1.Out -split "`n" | Select-Object -Last 8 | ForEach-Object { Log "  $_" }
if ($p1.Code -ne 0) { throw "ddrescue pass 1 exited $($p1.Code)" }

Log "== ddrescue pass 2: retry bad areas ($RetryPasses passes) =="
$p2 = Cyg "ddrescue -b 2048 -r $RetryPasses '$dev' '$cIso' '$cMap'"
$p2.Out -split "`n" | Select-Object -Last 8 | ForEach-Object { Log "  $_" }
if ($p2.Code -ne 0) { throw "ddrescue pass 2 exited $($p2.Code)" }

# ---- 4. what is still missing ------------------------------------------------
# The map is the truth, not ddrescue's exit code: exit 0 only means it finished,
# not that every sector was recovered. ddrescuelog -t prints the totals.
$t = Cyg "ddrescuelog -t '$cMap'"
$t.Out -split "`n" | ForEach-Object { Log "  $_" }
$bad = 0
if ($t.Out -match 'non-tried:\s*(\d+)\s*B') { $bad += [int64]$Matches[1] }
if ($t.Out -match 'bad-sector:\s*(\d+)\s*B') { $bad += [int64]$Matches[1] }
if ($t.Out -match 'non-trimmed:\s*(\d+)\s*B') { $bad += [int64]$Matches[1] }
if ($t.Out -match 'non-scraped:\s*(\d+)\s*B') { $bad += [int64]$Matches[1] }
if ($bad -gt 0) {
  Log ("UNRECOVERED: {0:N0} bytes ({1:N1} MB). Clean the disc or move it to another drive, then re-run the same command to retry only those areas." -f $bad, ($bad/1MB))
} else {
  Log 'image complete: every sector recovered'
}

# ---- 5. decrypt -------------------------------------------------------------
if (-not $Mkv) {
  Log "next: re-run with -Mkv, or: & '$MakeMkvCon' -r mkv iso:$iso all $VideoDir\$Name"
  exit 0
}
if ($bad -gt 0) {
  # A gap usually costs a few seconds of picture. Rip anyway, but say so loudly:
  # transcode.ps1's frame and duration gates are the check on the result.
  Log 'WARNING: ripping an image with unrecovered sectors. Expect glitches where the gaps fall.'
}
$out = Join-Path $VideoDir $Name
New-Item -ItemType Directory -Force $out | Out-Null
Log "== makemkvcon: $iso -> $out =="
& $MakeMkvCon -r --progress=-same mkv "iso:$iso" all $out 2>&1 |
  Where-Object { $_ -match '^MSG:' } | ForEach-Object { Log "  $_" }
if ($LASTEXITCODE -ne 0) { throw "makemkvcon exited $LASTEXITCODE" }
Get-ChildItem $out -Filter *.mkv | ForEach-Object { Log ("  {0}  {1:N1} GB" -f $_.Name, ($_.Length/1GB)) }
Log "done. The ISO is kept at $iso until you delete it. Pick the main feature by duration before queueing it for transcode.ps1."
