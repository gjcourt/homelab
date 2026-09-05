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
  [switch]$DryRun,
  [switch]$AllowIncomplete,
  [string]$AuditDir = (Join-Path $env:LOCALAPPDATA 'music-rip-audit')
)

$ErrorActionPreference = 'Continue'
$FFDIR   = 'C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin'
$FP      = Join-Path $FFDIR 'ffprobe.exe'
$HST     = 'truenas_admin@10.42.2.10'
$LIB     = '/mnt/main/family/media/music'
$SCRATCH = '/mnt/main/downloads/music-import'
$AUDIT_REMOTE = '/mnt/main/archive/_inventory/rips'
$RUN_ID  = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
New-Item -ItemType Directory -Force -Path $AuditDir | Out-Null
$AUDIT     = Join-Path $AuditDir "$RUN_ID.files.tsv"
$AUDIT_SUM = Join-Path $AuditDir "$RUN_ID.albums.tsv"

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
  $lines = & $FP -v quiet -show_entries 'format_tags=album_artist,artist,album,title,track,disc,TOTALDISCS,DISCTOTAL,TOTALTRACKS,TRACKTOTAL,MUSICBRAINZ_ALBUMID,BARCODE' -of default=noprint_wrappers=1 $f.FullName 2>$null
  $t = @{}
  foreach ($l in $lines) {
    $i = $l.IndexOf('=')
    if ($i -gt 0) { $t[$l.Substring(0,$i).Replace('TAG:','').ToLower()] = $l.Substring($i+1) }
  }
  $aa=$t['album_artist']; $ar=$t['artist']; $al=$t['album']
  $artist = if ($aa) { $aa } else { $ar }
  if (-not $artist -or -not $al -or $artist -like '*Unknown Artist*') { $untagged += $f.Name; continue }
  # First number only: Picard may write track as "3/12" or disc as "1/2".
  function Num([string]$v) { if ($v -match '(\d+)') { [int]$Matches[1] } else { 0 } }
  $disc      = Num $t['disc']
  $disctotal = [Math]::Max((Num $t['totaldiscs']), (Num $t['disctotal']))
  $track     = Num $t['track']
  $tracktot  = [Math]::Max((Num $t['totaltracks']), (Num $t['tracktotal']))
  if ($disc -lt 1) { $disc = 1 }
  $items += [pscustomobject]@{
    File=$f; Artist=$artist.Trim(); Album=$al.Trim(); Title=$t['title']
    Disc=$disc; DiscTotal=$disctotal; Track=$track; TrackTotal=$tracktot
    Mbid=$t['musicbrainz_albumid']; Barcode=$t['barcode']
  }
}
if ($untagged) {
  Log "$($untagged.Count) UNTAGGED file(s) skipped - categorise these on Windows, do NOT stage them:"
  $untagged | Select-Object -First 10 | ForEach-Object { Log "    $_" }
}
if (-not $items) { Log 'nothing tagged to import'; exit 0 }

# ---- 1b. audit trail + completeness gate --------------------------------
# WHY THIS EXISTS: on 2026-08-29 a 2xCD rip (The Clash - London Calling) moved
# with only disc 2 present, the source rips on THIS BOX were deleted on the
# strength of a check reporting "untagged/suspect files: 0", and disc 1 was lost
# permanently. That check proved tags EXISTED, not that the album was WHOLE.
# The surviving files carried TOTALDISCS=2 the entire time - the evidence was
# inside the files that moved. This gate is purely local: no network, no
# MusicBrainz lookup, because Picard already wrote what we need.
Log '== audit trail =='
$rows = New-Object System.Collections.Generic.List[string]
$sha  = [Security.Cryptography.SHA256]::Create()
foreach ($it in $items) {
  $fs = [IO.File]::OpenRead($it.File.FullName)
  try { $hash = [BitConverter]::ToString($sha.ComputeHash($fs)).Replace('-','').ToLower() } finally { $fs.Dispose() }
  $dest = (LinuxName $it.Artist) + '/' + (LinuxName $it.Album) + '/' + (TruncName $it.File.Name)
  $rows.Add(($hash, $it.File.Length, $it.Artist, $it.Album, $it.Disc, $it.DiscTotal,
             $it.Track, $it.TrackTotal, $it.Title, $it.Mbid, $it.Barcode,
             $it.File.FullName, $dest) -join "`t")
}
[IO.File]::WriteAllLines($AUDIT, $rows, [Text.UTF8Encoding]::new($false))
Log "  $($rows.Count) file rows -> $AUDIT"

# Group by MUSICBRAINZ_ALBUMID where present: two discs of one release routinely
# carry DIFFERENT album strings, and grouping on the string splits a complete
# release into two half-albums - a gate that cries wolf is a gate that gets
# ignored.
Log '== COMPLETENESS =='
$groups = $items | Group-Object { if ($_.Mbid) { $_.Mbid } else { (Norm $_.Artist) + '|' + (Norm $_.Album) } }
$verdicts = @(); $nOk=0; $nBad=0; $nUnv=0
foreach ($grp in $groups) {
  $label = $grp.Group[0].Artist + ' :: ' + $grp.Group[0].Album
  $maxDt = ($grp.Group | Measure-Object DiscTotal -Maximum).Maximum
  $discs = $grp.Group | Group-Object Disc
  $verdict = 'OK'; $detail = ''
  if ($maxDt -gt 0 -and $discs.Count -lt $maxDt) {
    $have = $discs.Name | ForEach-Object { [int]$_ }
    $missing = (1..$maxDt | Where-Object { $_ -notin $have }) -join ','
    $verdict = 'INCOMPLETE'
    $detail  = "declared $maxDt discs, have $($discs.Count) (missing disc $missing)"
  }
  if ($verdict -eq 'OK') {
    foreach ($d in $discs) {
      $tt = ($d.Group | Measure-Object TrackTotal -Maximum).Maximum
      if ($tt -gt 0 -and $d.Count -lt $tt) {
        $verdict = 'INCOMPLETE'
        $detail = ($detail, "disc $($d.Name): $($d.Count)/$tt tracks" | Where-Object { $_ }) -join '; '
      }
    }
  }
  if ($verdict -eq 'OK' -and $maxDt -eq 0 -and (($grp.Group | Measure-Object TrackTotal -Maximum).Maximum -eq 0)) {
    $verdict = 'UNVERIFIED'; $detail = 'no TOTALDISCS/TOTALTRACKS tags - cannot prove completeness'
  }
  switch ($verdict) { 'OK' { $nOk++ } 'INCOMPLETE' { $nBad++ } 'UNVERIFIED' { $nUnv++ } }
  $verdicts += ($verdict, $label, $detail, $grp.Group[0].Mbid) -join "`t"
  switch ($verdict) {
    'OK'         { Log "  OK          $label" }
    'UNVERIFIED' { Log "  UNVERIFIED  $label  ($detail)" }
    'INCOMPLETE' { Log "  INCOMPLETE  $label  ($detail)" }
  }
}
[IO.File]::WriteAllLines($AUDIT_SUM, $verdicts, [Text.UTF8Encoding]::new($false))
Log ''
Log "  complete: $nOk   incomplete: $nBad   unverified: $nUnv"
Log "  audit trail: $AUDIT"
Log "               $AUDIT_SUM"
Log ''
if ($nBad -gt 0 -and -not $AllowIncomplete) {
  Log "REFUSING TO PROCEED: $nBad album(s) are provably incomplete."
  Log 'Re-rip the missing disc(s), or pass -AllowIncomplete if this is deliberate.'
  Log "DO NOT delete the source rips in $RipsDir until this reports 0 incomplete."
  exit 2
}

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

# ---- 7. verify the BYTES that landed, not just the count -----------------
# rsync checksums in flight, but --ignore-existing SKIPS files already present
# and nothing ever re-checks those. A truncated earlier import would persist
# forever. Hash the destination against the audit trail and prove it.
Log '== verifying destination checksums =='
$man = New-Object System.Collections.Generic.List[string]
foreach ($r in [IO.File]::ReadAllLines($AUDIT)) {
  $c = $r -split "`t"
  if ($c.Count -ge 13) { $man.Add($c[0] + '  ./' + $c[12]) }
}
$manLocal = Join-Path $env:TEMP "$RUN_ID.sha256"
[IO.File]::WriteAllLines($manLocal, $man, [Text.UTF8Encoding]::new($false))
scp -B -o BatchMode=yes $manLocal "$($HST):$SCRATCH.sha256" | Out-Null
$chk = ssh -n -o BatchMode=yes $HST "cd '$LIB' && sudo -n sha256sum -c --quiet '$SCRATCH.sha256' 2>&1; echo EXIT=`$?"
$failed = @($chk | Where-Object { $_ -match ': (FAILED|No such file)' })
if ($failed.Count -gt 0) {
  Log "VERIFY FAILED: $($failed.Count) file(s) do not match the audit trail:"
  $failed | Select-Object -First 10 | ForEach-Object { Log "    $_" }
  Log 'The library may contain a truncated or stale copy. Scratch left in place.'
  Log "DO NOT delete the source rips in $RipsDir."
  ssh -n -o BatchMode=yes $HST "rm -f '$SCRATCH.sha256'" | Out-Null
  exit 3
}
Log "  all $($man.Count) file(s) match their source hash on hestia"

# Audit must outlive the source rips: keep a copy off this box.
ssh -n -o BatchMode=yes $HST "sudo -n mkdir -p '$AUDIT_REMOTE' && sudo -n chown truenas_admin '$AUDIT_REMOTE'" | Out-Null
scp -B -o BatchMode=yes $AUDIT $AUDIT_SUM "$($HST):$AUDIT_REMOTE/" | Out-Null
Log "  audit copied to hestia:$AUDIT_REMOTE/"
ssh -n -o BatchMode=yes $HST "rm -f '$SCRATCH.sha256'" | Out-Null

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
