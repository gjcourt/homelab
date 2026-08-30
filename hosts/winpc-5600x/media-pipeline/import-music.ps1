<#
.SYNOPSIS
  Import CD rips from this box into the hestia music library that Navidrome serves.

.DESCRIPTION
  EAC + Picard write well-tagged .flac FLAT into C:\Rips - `NN Title.flac`, not in
  album folders, with several albums interleaved. So the Artist/Album grouping must
  come from TAGS, never from filenames or directory structure.

  Pipeline: read tags -> group -> dedup against the live library -> organise ->
  scp to a hestia scratch dir -> rsync into the library with correct ownership.

  Every guard below exists because the manual version of this pipeline hit the
  problem it guards against, on 2026-08-29:

    * 255-BYTE FILENAMES. Linux caps a filename at 255 BYTES, not characters. A
      Marvin Gaye medley track listing the whole medley came to 259 bytes because
      each curly apostrophe costs 3 in UTF-8; scp failed `Bad message` on that one
      file and silently left the album incomplete. Names are truncated on a
      character boundary before transfer. Navidrome is TAG-DRIVEN, so shortening a
      filename loses nothing.
    * WINDOWS TRAILING DOT. Windows forbids a directory ending in `.`, so
      `Harry Connick, Jr.` becomes `Harry Connick, Jr` here. Linux permits it, so
      the final name is restored on hestia via a rename manifest.
    * COLON CONVENTION. The library uses space-dash-space (`Jazz Steps Out - Rare
      Masters`). A naive `:`->`-` gives `True Love- A Celebration`, a second
      convention. Normalised to ` - `.
    * UNICODE NORMALISATION. An existing artist dir silently becomes a SECOND
      folder if the incoming `e-acute` differs (NFC vs NFD). The rsync dry-run is
      inspected: `.d..t` on an existing artist means merge, `cd+++` means it is
      being created, and that is the bug.
    * DEDUP ON ARTIST **AND** ALBUM. "The Montreux Years" is a series - matching on
      album title alone would have skipped a genuinely new Monty Alexander album
      because Nina Simone's was already present.

.PARAMETER DryRun
  Report what would be imported and stop. Nothing is copied or transferred.

.NOTES
  Does NOT delete anything from C:\Rips. Clearing source rips is operator-only and
  deliberately manual - verify against the library first.
  The pipeline has NO dependency on Kubernetes. It ends when rsync lands the files
  on hestia, which is the source of truth. Navidrome is a downstream CONSUMER: it
  mounts the library read-only over NFS and rescans on ND_SCANSCHEDULE=1h, so it
  updates itself. The rollout-restart command is printed only as a way to see the
  change immediately, and this box has no kubeconfig anyway.
#>
[CmdletBinding()]
param(
  [string]$RipsDir  = 'C:\Rips',
  [string]$Staging  = 'C:\RipsOrg',
  [int]$MaxNameBytes = 200,
  [switch]$DryRun
)

$ErrorActionPreference = 'Continue'
$FFDIR   = 'C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin'
$FP      = Join-Path $FFDIR 'ffprobe.exe'
$HST     = 'truenas_admin@10.42.2.10'
$LIB     = '/mnt/main/family/media/music'
$SCRATCH = '/mnt/main/downloads/music-import'

function Log($m) { Write-Output ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $m) }
function Norm([string]$s) {
  $t = $s.Normalize([Text.NormalizationForm]::FormKD) -replace "[\u2010\u2011]","-" -replace "[\u2018\u2019]","'"
  ($t.ToCharArray() | Where-Object { [Globalization.CharUnicodeInfo]::GetUnicodeCategory($_) -ne 'NonSpacingMark' }) -join '' `
    -replace '[^a-zA-Z0-9]','' | ForEach-Object { $_.ToLower() }
}
# Final name as it should appear in the library (Linux).
function LinuxName([string]$s) {
  ($s -replace '\s*:\s*',' - ' -replace '[/\\]','-').Trim()
}
# Same name made legal on Windows for staging: no trailing dot.
function WinName([string]$s) {
  $t = $s
  foreach ($c in @('*','?','"','<','>','|',':')) { $t = $t.Replace($c,'-') }
  $t.Trim().TrimEnd('.')
}
function TruncName([string]$fileName) {
  $ext  = [IO.Path]::GetExtension($fileName)
  $stem = [IO.Path]::GetFileNameWithoutExtension($fileName)
  if ([Text.Encoding]::UTF8.GetByteCount($fileName) -le $MaxNameBytes) { return $fileName }
  while ([Text.Encoding]::UTF8.GetByteCount($stem + $ext) -gt ($MaxNameBytes - 10)) {
    $stem = $stem.Substring(0, $stem.Length - 1)
  }
  return ($stem.TrimEnd() + $ext)
}

if (-not (Test-Path $FP))      { Log "ABORT: ffprobe not found at $FP"; exit 1 }
if (-not (Test-Path $RipsDir)) { Log "ABORT: rips dir not found: $RipsDir"; exit 1 }

# ---- 1. read tags -------------------------------------------------------
$flacs = Get-ChildItem $RipsDir -File -Recurse -Filter *.flac -ErrorAction SilentlyContinue |
         Where-Object { $_.FullName -notmatch '\\_GV\\' }   # _GV = always ignore
if (-not $flacs) { Log "nothing to import in $RipsDir"; exit 0 }
Log "== reading tags from $($flacs.Count) files =="

$items = @(); $untagged = @()
foreach ($f in $flacs) {
  $lines = & $FP -v quiet -show_entries 'format_tags=album_artist,artist,album' -of default=noprint_wrappers=1 $f.FullName 2>$null
  $aa=''; $ar=''; $al=''
  foreach ($l in $lines) {
    $i = $l.IndexOf('=')
    if ($i -gt 0) {
      $k=$l.Substring(0,$i); $v=$l.Substring($i+1)
      if ($k -eq 'TAG:album_artist') { $aa=$v } elseif ($k -eq 'TAG:ARTIST') { $ar=$v } elseif ($k -eq 'TAG:ALBUM') { $al=$v }
    }
  }
  $artist = if ($aa) { $aa } else { $ar }
  if (-not $artist -or -not $al -or $artist -like '*Unknown Artist*') { $untagged += $f.Name; continue }
  $items += [pscustomobject]@{ File=$f; Artist=$artist.Trim(); Album=$al.Trim() }
}
if ($untagged) {
  Log "$($untagged.Count) UNTAGGED file(s) skipped - categorise these on Windows, do NOT stage them:"
  $untagged | Select-Object -First 10 | ForEach-Object { Log "    $_" }
}
if (-not $items) { Log 'nothing tagged to import'; exit 0 }

# ---- 2. dedup against the live library ----------------------------------
Log '== querying the live library =='
$existing = ssh -n -o BatchMode=yes $HST "sudo -n find '$LIB' -mindepth 2 -maxdepth 2 -type d -printf '%P\n'"
if ($LASTEXITCODE -ne 0) { Log 'ABORT: could not list the library over ssh'; exit 1 }
$have = @{}
foreach ($l in $existing) {
  if ($l -match '/') { $a,$b = $l -split '/',2; $have[(Norm $a) + '|' + (Norm $b)] = $l }
}
Log "library has $($have.Count) albums"

$albums = $items | Group-Object Artist,Album
$toImport = @()
foreach ($g in $albums) {
  $art = $g.Group[0].Artist; $alb = $g.Group[0].Album
  $key = (Norm $art) + '|' + (Norm $alb)
  if ($have.ContainsKey($key)) { Log ("  SKIP already in library: $art / $alb  -> " + $have[$key]) ; continue }
  Log "  NEW  $($g.Count.ToString().PadLeft(3))  $art / $alb"
  $toImport += $g
}
if (-not $toImport) { Log '== nothing new to import =='; exit 0 }
Log "== $($toImport.Count) new album(s), $(($toImport | ForEach-Object { $_.Count } | Measure-Object -Sum).Sum) tracks =="
if ($DryRun) { Log 'DRY RUN - stopping here'; exit 0 }

# ---- 3. stage on Windows, recording any name that must be fixed on Linux --
if (Test-Path $Staging) { Remove-Item $Staging -Recurse -Force }
New-Item -ItemType Directory -Path $Staging | Out-Null
$renames = @(); $truncated = 0
foreach ($g in $toImport) {
  $art = $g.Group[0].Artist; $alb = $g.Group[0].Album
  $artL = LinuxName $art; $albL = LinuxName $alb
  $artW = WinName $artL;  $albW = WinName $albL
  if ($artW -ne $artL) { $renames += [pscustomobject]@{ From=$artW; To=$artL; Depth=1 } }
  if ($albW -ne $albL) { $renames += [pscustomobject]@{ From="$artW/$albW"; To="$artW/$albL"; Depth=2 } }
  $dir = Join-Path $Staging (Join-Path $artW $albW)
  New-Item -ItemType Directory -Path $dir -Force | Out-Null
  foreach ($it in $g.Group) {
    $n = TruncName $it.File.Name
    if ($n -ne $it.File.Name) { $truncated++; Log "  truncated (>${MaxNameBytes}B): $($it.File.Name.Substring(0,[Math]::Min(50,$it.File.Name.Length)))..." }
    Copy-Item $it.File.FullName (Join-Path $dir $n) -Force
  }
}
$srcCount = (Get-ChildItem $Staging -Recurse -File -Filter *.flac).Count
$srcBytes = (Get-ChildItem $Staging -Recurse -File | Measure-Object Length -Sum).Sum
Log "staged $srcCount files, $srcBytes bytes$(if ($truncated) { " ($truncated filename(s) truncated)" })"

# ---- 4. transfer + verify ------------------------------------------------
ssh -n -o BatchMode=yes $HST "rm -rf '$SCRATCH' && mkdir -p '$SCRATCH'" | Out-Null
Log '== scp -> hestia scratch =='
scp -B -r -o BatchMode=yes $Staging "$($HST):$SCRATCH/" | Out-Null
if ($LASTEXITCODE -ne 0) { Log 'FAIL scp - staging left in place for inspection'; exit 1 }
$leaf = Split-Path $Staging -Leaf
$remCount = [int](ssh -n -o BatchMode=yes $HST "find '$SCRATCH/$leaf' -type f -name '*.flac' | wc -l")
$remBytes = [long](ssh -n -o BatchMode=yes $HST "du -sb '$SCRATCH/$leaf' | cut -f1")
if ($remCount -ne $srcCount) { Log "FAIL transfer: $remCount of $srcCount files arrived - NOT importing"; exit 1 }
Log "verified $remCount files on hestia (bytes local=$srcBytes remote=$remBytes)"

# ---- 5. restore names Windows could not represent ------------------------
foreach ($r in ($renames | Sort-Object Depth)) {
  ssh -n -o BatchMode=yes $HST "cd '$SCRATCH/$leaf' && [ -e '$($r.From)' ] && mv '$($r.From)' '$($r.To)'" | Out-Null
  Log "  restored name: $($r.From)  ->  $($r.To)"
}

# ---- 6. Unicode-duplicate guard, then import -----------------------------
Log '== rsync dry-run (checking for duplicate artist folders) =='
$dry = ssh -n -o BatchMode=yes $HST "sudo -n rsync -a --ignore-existing --chown=george:users --chmod=D755,F644 --itemize-changes --dry-run '$SCRATCH/$leaf/' '$LIB/'"
$created = @($dry | Where-Object { $_ -match '^cd\+{9}\s+[^/]+/$' })
foreach ($c in $created) {
  $name = ($c -split '\s+',2)[1].TrimEnd('/')
  $k = Norm $name
  if ($have.Keys | Where-Object { $_.StartsWith($k + '|') }) {
    Log "ABORT: '$name' would be CREATED but a matching artist already exists - Unicode normalisation mismatch, would make a duplicate folder"
    exit 1
  }
}
Log "  $(@($dry | Where-Object { $_ -match '^>f' }).Count) files to write, $($created.Count) new artist folder(s), no duplicates"

Log '== rsync into the library =='
$out = ssh -n -o BatchMode=yes $HST "sudo -n rsync -a --ignore-existing --chown=george:users --chmod=D755,F644 --stats '$SCRATCH/$leaf/' '$LIB/'"
if ($LASTEXITCODE -ne 0) { Log 'FAIL rsync - scratch left in place'; exit 1 }
$out | Where-Object { $_ -match 'Number of regular files transferred|Total transferred file size' } | ForEach-Object { Log "  $_" }

$bad = ssh -n -o BatchMode=yes $HST "sudo -n find '$LIB' \( ! -user george -o ! -group users \) | head -3"
if ($bad) { Log "WARNING: files with unexpected ownership:"; $bad | ForEach-Object { Log "    $_" } }

ssh -n -o BatchMode=yes $HST "rm -rf '$SCRATCH'" | Out-Null
Remove-Item $Staging -Recurse -Force
Log '== staging cleaned up (source rips in ' + $RipsDir + ' left untouched) =='

Log ''
Log '== IMPORT COMPLETE. The library on hestia is the source of truth and is now =='
Log '== updated. Nothing further is required.                                    =='
Log ''
Log 'Navidrome mounts the library READ-ONLY over NFS and rescans on'
Log 'ND_SCANSCHEDULE=1h, so it will pick these up on its own. To see them'
Log 'immediately, from a machine with a kubeconfig (not this one):'
Log '  kubectl -n navidrome-prod rollout restart deployment/navidrome'
Log ''
Log 'Unrelated housekeeping, when convenient: refresh'
Log '~/src/music-library/owned_albums.txt on the Mac so the SFPL borrow queue'
Log 'stops re-borrowing what you now own. UNION it, never overwrite - folder'
Log 'names are sanitised while the list carries real punctuation, so'
Log 'regenerating from the filesystem silently drops entries.'
Log ''
Log 'Imported albums:'
foreach ($g in $toImport) { Log ("  " + (LinuxName $g.Group[0].Artist) + "/" + (LinuxName $g.Group[0].Album)) }
