<#
.SYNOPSIS
  The one entry point for loading rips into the hestia library.

.DESCRIPTION
  Every load goes through here. It does no media work itself: it checks the
  checkout, takes the lock, and runs import-music.ps1 (music) or
  build-queue.ps1 + transcode.ps1 (video).

  Why it exists: on 2026-11-02 two CD loads were hand-run with scp + rsync
  instead of import-music.ps1, and a plain `rsync -a` stamped 707/truenas_admin
  onto five existing artist folders. C:\media-work also holds dozens of one-off
  launch-/wait-/status- scripts, each re-improvising the detach. Neither should
  be necessary.

  Order of checks, each one a refusal:
    1. the checkout has uncommitted changes  (checked BEFORE pulling: a clear
       message instead of a failed pull)
    2. git pull --ff-only fails              (never run a stale or diverged copy)
    3. another load holds the lock, or an ffmpeg is already running

  -DryRun runs in the foreground and changes nothing in the library.
  A real run DETACHES via WMI, so it survives the SSH session ending
  (Start-Process does not), and logs to C:\media-work\load-<type>-<stamp>.log.

  THE LOCK (C:\media-work\load.lock) is held by the detached run, not by this
  launcher, and holds its PID. A lock whose PID is not a running PowerShell is
  stale - left by a crash or a kill - and is reported and cleared. A live one
  blocks the start.

.EXAMPLE
  .\load.ps1 status
  .\load.ps1 music -DryRun
  .\load.ps1 music
  .\load.ps1 video -Disc C:\Video\Looper -Name 'Looper (2012)' -DryRun
  .\load.ps1 video -Disc C:\Video\Looper -Name 'Looper (2012)' -Exclude '*Play_All*'
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)][ValidateSet('music', 'video', 'status')][string]$Type = 'status',
  [string]$Disc,
  [string]$Name,
  [string]$Main,
  [string[]]$Exclude = @(),
  [switch]$NoExtras,
  [switch]$DryRun,
  # Internal: set only on the detached run this script launches.
  [string]$Child,
  [string]$Queue
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }

$HERE = $PSScriptRoot
$REPO = (Resolve-Path (Join-Path $HERE '..\..\..')).Path
$WORK = 'C:\media-work'
$LOCK = Join-Path $WORK 'load.lock'
New-Item -ItemType Directory -Path $WORK -Force | Out-Null

function Log($m) { Write-Output ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $m) }

# ---- lock ---------------------------------------------------------------
function Read-Lock {
  if (-not (Test-Path -LiteralPath $LOCK)) { return $null }
  $raw = (Get-Content -LiteralPath $LOCK -Raw -ErrorAction SilentlyContinue)
  $f = "$raw".Trim() -split "`t"
  $lockPid = 0; [void][int]::TryParse($f[0], [ref]$lockPid)
  $p = if ($lockPid -gt 0) { Get-Process -Id $lockPid -ErrorAction SilentlyContinue } else { $null }
  # PID reuse: a recycled PID belonging to something else is not our run. The
  # name check alone passes any powershell.exe (an interactive SSH shell, say),
  # so also require the process to have started no later than the lock was
  # written - a recycled PID starts after it.
  $alive = $false
  if ($p -and $p.ProcessName -match '^(powershell|pwsh)$') {
    $alive = $true
    $st = [datetime]::MinValue
    if ([datetime]::TryParse("$($f[2])", [ref]$st)) {
      try { if ($p.StartTime -gt $st.AddSeconds(5)) { $alive = $false } } catch { }
    }
  }
  [pscustomobject]@{ Pid = $lockPid; Type = $f[1]; Started = $f[2]; Log = $f[3]; Alive = $alive }
}

function Assert-Free {
  $l = Read-Lock
  if ($l -and $l.Alive) {
    Log "REFUSE: a $($l.Type) load is running (PID $($l.Pid), since $($l.Started)). Log: $($l.Log)"
    exit 3
  }
  if ($l) {
    Log "stale lock: PID $($l.Pid) ($($l.Type), since $($l.Started)) is not running - clearing it. Its log: $($l.Log)"
    Remove-Item -LiteralPath $LOCK -Force
  }
  $ff = @(Get-Process ffmpeg -ErrorAction SilentlyContinue)
  if ($ff) { Log "REFUSE: ffmpeg is already running (PID $($ff.Id -join ',')), started outside load.ps1"; exit 3 }
}

# ---- detached child: hold the lock for the life of the run --------------
if ($Child) {
  $logPath = "$env:LOAD_LOG"
  try {
    # CreateNew fails if the file exists, so two children cannot both win.
    $fs = [IO.File]::Open($LOCK, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    $b = [Text.Encoding]::UTF8.GetBytes("$PID`t$Child`t$((Get-Date).ToString('s'))`t$logPath")
    $fs.Write($b, 0, $b.Length); $fs.Close()
  } catch { Log "REFUSE: lock already held ($LOCK)"; exit 3 }
  $rc = 1
  try {
    Log "== load $Child started (PID $PID) from $(git -C $REPO log -1 --format='%h %s') =="
    if ($Child -eq 'music') {
      & (Join-Path $HERE 'import-music.ps1')
    } else {
      & (Join-Path $HERE 'transcode.ps1') -Queue $Queue
    }
    $rc = $LASTEXITCODE
    Log "== load $Child finished, exit $rc =="
  } finally {
    $l = Read-Lock
    if ($l -and $l.Pid -eq $PID) { Remove-Item -LiteralPath $LOCK -Force }
  }
  exit $rc
}

# ---- status ---------------------------------------------------------------
if ($Type -eq 'status') {
  $l = Read-Lock
  if (-not $l)      { Log 'lock: free' }
  elseif ($l.Alive) { Log "lock: $($l.Type) load RUNNING, PID $($l.Pid), since $($l.Started)" }
  else              { Log "lock: STALE (PID $($l.Pid) gone) - the next load will clear it" }
  $ff = @(Get-Process ffmpeg -ErrorAction SilentlyContinue)
  Log ("ffmpeg: " + $(if ($ff) { "running, PID $($ff.Id -join ',')" } else { 'not running' }))
  foreach ($f in (Get-ChildItem $WORK -Filter 'load-*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 3)) {
    Write-Output ''
    Write-Output "--- $($f.Name)  ($($f.LastWriteTime))"
    Get-Content -LiteralPath $f.FullName -Tail 4 -Encoding UTF8 | ForEach-Object { Write-Output "    $_" }
  }
  exit 0
}

# ---- checkout: dirty check FIRST, then pull ------------------------------
$dirty = git -C $REPO status --porcelain
if ($dirty) {
  Log "REFUSE: the checkout at $REPO has local changes. Scripts are edited in the repo, merged, then pulled here:"
  $dirty | ForEach-Object { Write-Output "    $_" }
  exit 2
}
git -C $REPO pull --ff-only --quiet
if ($LASTEXITCODE -ne 0) { Log "REFUSE: git pull --ff-only failed in $REPO - not running a stale or diverged copy"; exit 2 }
Log "checkout: $(git -C $REPO log -1 --format='%h %s')"

Assert-Free

# ---- video: build the queue in the foreground so the pick is visible -----
$qf = $null
if ($Type -eq 'video') {
  if (-not $Disc -or -not $Name) { Log "REFUSE: video needs -Disc <folder> and -Name 'Title (Year)'"; exit 1 }
  $bq = @{ Disc = $Disc; Name = $Name }
  if ($Main)     { $bq.Main = $Main }
  if ($Exclude)  { $bq.Exclude = $Exclude }
  if ($NoExtras) { $bq.NoExtras = $true }
  $bqOut = @(& (Join-Path $HERE 'build-queue.ps1') @bq)
  if ($LASTEXITCODE -ne 0) { $bqOut | ForEach-Object { Write-Output $_ }; exit 1 }
  $bqOut[0..($bqOut.Count - 2)] | ForEach-Object { Write-Output $_ }
  $qf = $bqOut[-1]
}

if ($DryRun) {
  if ($Type -eq 'music') { & (Join-Path $HERE 'import-music.ps1') -DryRun }
  else                   { & (Join-Path $HERE 'transcode.ps1') -Queue $qf -DryRun }
  exit $LASTEXITCODE
}

# ---- real run: detach -----------------------------------------------------
$stamp   = (Get-Date).ToString('yyyyMMdd-HHmmss')
$logPath = Join-Path $WORK "load-$Type-$stamp.log"
$childArgs = "-Child $Type"
if ($qf) { $childArgs += " -Queue `"$qf`"" }
$cmd = "cmd.exe /c `"set LOAD_LOG=$logPath&& powershell -NoProfile -ExecutionPolicy Bypass -File `"$HERE\load.ps1`" $childArgs > `"$logPath`" 2>&1`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $REPO }
if ($r.ReturnValue -ne 0) { Log "FAIL: could not start the detached run (Win32_Process.Create returned $($r.ReturnValue))"; exit 1 }

# Confirm it actually took the lock rather than reporting success blind. The
# lock must be OURS (same log path): if two launchers race past Assert-Free,
# the losing child refuses, and its launcher must not report the winner's run.
$l = $null
for ($i = 0; $i -lt 20; $i++) { Start-Sleep -Milliseconds 500; $l = Read-Lock; if ($l -and $l.Alive -and $l.Log -eq $logPath) { break } }
if ($l -and $l.Alive -and $l.Log -eq $logPath) {
  Log "STARTED $Type load, PID $($l.Pid). Log: $logPath"
  Log 'Check on it with: .\load.ps1 status'
} else {
  if ($l -and $l.Alive) { Log "WARNING: another load (PID $($l.Pid), log $($l.Log)) holds the lock; this one did not start. Read: $logPath" }
  else                  { Log "WARNING: started, but no live lock after 10 s. Read the log: $logPath" }
  exit 1
}
