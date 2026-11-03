<#
.SYNOPSIS
  Build a transcode.ps1 queue from one MakeMKV disc folder.

.DESCRIPTION
  Replaces writing queue TSVs by hand. Every *.mkv in -Disc is probed:

    - the MAIN FEATURE (the longest title, or -Main) goes to movies/<Name>/<Name>
    - every other title goes to movies/<Name>/extras/Extra tNN (M min)

  That is the layout of every 2026-11-01 queue (War for the Planet of the Apes,
  Back to the Future). tNN is MakeMKV's title number from the file name, so an
  extra keeps the same name if the disc is re-ripped.

  ⚠️ "Longest = feature" is a heuristic. It is wrong on a disc that carries an
  extended cut, several versions of the film, or a "Play All" reel longer than
  the film. ALWAYS read the printed queue (load.ps1 -DryRun) before a real run,
  and use -Main / -Exclude / -NoExtras to correct it.

  The queue is written as UTF-8 without a BOM (transcode.ps1 reads it that way)
  to C:\media-work\queue-<slug>.tsv, and printed.

.PARAMETER Name
  Library name, "Title (Year)". Required: it is a human decision, not something
  derived from a disc label like WAR_GAMES_25AE. No apostrophes, colons or other
  characters transcode.ps1 refuses (write "Mission - Impossible").

.EXAMPLE
  .\build-queue.ps1 -Disc C:\Video\Looper -Name 'Looper (2012)'
  .\build-queue.ps1 -Disc C:\Video\Looper -Name 'Looper (2012)' -Exclude '*Play_All*','*SEG_*'
  .\build-queue.ps1 -Disc C:\Video\Alien -Name 'Alien (1979)' -Main 'C:\Video\Alien\title_t03.mkv' -NoExtras
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Disc,
  [Parameter(Mandatory = $true)][string]$Name,
  [string]$Main,                 # explicit main-feature file; default = longest title
  [string[]]$Exclude = @(),      # wildcards on the file name, e.g. '*Play_All*'
  [switch]$NoExtras,             # queue the main feature only
  [int]$MinSeconds = 60,         # titles shorter than this are skipped (menus, logos)
  [string]$OutDir = 'C:\media-work'
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }
$FP = 'C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe'

function Fail($m) { Write-Output "ABORT: $m"; exit 1 }

# Same rules transcode.ps1 enforces, checked here so a bad name fails before
# anything is written rather than as a SKIP line hours later.
if ($Name -match '[\\/:*?"<>|'']') { Fail "Name has a character transcode.ps1 refuses: $Name" }
if ($Name -notmatch '^\S.* \((19|20)\d{2}\)$') { Fail "Name must be 'Title (Year)': $Name" }
if (-not (Test-Path -LiteralPath $Disc -PathType Container)) { Fail "disc folder not found: $Disc" }
if (-not (Test-Path $FP)) { Fail "ffprobe not found at $FP" }

$files = @(Get-ChildItem -LiteralPath $Disc -File -Filter *.mkv | Sort-Object Name)
if (-not $files) { Fail "no .mkv files in $Disc" }

$titles = @()
foreach ($f in $files) {
  $excluded = $false
  foreach ($pat in $Exclude) { if ($f.Name -like $pat) { $excluded = $true } }
  # Native call under a local 'Continue': in Windows PowerShell 5.1 a redirected
  # stderr line becomes an ErrorRecord, and with the script-wide 'Stop' that is
  # a terminating error instead of an UNREADABLE row.
  $d = & { $ErrorActionPreference = 'Continue'; & $FP -v quiet -show_entries format=duration -of default=nw=1:nk=1 $f.FullName 2>$null }
  $sec = 0.0; if ("$d".Trim() -match '^[\d.]+$') { $sec = [double]"$d".Trim() }
  $tn = if ($f.BaseName -match '_t(\d+)$') { [int]$Matches[1] } else { $null }
  $titles += [pscustomobject]@{ File = $f; Sec = $sec; T = $tn; Excluded = $excluded }
}

$unreadable = @($titles | Where-Object { $_.Sec -le 0 })
foreach ($u in $unreadable) { Write-Output "  UNREADABLE  $($u.File.Name) (skipped)" }

if ($Main) {
  $mainPath = Resolve-Path -LiteralPath $Main -ErrorAction SilentlyContinue
  if (-not $mainPath) { Fail "-Main not found: $Main" }
  $mainT = $titles | Where-Object { $_.File.FullName -eq $mainPath.Path }
  if (-not $mainT) { Fail "-Main is not a file in $Disc : $Main" }
  $how = 'given by -Main'
} else {
  $mainT = $titles | Where-Object { -not $_.Excluded -and $_.Sec -gt 0 } | Sort-Object Sec -Descending | Select-Object -First 1
  if (-not $mainT) { Fail 'no readable, non-excluded title to use as the main feature' }
  $how = 'longest title - CHECK THIS'
}

$rows = @()
$rows += "$($mainT.File.FullName)`t$Name`tmovies/$Name"
$out = @("  MAIN        $([math]::Round($mainT.Sec/60,1)) min  $($mainT.File.Name)   ($how)")

foreach ($t in ($titles | Sort-Object { if ($null -ne $_.T) { $_.T } else { 9999 } }, { $_.File.Name })) {
  if ($t -eq $mainT -or $t.Sec -le 0) { continue }
  $label = $t.File.Name
  if ($t.Excluded) { $out += "  EXCLUDED    $label"; continue }
  if ($NoExtras)   { $out += "  NO-EXTRAS   $label"; continue }
  if ($t.Sec -lt $MinSeconds) { $out += "  TOO SHORT   $([math]::Round($t.Sec))s  $label"; continue }
  $min = [math]::Max(1, [math]::Round($t.Sec / 60, [MidpointRounding]::AwayFromZero))
  $tag = if ($null -ne $t.T) { 't{0:D2}' -f $t.T } else { $t.File.BaseName -replace '[^A-Za-z0-9]', '' }
  $nm  = "Extra $tag ($min min)"
  $rows += "$($t.File.FullName)`t$nm`tmovies/$Name/extras"
  $out += "  EXTRA       $([math]::Round($t.Sec/60,1)) min  $label  ->  $nm"
}

# Two extras with the same tag would overwrite each other in the library.
$dupNames = @($rows | ForEach-Object { ($_ -split "`t")[1] } | Group-Object | Where-Object Count -gt 1)
if ($dupNames) { Fail "two titles map to the same library name: $($dupNames.Name -join ', ')" }

$slug = ($Name.ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$qf = Join-Path $OutDir "queue-$slug.tsv"
[IO.File]::WriteAllLines($qf, [string[]]$rows, [Text.UTF8Encoding]::new($false))

Write-Output "== QUEUE for $Name : $($rows.Count) title(s) from $Disc =="
$out | ForEach-Object { Write-Output $_ }
Write-Output "== written: $qf =="
Write-Output $qf
# Explicit: load.ps1 gates on $LASTEXITCODE, which would otherwise still hold
# the last ffprobe's exit code.
exit 0
