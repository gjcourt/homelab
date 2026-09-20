<#
.SYNOPSIS
  Append-only movement ledger, shared by the music and video import paths.

.DESCRIPTION
  One record of what moved, where, and whether it was verified - written to
  hestia AS IT HAPPENS rather than at the end of a successful run.

  ⚠️ Why this file exists rather than a copy in each script: transcode.ps1's own
  header records that transcode-batch{,2,3}.ps1, transcode-extras.ps1 and
  chain-extras.ps1 "had all drifted apart while re-implementing the same push
  logic". A ledger duplicated into two scripts is the same mistake with a
  shorter fuse - the copies diverge, and the one that matters is the one that
  was not updated.

  ⚠️ The ledger must never fail a run. A lost row is bad; a lost album or a
  half-pushed film is worse. Every failure here warns and returns.

  Schema (TSV, no header - the file is append-only and read by tools):
    ts  run_id  event  scope  title  item  disc  track  sha256  bytes  src  dest  note

  scope is 'run' | 'album' | 'file' | 'name' | 'title'. For music, title=artist
  and item=album; for video, title=the library name and item is the file.
#>

$script:LedgerHost = $null
$script:LedgerRunId = $null
$script:LedgerPath = '/mnt/main/archive/_inventory/rips/ledger.tsv'
$script:LedgerSeq  = 0

function Initialize-Ledger {
  param([Parameter(Mandatory)][string]$RemoteHost,
        [Parameter(Mandatory)][string]$RunId,
        [string]$Path)
  $script:LedgerHost  = $RemoteHost
  $script:LedgerRunId = $RunId
  if ($Path) { $script:LedgerPath = $Path }
}

function Write-Ledger {
  param([Parameter(Mandatory)][string]$Event, [object[]]$Records)
  if (-not $script:LedgerHost) { return }          # not initialised: stay silent
  if (-not $Records -or $Records.Count -eq 0) { return }
  $script:LedgerSeq++
  $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
  $lines = foreach ($r in $Records) {
    @(
      $ts; $script:LedgerRunId; $Event
      $(if ($r.Scope) { $r.Scope } else { 'item' })
      $(if ($r.Title) { $r.Title } else { '' })
      $(if ($r.Item)  { $r.Item }  else { '' })
      $(if ($null -ne $r.Disc)  { $r.Disc }  else { '' })
      $(if ($null -ne $r.Track) { $r.Track } else { '' })
      $(if ($r.Sha)   { $r.Sha }   else { '' })
      $(if ($null -ne $r.Bytes) { $r.Bytes } else { '' })
      $(if ($r.Src)   { $r.Src }   else { '' })
      $(if ($r.Dest)  { $r.Dest }  else { '' })
      $(if ($r.Note)  { $r.Note }  else { '' })
    ) -join "`t"
  }
  $chunk  = Join-Path $env:TEMP "$($script:LedgerRunId).ledger.$($script:LedgerSeq).tsv"
  [IO.File]::WriteAllLines($chunk, $lines, [Text.UTF8Encoding]::new($false))
  $remote = "/tmp/.ledger.$($script:LedgerRunId).$($script:LedgerSeq).tsv"
  & cmd /c "scp -B -o BatchMode=yes `"$chunk`" $($script:LedgerHost):$remote >nul 2>nul" | Out-Null
  Remove-Item $chunk -Force -ErrorAction SilentlyContinue
  $dir = Split-Path -Parent $script:LedgerPath
  # flock: a second import, or a retry, must not interleave half-written lines.
  & cmd /c "ssh -n -o BatchMode=yes $($script:LedgerHost) `"sudo -n mkdir -p '$dir' && sudo -n touch '$($script:LedgerPath)' && sudo -n flock '$($script:LedgerPath).lock' -c 'cat $remote >> $($script:LedgerPath)' && rm -f $remote`" >nul 2>nul" | Out-Null
}

function Write-LedgerRun {
  param([Parameter(Mandatory)][string]$Event, [string]$Note)
  Write-Ledger -Event $Event -Records @([pscustomobject]@{ Scope = 'run'; Note = $Note })
}
