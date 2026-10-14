<#
.SYNOPSIS
  Rip a damaged disc in two steps: image it with ddrescue, then decrypt the image with MakeMKV.

.DESCRIPTION
  MakeMKV reads and decrypts in one pass. A damaged sector stalls it, its retry
  setting is a single knob, and when it fails the next run starts from sector 0.

  ddrescue is built for damaged media. Pass 1 copies every sector that reads
  cleanly and skips the bad ones; pass 2 works only on what is still missing.
  Progress lives in a MAP FILE, so a run can be stopped, the disc cleaned or moved
  to another drive, and the same command re-run to retry only the gaps.

  MakeMKV then reads the finished ISO. ddrescue does NOT decrypt: the ISO is a raw
  copy of the disc, encryption included.

  Runs under Cygwin. WSL2 cannot see the optical drive without USB passthrough,
  and passthrough takes the drive away from MakeMKV.

  Durability - each guard is here because a resumable, unattended, hours-long job
  fails in ways a one-shot rip does not:

    * DISC IDENTITY. Resuming is the point, so the dangerous mistake is resuming
      with the WRONG DISC in the drive: ddrescue would happily fill the gaps of
      one film's image with another film's sectors and call it complete. The
      first run records the disc's volume label and size in <Name>.disc; every
      resume refuses unless the loaded disc matches.
    * ONE RUN AT A TIME. Two runs would fight over one drive, or over one map.
      The lock is an open file handle, not a PID file, so the OS releases it when
      the process dies and a crash never leaves a stale lock behind.
    * ITS OWN LOG. Every line also goes to <Name>.log, appended, so the history
      of a disc that took four sessions is in one place whatever the caller did
      with stdout.
    * -Detach. Start-Process dies with the SSH session. -Detach relaunches this
      script through Win32_Process Create, the same way the other long jobs in
      this directory are run, so nobody has to remember the incantation.
    * RIP INTO .partial. MakeMKV writes to <Name>.partial and the folder is
      renamed only on success, so a failed rip never leaves half a title where
      transcode.ps1 would pick it up.
    * LEDGER. RUN_START, IMAGED (with the unrecovered byte count), RIPPED and
      RUN_END/RUN_ABORT go to the shared hestia ledger (ledger.ps1).

.PARAMETER Name
  Label for this disc. Names the image, map, log and output folder.

.PARAMETER RetryPasses
  ddrescue -r count for the retry phase. Default 3.

.PARAMETER Mkv
  After imaging, rip the ISO with makemkvcon into $VideoDir\<Name>.

.PARAMETER Detach
  Relaunch detached from the SSH session and return immediately.
  Follow the log at $WorkDir\<Name>.log.

.EXAMPLE
  # image, then rip; survives the SSH session
  .\rescue-disc.ps1 -Name SPIDERVERSE -Mkv -Detach

.EXAMPLE
  # disc came out clean on the second drive: same command, only the gaps are re-read
  .\rescue-disc.ps1 -Name SPIDERVERSE -Mkv -Detach
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._-]+$')][string]$Name,
  [string]$WorkDir     = 'C:\media-work\rescue',
  [string]$VideoDir    = 'C:\Video',
  [ValidateRange(0, 20)][int]$RetryPasses = 3,
  [int]$MinFreeGB      = 55,
  [switch]$Mkv,
  [switch]$Detach,
  [switch]$Force,
  [string]$Cygwin      = 'C:\cygwin64',
  [string]$MakeMkvCon  = 'C:\Program Files (x86)\MakeMKV\makemkvcon64.exe',
  # NOT named $LedgerHost: ledger.ps1 assigns $script:LedgerHost = $null when it
  # is dot-sourced, which silently blanked a parameter of that name.
  [string]$HestiaHost  = 'truenas_admin@10.42.2.10'
)
$ErrorActionPreference = 'Stop'

New-Item -ItemType Directory -Force $WorkDir | Out-Null
$iso  = Join-Path $WorkDir "$Name.iso"
$map  = Join-Path $WorkDir "$Name.map"
$id   = Join-Path $WorkDir "$Name.disc"
$logf = Join-Path $WorkDir "$Name.log"

# ---- -Detach: hand off and return -------------------------------------------
if ($Detach) {
  $argv = @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`"",'-Name',$Name,
            '-WorkDir',"`"$WorkDir`"",'-VideoDir',"`"$VideoDir`"",'-RetryPasses',$RetryPasses,
            '-MinFreeGB',$MinFreeGB,'-Cygwin',"`"$Cygwin`"",'-MakeMkvCon',"`"$MakeMkvCon`"",'-HestiaHost',$HestiaHost)
  if ($Mkv)   { $argv += '-Mkv' }
  if ($Force) { $argv += '-Force' }
  # Output is discarded: the run writes its own log, so a redirect would only duplicate it.
  $cmd = "cmd.exe /c `"powershell $($argv -join ' ') >nul 2>&1`""
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
  if ($r.ReturnValue -ne 0) { throw "detach failed: Win32_Process.Create returned $($r.ReturnValue)" }
  Write-Output "detached: pid $($r.ProcessId). Follow: Get-Content -Wait $logf"
  exit 0
}

function Log($m) {
  $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
  Write-Output $line
  # The log must never be the thing that kills a three-hour run.
  try { [IO.File]::AppendAllText($logf, $line + "`n", [Text.UTF8Encoding]::new($false)) } catch { }
}

# ---- one run at a time ------------------------------------------------------
$lockPath = Join-Path $WorkDir '.rescue.lock'
try {
  $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
  Log "ABORT: cannot take $lockPath - another rescue-disc run holds it? One drive, one run. ($($_.Exception.Message))"
  exit 1
}

. (Join-Path $PSScriptRoot 'ledger.ps1')
$RUN_ID = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
Initialize-Ledger -RemoteHost $HestiaHost -RunId $RUN_ID
function TitleLedger([string]$event, [hashtable]$f) {
  $rec = [pscustomobject](@{ Scope = 'title'; Title = $Name } + $f)
  Write-Ledger -Event $event -Records @($rec); Log "  ledger: $event"
}

$bash = Join-Path $Cygwin 'bin\bash.exe'
function Cyg([string]$cmd) {
  # Output plus exit code. Callers decide what a failure means.
  # Windows PowerShell turns a native command's stderr into ErrorRecords under
  # 2>&1, and with 'Stop' the first progress line on stderr would kill the run.
  $ErrorActionPreference = 'Continue'
  $out = & $bash -lc $cmd 2>&1 | ForEach-Object { "$_" }
  [pscustomobject]@{ Code = $LASTEXITCODE; Out = ($out -join "`n") }
}
function CygPath([string]$win) { (& (Join-Path $Cygwin 'bin\cygpath.exe') -u $win).Trim() }

function Abort([string]$why) {
  Log "ABORT: $why"
  Write-LedgerRun -Event 'RUN_ABORT' -Note $why
  exit 1
}

try {
  Log "== rescue-disc $Name (run $RUN_ID) =="
  Write-LedgerRun -Event 'RUN_START' -Note "name=$Name mkv=$Mkv retry=$RetryPasses"

  if (-not (Test-Path $bash)) { Abort "Cygwin not found at $Cygwin. Install: setup-x86_64.exe --quiet-mode --no-admin --root $Cygwin --packages ddrescue" }
  if ($Mkv -and -not (Test-Path $MakeMkvCon)) { Abort "makemkvcon not found at $MakeMkvCon" }
  $cIso = CygPath $iso; $cMap = CygPath $map

  # ---- 1. find the drive ----------------------------------------------------
  # Cygwin numbers /dev/srN by Windows device index, not drive letter, and on
  # this box /dev/sr0 is a phantom ("No such device"). Probe every srN with a
  # one-sector read and require exactly one to answer, rather than hard-coding.
  $probe = Cyg 'for d in /dev/sr*; do dd if=$d of=/dev/null bs=2048 count=1 status=none 2>/dev/null && echo $d; done'
  $devs = @($probe.Out -split "`n" | Where-Object { $_ -match '^/dev/sr\d+$' })
  if ($devs.Count -ne 1) { Abort "expected exactly one readable optical device, found $($devs.Count) ($($devs -join ', ')). Is a disc loaded?" }
  $dev = $devs[0]

  # ---- 2. disc identity -----------------------------------------------------
  $drv = @(Get-CimInstance Win32_CDROMDrive | Where-Object { $_.MediaLoaded })
  if ($drv.Count -ne 1) { Abort "expected one drive with media loaded, found $($drv.Count)" }
  $disc = [pscustomobject]@{ Label = "$($drv[0].VolumeName)"; Size = [int64]$drv[0].Size; Drive = $drv[0].Drive }
  if (-not $disc.Label -or $disc.Size -le 0) { Abort "could not read the loaded disc's label/size from Windows" }
  Log ("drive: {0} = {1}  disc: {2}  {3:N1} GB" -f $dev, $disc.Drive, $disc.Label, ($disc.Size / 1GB))

  if (Test-Path $id) {
    $want = Get-Content $id -Raw | ConvertFrom-Json
    if ($want.Label -ne $disc.Label -or [int64]$want.Size -ne $disc.Size) {
      Abort ("loaded disc is {0} ({1:N0} B) but {2} belongs to {3} ({4:N0} B). Resuming would splice two discs into one image." -f $disc.Label, $disc.Size, $map, $want.Label, [int64]$want.Size)
    }
    Log 'disc matches the one this image was started from - resuming'
  } elseif ((Test-Path $map) -or (Test-Path $iso)) {
    # A map with no identity file predates this guard; nothing to compare against.
    if (-not $Force) { Abort "$map exists with no $id to prove which disc it came from. Re-run with -Force if you are sure it is this disc." }
    Log 'WARNING: -Force: adopting an existing image with no recorded disc identity'
  }
  if (-not (Test-Path $id)) { $disc | ConvertTo-Json | Set-Content -Encoding UTF8 $id }

  # ---- 3. space -------------------------------------------------------------
  # Image and rip both land on this volume; a resumed run already has most of the image.
  $have = if (Test-Path $iso) { (Get-Item $iso).Length } else { 0 }
  $need = ($disc.Size + 64MB - $have) + $(if ($Mkv) { $disc.Size } else { 0 })   # +64MB: the device runs slightly past Windows' size
  $free = (Get-PSDrive ($WorkDir.Substring(0,1))).Free
  if ($free -lt $need -or ($free + $have) / 1GB -lt $MinFreeGB) {
    Abort ("{0:N0} GB free on {1}; need ~{2:N0} GB for this disc{3}, and at least -MinFreeGB {4}" -f ($free/1GB), $WorkDir.Substring(0,2), ($need/1GB), $(if ($Mkv) { ' plus its rip' } else { '' }), $MinFreeGB)
  }

  # ---- 4. image -------------------------------------------------------------
  # -b 2048: the optical sector size. Pass 1 (-n) skips the slow scraping phase;
  # pass 2 (-r N) retries only what pass 1 left. Both read and update the same
  # map, so any re-run resumes rather than restarting.
  # ⚠️ No -s cap from the Windows size. Measured 2026-10-13 on Spider-Verse: the
  # device is 589,824 B (288 sectors) LARGER than Win32_CDROMDrive.Size, which
  # reports the filesystem, not the medium. Capping at it would cut the tail of
  # the disc, where UDF keeps an anchor descriptor. Windows' size is used only
  # as the identity check, where all that matters is that it is stable.
  if (Test-Path $map) { Log "resuming from $map" }
  Log '== ddrescue pass 1: copy everything that reads cleanly =='
  $p1 = Cyg "ddrescue -b 2048 -n '$dev' '$cIso' '$cMap'"
  $p1.Out -split "`n" | Select-Object -Last 6 | ForEach-Object { Log "  $_" }
  if ($p1.Code -ne 0) { Abort "ddrescue pass 1 exited $($p1.Code)" }

  if ($RetryPasses -gt 0) {
    Log "== ddrescue pass 2: retry bad areas ($RetryPasses passes) =="
    $p2 = Cyg "ddrescue -b 2048 -r $RetryPasses '$dev' '$cIso' '$cMap'"
    $p2.Out -split "`n" | Select-Object -Last 6 | ForEach-Object { Log "  $_" }
    if ($p2.Code -ne 0) { Abort "ddrescue pass 2 exited $($p2.Code)" }
  }

  # ---- 5. what is still missing ----------------------------------------------
  # The map is the truth, not the exit code: exit 0 means ddrescue finished, not
  # that it recovered everything. Sum every not-yet-rescued state from the map
  # itself (status column: '+' rescued; '?', '*', '/', '-' all not), so the
  # count does not depend on the wording of ddrescuelog's human summary.
  $mapLines = Get-Content $map | Where-Object { $_ -match '^0x[0-9A-Fa-f]+\s+0x[0-9A-Fa-f]+\s+[?*/+-]' }
  if (-not $mapLines) { Abort "could not parse any blocks from $map" }
  $bad = [int64]0
  foreach ($l in $mapLines) {
    $f = $l -split '\s+'
    if ($f[2] -ne '+') { $bad += [Convert]::ToInt64($f[1].Substring(2), 16) }
  }
  TitleLedger 'IMAGED' @{ Bytes = (Get-Item $iso).Length; Src = $disc.Label; Dest = $iso; Note = "unrecovered=$bad" }
  if ($bad -gt 0) {
    Log ("UNRECOVERED: {0:N0} bytes ({1:N1} MB). Clean the disc or move it to another drive, then re-run the same command; only those areas are read again." -f $bad, ($bad / 1MB))
  } else {
    Log 'image complete: every sector recovered'
  }

  # ---- 6. decrypt -----------------------------------------------------------
  if (-not $Mkv) {
    Log "next: re-run with -Mkv, disc still loaded, to rip $iso (the identity check and ddrescue run again first; on a complete map ddrescue has nothing left to read)"
    Write-LedgerRun -Event 'RUN_END' -Note "imaged only, unrecovered=$bad"
    exit 0
  }
  $out = Join-Path $VideoDir $Name
  $partial = "$out.partial"
  if ((Test-Path $out) -and -not $Force) { Abort "$out already exists. Move it aside, or -Force to replace it." }
  if ($bad -gt 0) {
    # A gap usually costs a few seconds of picture. Rip anyway, loudly:
    # transcode.ps1's frame and duration gates are the real check on the result.
    Log 'WARNING: ripping an image with unrecovered sectors. Expect glitches where the gaps fall.'
  }
  if (Test-Path $partial) { Remove-Item -Recurse -Force $partial }   # a previous failed rip, never a finished one
  New-Item -ItemType Directory -Force $partial | Out-Null
  Log "== makemkvcon: $iso -> $out =="
  $ErrorActionPreference = 'Continue'                                # same stderr issue as Cyg
  & $MakeMkvCon -r --progress=-same mkv "iso:$iso" all $partial 2>&1 |
    Where-Object { $_ -match '^MSG:' } | ForEach-Object { Log "  $_" }
  $rc = $LASTEXITCODE
  $ErrorActionPreference = 'Stop'
  $mkvs = @(Get-ChildItem $partial -Filter *.mkv -ErrorAction SilentlyContinue)
  if ($rc -ne 0 -or $mkvs.Count -eq 0) { Abort "makemkvcon exited $rc with $($mkvs.Count) title(s); partial output left in $partial" }
  if (Test-Path $out) { Remove-Item -Recurse -Force $out }           # only reachable with -Force
  Rename-Item $partial (Split-Path -Leaf $out)
  $mkvs = @(Get-ChildItem $out -Filter *.mkv)
  $mkvs | ForEach-Object { Log ('  {0}  {1:N1} GB' -f $_.Name, ($_.Length / 1GB)) }
  TitleLedger 'RIPPED' @{ Bytes = ($mkvs | Measure-Object Length -Sum).Sum; Src = $iso; Dest = $out; Note = "titles=$($mkvs.Count) unrecovered=$bad" }
  Write-LedgerRun -Event 'RUN_END' -Note "ripped titles=$($mkvs.Count) unrecovered=$bad"
  Log "done. Pick the main feature by duration before queueing it for transcode.ps1. Delete $iso once the transcode has landed."
}
catch {
  # Anything not routed through Abort. A -Detach run discards stdout/stderr, so
  # without this an unexpected error would leave no trace in the log and a
  # RUN_START with no matching end in the ledger.
  Abort "unexpected error: $($_.Exception.Message) (at line $($_.InvocationInfo.ScriptLineNumber))"
}
finally {
  $lock.Dispose()
}
