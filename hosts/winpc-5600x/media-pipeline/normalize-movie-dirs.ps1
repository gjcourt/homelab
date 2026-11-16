<#
.SYNOPSIS
  One-time fix: make every movie DIRECTORY group `apps` with group write.

.DESCRIPTION
  Before 2026-11-03 transcode.ps1 created title dirs with a bare `sudo mkdir -p`,
  so they came out root:truenas_admin 2755. Radarr runs as uid 568 (group
  `apps`) and needs group write; Jellyfin only reads, which is why nothing
  looked wrong.

  The target is a PREDICATE, not a list: every dir under movies/ that is not
  group `apps` or lacks group write. On 2026-11-03 that matched 73 (71
  root:truenas_admin 2755, including extras/ subdirs, and 2
  truenas_admin:truenas_admin 2755).

  Directories only: owner truenas_admin, group apps, mode 2775 (matching the 67
  title dirs that were already right). FILES ARE NOT TOUCHED.

  Default is list-only. -Apply changes them, then re-runs the predicate and
  fails if anything still matches.

.EXAMPLE
  .\normalize-movie-dirs.ps1           # list what would change
  .\normalize-movie-dirs.ps1 -Apply
#>
[CmdletBinding()]
param([switch]$Apply)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }
. (Join-Path $PSScriptRoot 'ledger.ps1')   # Invoke-SshRead: reads go through a file, never an ssh pipe

$HST    = 'truenas_admin@10.42.2.10'
$MOVIES = '/mnt/main/family/media/video/movies'
$FIND   = "sudo -n find '$MOVIES' -mindepth 1 -type d \( ! -group apps -o ! -perm -g=w \)"

function Log($m) { Write-Output ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $m) }

$list = @(Invoke-SshRead -RemoteHost $HST -Cmd "$FIND -printf '%u:%g %m %P\n'" | Where-Object { $_ })
Log "$($list.Count) movie dir(s) are not group apps with group write"
$list | ForEach-Object { Write-Output "    $_" }
if (-not $list) { exit 0 }
if (-not $Apply) { Log 'list only - re-run with -Apply to fix them'; exit 0 }

# -exec ... + keeps paths with spaces and quotes out of any shell quoting.
ssh -n -o BatchMode=yes $HST "$FIND -exec chown truenas_admin:apps {} + && $FIND -exec chmod 2775 {} +"
$rc = $LASTEXITCODE
$left = @(Invoke-SshRead -RemoteHost $HST -Cmd "$FIND -printf '%P\n'" | Where-Object { $_ })
if ($rc -ne 0 -or $left) { Log "FAIL: exit $rc, $($left.Count) dir(s) still match"; $left | ForEach-Object { Write-Output "    $_" }; exit 1 }
Log "fixed $($list.Count) dir(s); none left"
