<#
.SYNOPSIS
  Prove a library copy exists, and is intact, before anything local is deleted.

.DESCRIPTION
  The import and transcode paths write a ledger row when they move something.
  That covers material moved SINCE the ledger existed. It says nothing about the
  3,629 files already in the music library - including the 147 landed by hand on
  2026-09-19 - so "is it safe to clear C:\Rips?" was still an act of faith.

  This closes that. It answers one question per local file:

      is there a file in the library holding this file's content?

  ⚠️ Matching is by CONTENT, not by path. The obvious implementation re-derives
  the library path from the tags (artist/album/disc/track, TruncName, AsciiSafe)
  and looks for it - which means a delete decision rides on the same name-
  mangling logic that produced three mojibake folders in one run. A hash match
  needs none of it: if the bytes are in the library, the local copy is redundant
  no matter what either side calls it.

  Two modes, because the two paths have different proof standards:

    Music  the library holds a COPY of the rip, so identity of content is the
           proof. Needs a hash index of the whole library. sha256 of the file
           first; failing that, for FLAC, the STREAMINFO audio checksum - the
           MD5 of the UNENCODED audio, which tag edits do not change. Eleven
           tracks on 2026-09-20 were 52 bytes smaller than their library copies
           (one metadata block) with identical audio; on the file hash alone the
           verifier would refuse to bless anything retagged since import, and
           that set only grows. The two are recorded as DIFFERENT verdicts, so
           the ledger never claims a byte-identical copy it does not have.
           -RequireExactBytes disables the fallback.

    Video  the library holds a TRANSCODE of the rip - a different file, by
           design, so no hash of the source can ever match. The proof is the
           ledger's own LANDED row (written only after hestia's sha256 matched)
           plus a re-check that the landed file is STILL there and STILL hashes
           to the recorded value. Only those files are hashed; indexing 1.6 TB
           to check a handful of films would be absurd.

  Read-only by default. -Delete removes nothing that is not proven, and the unit
  of deletion is the ALBUM: one unproven track keeps the whole album.

  ⚠️ The album comes from the TAGS, not the folder. C:\Rips is flat - Picard
  writes several albums interleaved into it - so a folder is not an album, and
  grouping by directory blocks every rip on the box the moment one is unproven.
  The album gate itself is not negotiable: on 2026-08-29 a 2xCD rip moved with
  only disc 2 present and the source was deleted anyway. Disc 1 was missing from
  the library until it was restored from C:\Rips on 2026-09-25.
  Per file, "the bytes are in the library" was true of everything that landed -
  which is exactly how deleting only the proven files destroys an album.

.EXAMPLE
  # report only - transfers nothing, deletes nothing
  .\verify-before-delete.ps1 -Mode Music

.EXAMPLE
  # delete the source rips that are proven redundant
  .\verify-before-delete.ps1 -Mode Music -Delete
#>
[CmdletBinding()]
param(
  [ValidateSet('Music','Video')][string]$Mode = 'Music',
  [string]$SourceDir,
  [switch]$Delete,
  [switch]$Reindex,
  [switch]$RequireExactBytes,
  [int]$IndexMaxAgeHours = 24,
  [int]$Jobs = 16,
  [string]$AuditDir = (Join-Path $env:LOCALAPPDATA 'music-rip-audit')
)

$ErrorActionPreference = 'Continue'

# ⚠️ Same root cause that mangled every non-ASCII music tag and broke dedup
# (see import-music.ps1 for the full measurement): PowerShell decodes native
# command output in the OEM codepage, not UTF-8. Here it would corrupt library
# paths read back from hestia. Fail closed - this script authorises DELETES.
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }
$OutputEncoding = [Text.UTF8Encoding]::new($false)
if ([Console]::OutputEncoding.CodePage -ne 65001) {
  Write-Output "ABORT: console encoding is CP$([Console]::OutputEncoding.CodePage), not UTF-8 (65001)."
  Write-Output "Library paths would be mis-decoded. Refusing to authorise deletions."
  exit 5
}

$FFDIR  = 'C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin'
$FP     = Join-Path $FFDIR 'ffprobe.exe'
$HST    = 'truenas_admin@10.42.2.10'
$HBASE  = '/mnt/main/family'
$INV    = '/mnt/main/archive/_inventory/rips'
$RUN_ID = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')

if ($Mode -eq 'Music') {
  $LIBROOT = '/mnt/main/family/media/music'
  $EXTS    = @('.flac','.mp3','.m4a','.ogg','.opus','.wav','.aiff','.aif')
  if (-not $SourceDir) { $SourceDir = 'C:\Rips' }
} else {
  # Video dest paths in the ledger are relative to HBASE, not to the video dir.
  $LIBROOT = $HBASE
  $EXTS    = @('.mkv','.mp4','.m4v','.avi','.mpg','.mpeg','.ts')
}

New-Item -ItemType Directory -Force -Path $AuditDir | Out-Null
$AUDIT = Join-Path $AuditDir "$RUN_ID.verify.tsv"

function Log($m) { Write-Output ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $m) }

. (Join-Path $PSScriptRoot 'ledger.ps1')
Initialize-Ledger -RemoteHost $HST -RunId $RUN_ID -Path "$INV/ledger.tsv"
function SshRead([string]$cmd) { Invoke-SshRead -RemoteHost $HST -Cmd $cmd }
function VLedger([string]$event, [object[]]$records) {
  if (-not $records -or $records.Count -eq 0) { return }
  Write-Ledger -Event $event -Records $records
  Log "  ledger: $event x$($records.Count)"
}
function VLedgerRun([string]$event, [string]$note) { Write-LedgerRun -Event $event -Note $note; Log "  ledger: $event" }

# Push a local file to hestia. Contents are bytes in a file, so unlike scp'd
# FILENAMES they are not codepage-converted - this is the same manifest trick
# import-music.ps1 uses to carry true names across the hop.
function Push([string]$local, [string]$remote) {
  & cmd /c "scp -B -o BatchMode=yes `"$local`" $($HST):$remote >nul 2>nul" | Out-Null
}
function Fetch([string]$remote, [string]$local) {
  & cmd /c "scp -B -o BatchMode=yes $($HST):$remote `"$local`" >nul 2>nul" | Out-Null
  if (Test-Path $local) { return @([IO.File]::ReadAllLines($local, [Text.UTF8Encoding]::new($false))) }
  return @()
}
function Sha([string]$path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() }
# First number only: Picard may write track as "3/12" or disc as "1/2".
function Num([string]$v) { if ($v -match '(\d+)') { [int]$Matches[1] } else { 0 } }
# FLAC's STREAMINFO audio checksum - the MD5 of the UNENCODED audio, at a fixed
# offset: "fLaC" (4) + block header (4) + STREAMINFO (34), last 16 bytes of which
# are the MD5. Reading 42 bytes decodes nothing. Unlike the file's sha256 it does
# not change when tags are edited, which is the only reason retagged library
# copies can be recognised at all. '' for anything that is not FLAC.
function AudioMd5([string]$path) {
  try {
    $fs = [IO.File]::OpenRead($path)
    try {
      $buf = New-Object byte[] 42
      if ($fs.Read($buf, 0, 42) -lt 42) { return '' }
      if ([Text.Encoding]::ASCII.GetString($buf, 0, 4) -ne 'fLaC') { return '' }
      return (($buf[26..41] | ForEach-Object { $_.ToString('x2') }) -join '')
    } finally { $fs.Dispose() }
  } catch { return '' }
}
function KV([string[]]$lines, [string]$key) {
  foreach ($l in $lines) { if ($l -like "$key=*") { return $l.Substring($key.Length + 1) } }
  return ''
}

# ---- run the indexer on hestia -----------------------------------------
# Returns the key=value block index-library.sh prints.
function Invoke-Indexer([string]$root, [string]$out, [string]$mode, [int]$hash = 1) {
  $sh = Join-Path $PSScriptRoot 'index-library.sh'
  if (-not (Test-Path $sh)) { Log "ABORT: index-library.sh not found next to this script"; exit 2 }
  Push $sh '/tmp/index-library.sh'
  return SshRead "sudo -n mkdir -p '$INV' && sudo -n bash /tmp/index-library.sh '$root' '$out' '$mode' $Jobs $hash"
}

Log "== verify-before-delete ($Mode) run $RUN_ID =="
VLedgerRun 'RUN_START' "verify-before-delete mode=$Mode source=$SourceDir delete=$($Delete.IsPresent)"

$rows = @()   # verdict objects: Verdict, Sha, Bytes, Local, Library, Group, Title, Item

if ($Mode -eq 'Music') {
  # ---- 1. library hash index -------------------------------------------
  $idxRemote = "$INV/library-index.music.tsv"
  $age = (SshRead "stat -c %Y '$idxRemote' 2>/dev/null") -join ''
  $stale = $true
  if ($age -match '^\d+$') {
    $hours = ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [int64]$age) / 3600.0
    $stale = $hours -gt $IndexMaxAgeHours
    Log "existing index is $([math]::Round($hours,1))h old (max $IndexMaxAgeHours h)"
  } else { Log "no existing index" }

  $remoteSha = ''
  if ($stale -or $Reindex) {
    Log "indexing $LIBROOT on hestia (this reads the whole library - minutes, not seconds)"
    $meta = Invoke-Indexer $LIBROOT $idxRemote 'music'
    $remoteSha = KV $meta 'index_sha256'
    if (-not $remoteSha) { Log "ABORT: indexer produced no index_sha256 - refusing to judge anything"; VLedgerRun 'RUN_END' 'indexer failed'; exit 3 }
  } else {
    Log "reusing index; pass -Reindex to force"
  }

  # ---- 2. load it -------------------------------------------------------
  # Everything below is derived from the LOCAL copy, so the anchor in the ledger
  # describes the bytes actually used to make the delete decision - not a
  # summary hestia reported about a file that may have been fetched imperfectly.
  $ltmp = [IO.Path]::GetTempFileName()
  $lines = Fetch $idxRemote $ltmp
  $idxSha = $(if (Test-Path $ltmp) { Sha $ltmp } else { '' })
  Remove-Item $ltmp -Force -ErrorAction SilentlyContinue
  if ($lines.Count -eq 0 -or -not $idxSha) { Log "ABORT: could not fetch the index"; VLedgerRun 'RUN_END' 'index fetch failed'; exit 3 }
  if ($remoteSha -and $remoteSha -ne $idxSha) {
    Log "ABORT: index sha mismatch - hestia said $remoteSha, the fetched copy is $idxSha"
    VLedgerRun 'RUN_END' 'index fetch corrupted'; exit 3
  }

  $byHash = @{}
  $byAudio = @{}
  $idxBytes = [long]0
  foreach ($l in $lines) {
    $p = $l -split "`t", 4
    if ($p.Count -lt 3) { continue }
    $idxBytes += [long]$p[1]
    if (-not $byHash.ContainsKey($p[0])) { $byHash[$p[0]] = $p[2] }
    if ($p.Count -ge 4 -and $p[3] -and $p[3] -ne '-' -and -not $byAudio.ContainsKey($p[3])) { $byAudio[$p[3]] = $p[2] }
  }
  Log "library index: $($lines.Count) files, $($byHash.Count) distinct hashes, sha256 $($idxSha.Substring(0,12))"

  # Anchor the inventory in the ledger. The ledger stays a movement log; the
  # inventory is a snapshot file, and this row is what makes it evidence - the
  # index can be re-fetched later and checked against this hash.
  VLedger 'LIBRARY_INDEXED' @([pscustomobject]@{
    Scope = 'run'; Sha = $idxSha; Bytes = $idxBytes; Dest = $idxRemote
    Note  = "root=$LIBROOT files=$($lines.Count) - sha256 anchors the index file"
  })

  # ---- 3. judge the local rips -----------------------------------------
  if (-not (Test-Path $FP)) { Log "ABORT: ffprobe not found at $FP - album grouping needs it"; VLedgerRun 'RUN_END' 'ffprobe missing'; exit 2 }
  if (-not (Test-Path $SourceDir)) { Log "nothing to verify: $SourceDir does not exist"; VLedgerRun 'RUN_END' 'no source dir'; exit 0 }
  $local = Get-ChildItem $SourceDir -File -Recurse -ErrorAction SilentlyContinue |
           Where-Object { $EXTS -contains $_.Extension.ToLower() -and $_.FullName -notmatch '\\_GV\\' }
  if (-not $local) { Log "nothing to verify in $SourceDir"; VLedgerRun 'RUN_END' 'source dir empty'; exit 0 }
  Log "hashing $($local.Count) local file(s) in $SourceDir"

  # ⚠️ Group by TAGS, never by directory. C:\Rips is FLAT - EAC and Picard write
  # several albums interleaved into one folder - so grouping by the containing
  # directory puts every rip in a single group, and one unproven track then
  # blocks every other album on the box. Measured on the first run: 11 unproven
  # Daft Punk remixes held back 301 files that were provably in the library.
  #
  # The album gate itself is not optional. On 2026-08-29 a 2xCD rip moved with
  # only disc 2 present and the source was deleted anyway; disc 1 is gone. Per
  # file, "the bytes are in the library" is true of every track that made it -
  # deleting exactly those leaves an album that can never be re-imported whole.
  # So the unit of deletion is the album, and the album comes from the tags.
  #
  # Only the grouping needs tags. Matching stays on content, and none of the
  # library-naming stack (LinuxName/TruncName/AsciiSafe) is involved.
  $untagged = 0; $audioOnly = 0
  foreach ($f in $local) {
    $tl = & $FP -v quiet -show_entries 'format_tags=album_artist,artist,album,disc,TOTALDISCS,DISCTOTAL' -of default=noprint_wrappers=1 $f.FullName 2>$null
    $t = @{}
    foreach ($l in $tl) {
      $i = $l.IndexOf('=')
      if ($i -gt 0) { $t[$l.Substring(0,$i).Replace('TAG:','').ToLower()] = $l.Substring($i+1) }
    }
    $artist = $(if ($t['album_artist']) { $t['album_artist'] } else { $t['artist'] })
    $album  = $t['album']
    if ($artist -and $album -and $artist -notlike '*Unknown Artist*') {
      $disc = Num $t['disc']; if ($disc -lt 1) { $disc = 1 }
      $dtot = [Math]::Max((Num $t['totaldiscs']), (Num $t['disctotal']))
      $grp  = "$($artist.Trim()) / $($album.Trim())" + $(if ($dtot -gt 1) { " [Disc $disc]" } else { '' })
      $ttl  = $artist.Trim()
    } else {
      # No album context. Judge it strictly alone rather than letting it join,
      # or silently vouch for, a group it cannot be attributed to.
      $untagged++
      $grp = "(untagged) $($f.FullName)"
      $ttl = '(untagged)'
    }

    $h = Sha $f.FullName
    $hit = $byHash[$h]
    $verdict = 'UNVERIFIED'; $note = 'no library file matches this content'
    if ($hit) { $verdict = 'VERIFIED'; $note = '' }
    elseif (-not $RequireExactBytes) {
      # Same audio, different container - the library copy was retagged after it
      # landed. Recorded as a DIFFERENT verdict so the ledger never claims a
      # byte-identical copy it does not have.
      $am = AudioMd5 $f.FullName
      if ($am -and $byAudio.ContainsKey($am)) {
        $hit = $byAudio[$am]
        $verdict = 'VERIFIED_AUDIO'
        $note = "audio identical (flac md5 $am); container differs - library copy retagged"
        $audioOnly++
      }
    }
    $rows += [pscustomobject]@{
      Verdict = $verdict
      Sha = $h; Bytes = [long]$f.Length; Local = $f.FullName
      Library = $(if ($hit) { $hit } else { '' })
      Group = $grp; Title = $ttl; Item = $f.Name; Note = $note
    }
  }
  if ($audioOnly -gt 0) { Log "$audioOnly file(s) proven by FLAC audio checksum, not by container bytes" }
  if ($untagged -gt 0) { Log "$untagged file(s) carry no usable album tags - each judged alone" }
}
else {
  # ---- video: replay the ledger, then re-check the landed files ---------
  $ltmp = [IO.Path]::GetTempFileName()
  $ledgerLines = Fetch "$INV/ledger.tsv" $ltmp
  Remove-Item $ltmp -Force -ErrorAction SilentlyContinue
  if ($ledgerLines.Count -eq 0) { Log "ABORT: could not read the ledger"; exit 3 }
  Log "read $($ledgerLines.Count) ledger row(s)"

  # ts0 run1 event2 scope3 title4 item5 disc6 track7 sha8 bytes9 src10 dest11 note12
  # Later rows win: a title re-encoded and re-landed is judged on its last landing.
  $landed = [ordered]@{}
  foreach ($l in $ledgerLines) {
    $p = $l -split "`t"
    if ($p.Count -lt 12) { continue }
    if ($p[2] -ne 'LANDED') { continue }
    if (-not $p[10]) { continue }
    $landed[$p[10]] = [pscustomobject]@{ Title = $p[4]; Sha = $p[8].ToLower(); Bytes = $p[9]; Src = $p[10]; Dest = $p[11] }
  }
  Log "$($landed.Count) source(s) have a LANDED row"

  $present = @($landed.Values | Where-Object { Test-Path -LiteralPath $_.Src })
  Log "$($present.Count) of those still exist locally"

  $remoteSha = @{}
  if ($present.Count -gt 0) {
    # Hash only the landed files, via the indexer's list: mode.
    $listLocal = Join-Path $env:TEMP "$RUN_ID.destlist.txt"
    Write-LfLines -Path $listLocal -Lines @($present | ForEach-Object { $_.Dest })
    Push $listLocal "/tmp/$RUN_ID.destlist.txt"
    Remove-Item $listLocal -Force -ErrorAction SilentlyContinue
    $out = "$INV/library-index.video.$RUN_ID.tsv"
    $meta = Invoke-Indexer $LIBROOT $out "list:/tmp/$RUN_ID.destlist.txt"
    $idxSha = KV $meta 'index_sha256'
    if (-not $idxSha) { Log "ABORT: indexer produced no index_sha256"; exit 3 }
    VLedger 'LIBRARY_INDEXED' @([pscustomobject]@{
      Scope = 'run'; Sha = $idxSha; Bytes = (KV $meta 'bytes'); Dest = $out
      Note  = "root=$LIBROOT files=$(KV $meta 'files') missing=$(KV $meta 'missing') - landed video re-check"
    })
    $vtmp = [IO.Path]::GetTempFileName()
    foreach ($l in (Fetch $out $vtmp)) {
      $p = $l -split "`t", 4
      if ($p.Count -ge 3) { $remoteSha[$p[2]] = $p[0] }
    }
    Remove-Item $vtmp -Force -ErrorAction SilentlyContinue
  }

  foreach ($e in $present) {
    $now = $remoteSha[$e.Dest]
    $ok  = ($now -and $now -ne 'MISSING' -and $now -eq $e.Sha)
    $note = ''
    if (-not $now) { $note = 'no hash returned for the library file' }
    elseif ($now -eq 'MISSING') { $note = 'library file is GONE' }
    elseif ($now -ne $e.Sha) { $note = "library file CHANGED: ledger $($e.Sha.Substring(0,12)) now $($now.Substring(0,12))" }
    $item = Split-Path -Leaf $e.Src
    $rows += [pscustomobject]@{
      Verdict = $(if ($ok) { 'VERIFIED' } else { 'UNVERIFIED' })
      Sha = $e.Sha; Bytes = $(if ($e.Bytes -match '^\d+$') { [long]$e.Bytes } else { [long]0 }); Local = $e.Src; Library = $e.Dest
      Group = $e.Src; Title = $e.Title; Item = $item; Note = $note
    }
  }

  # Sources sitting on the box with no LANDED row at all are the honest gap:
  # nothing ever proved them, so nothing may delete them.
  if ($SourceDir -and (Test-Path $SourceDir)) {
    $known = @{}
    foreach ($k in $landed.Keys) { $known[$k.ToLower()] = $true }
    $orphans = Get-ChildItem $SourceDir -File -Recurse -ErrorAction SilentlyContinue |
               Where-Object { $EXTS -contains $_.Extension.ToLower() -and -not $known[$_.FullName.ToLower()] }
    if ($orphans) {
      Log "$($orphans.Count) local video file(s) have no LANDED row"

      # Same gap music had: anything that landed BEFORE the ledger existed has no
      # row to replay, and refusing all of it forever is not an answer. So fall
      # back to content, exactly as the music path does - an encoded output that
      # was pushed is byte-identical to the library copy.
      #
      # Size is the prefilter that makes this affordable: a stat-only pass over
      # the library is instant, and only the candidates of exactly the right size
      # are then hashed. Nothing is judged on size - a match of the right NUMBER
      # of wrong bytes is precisely the failure transcode.ps1 stopped accepting.
      $sizeOut = "$INV/library-sizes.video.$RUN_ID.tsv"
      $null = Invoke-Indexer $LIBROOT $sizeOut 'video' 0
      $stmp = [IO.Path]::GetTempFileName()
      $bySize = @{}
      foreach ($l in (Fetch $sizeOut $stmp)) {
        $p = $l -split "`t", 4
        if ($p.Count -lt 3) { continue }
        if (-not $bySize.ContainsKey($p[1])) { $bySize[$p[1]] = @() }
        $bySize[$p[1]] += $p[2]
      }
      Remove-Item $stmp -Force -ErrorAction SilentlyContinue
      SshRead "sudo -n rm -f '$sizeOut'" | Out-Null
      Log "library size index: $($bySize.Count) distinct size(s)"

      $cands = @()
      foreach ($o in $orphans) { if ($bySize.ContainsKey("$($o.Length)")) { $cands += $bySize["$($o.Length)"] } }
      $cands = @($cands | Sort-Object -Unique)

      $candSha = @{}
      if ($cands.Count -gt 0) {
        Log "hashing $($cands.Count) size-matched library candidate(s)"
        $cl = Join-Path $env:TEMP "$RUN_ID.candlist.txt"
        Write-LfLines -Path $cl -Lines $cands
        Push $cl "/tmp/$RUN_ID.candlist.txt"
        Remove-Item $cl -Force -ErrorAction SilentlyContinue
        $co = "$INV/library-index.video.cand.$RUN_ID.tsv"
        $cmeta = Invoke-Indexer $LIBROOT $co "list:/tmp/$RUN_ID.candlist.txt"
        $csha = KV $cmeta 'index_sha256'
        if ($csha) {
          VLedger 'LIBRARY_INDEXED' @([pscustomobject]@{
            Scope = 'run'; Sha = $csha; Bytes = (KV $cmeta 'bytes'); Dest = $co
            Note  = "root=$LIBROOT files=$(KV $cmeta 'files') - size-matched candidates for pre-ledger video"
          })
        }
        $ctmp = [IO.Path]::GetTempFileName()
        foreach ($l in (Fetch $co $ctmp)) {
          $p = $l -split "`t", 4
          if ($p.Count -ge 3 -and $p[0] -ne 'MISSING') { $candSha[$p[0]] = $p[2] }
        }
        Remove-Item $ctmp -Force -ErrorAction SilentlyContinue
      }

      foreach ($o in $orphans) {
        $lh = Sha $o.FullName
        $hit = $candSha[$lh]
        $rows += [pscustomobject]@{
          Verdict = $(if ($hit) { 'VERIFIED' } else { 'UNVERIFIED' })
          Sha = $lh; Bytes = [long]$o.Length; Local = $o.FullName
          Library = $(if ($hit) { $hit } else { '' })
          Group = $o.FullName; Title = $o.Directory.Name; Item = $o.Name
          Note = $(if ($hit) { 'no LANDED row - matched the library by content (landed before the ledger existed)' }
                   else { 'no LANDED row, and no library file holds these bytes' })
        }
      }
    }
  }
}

# ---- verdicts ------------------------------------------------------------
if ($rows.Count -eq 0) { Log "nothing to judge"; VLedgerRun 'RUN_END' 'nothing to judge'; exit 0 }

$scope = $(if ($Mode -eq 'Music') { 'album' } else { 'title' })
$groups = $rows | Group-Object Group | Sort-Object Name
$safeGroups = @(); $blockedGroups = @()

foreach ($g in $groups) {
  $bad   = @($g.Group | Where-Object { $_.Verdict -eq 'UNVERIFIED' })
  $exact = @($g.Group | Where-Object { $_.Verdict -eq 'VERIFIED' })
  $audio = @($g.Group | Where-Object { $_.Verdict -eq 'VERIFIED_AUDIO' })

  if ($exact.Count -gt 0) {
    VLedger 'VERIFIED' @($exact | ForEach-Object {
      [pscustomobject]@{ Scope='file'; Title=$_.Title; Item=$_.Item; Sha=$_.Sha; Bytes=$_.Bytes
                         Src=$_.Local; Dest=$_.Library; Note='byte-identical copy present in the library' }
    })
  }
  if ($audio.Count -gt 0) {
    VLedger 'VERIFIED_AUDIO' @($audio | ForEach-Object {
      [pscustomobject]@{ Scope='file'; Title=$_.Title; Item=$_.Item; Sha=$_.Sha; Bytes=$_.Bytes
                         Src=$_.Local; Dest=$_.Library; Note=$_.Note }
    })
  }
  if ($bad.Count -gt 0) {
    VLedger 'UNVERIFIED' @($bad | ForEach-Object {
      [pscustomobject]@{ Scope='file'; Title=$_.Title; Item=$_.Item; Sha=$_.Sha; Bytes=$_.Bytes
                         Src=$_.Local; Dest=$_.Library
                         Note=$(if ($_.Note) { $_.Note } else { 'no library file matches this content' }) }
    })
  }

  if ($bad.Count -eq 0) {
    $safeGroups += $g
    VLedger 'DELETE_SAFE' @([pscustomobject]@{
      Scope=$scope; Title=$g.Group[0].Title; Item=$g.Name; Bytes=(($g.Group | Measure-Object Bytes -Sum).Sum)
      Note="all $($g.Count) file(s) proven in the library$(if ($audio.Count) { " ($($audio.Count) by audio checksum)" })"
    })
    Log "SAFE    $($g.Name)  ($($g.Count) file(s)$(if ($audio.Count) { ", $($audio.Count) by audio checksum" }))"
  } else {
    $blockedGroups += $g
    Log "BLOCKED $($g.Name)  ($($bad.Count) of $($g.Count) unproven)"
    foreach ($b in $bad | Select-Object -First 3) { Log "          - $($b.Item)$(if ($b.Note) { "  [$($b.Note)]" })" }
  }
}

# ---- audit trail ---------------------------------------------------------
$hdr = @("verdict`tsha256`tbytes`tlocal`tlibrary`tnote")
$body = $rows | ForEach-Object { "$($_.Verdict)`t$($_.Sha)`t$($_.Bytes)`t$($_.Local)`t$($_.Library)`t$($_.Note)" }
Write-LfLines -Path $AUDIT -Lines ($hdr + $body)
Push $AUDIT "/tmp/$RUN_ID.verify.tsv"
$ok = SshRead "sudo -n mv '/tmp/$RUN_ID.verify.tsv' '$INV/' && test -f '$INV/$RUN_ID.verify.tsv' && echo PRESENT"
if ($ok -contains 'PRESENT') { Log "audit off-box: $INV/$RUN_ID.verify.tsv" }
else { Log "WARNING: verify audit did NOT reach hestia (local copy at $AUDIT)" }

# ---- deletion ------------------------------------------------------------
$freed = 0
if ($Delete) {
  if ($safeGroups.Count -eq 0) { Log "-Delete given, but nothing is proven safe" }
  foreach ($g in $safeGroups) {
    $bytes = ($g.Group | Measure-Object Bytes -Sum).Sum
    # The group is an ALBUM, not a directory - C:\Rips is flat and interleaved,
    # so only the files themselves may be removed. Empty directories are tidied
    # afterwards, and $SourceDir itself is never one of them.
    foreach ($r in $g.Group) { Remove-Item -LiteralPath $r.Local -Force -ErrorAction SilentlyContinue }
    foreach ($d in @($g.Group | ForEach-Object { Split-Path -Parent $_.Local } | Sort-Object -Unique)) {
      if ($d -and $d -ne $SourceDir -and (Test-Path -LiteralPath $d) -and
          (Get-ChildItem -LiteralPath $d -Force -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) {
        Remove-Item -LiteralPath $d -Force -ErrorAction SilentlyContinue
      }
    }
    $still = @($g.Group | Where-Object { Test-Path -LiteralPath $_.Local })
    if ($still.Count -eq 0) {
      $freed += $bytes
      VLedger 'LOCAL_DELETED' @([pscustomobject]@{
        Scope=$scope; Title=$g.Group[0].Title; Item=$g.Name; Bytes=$bytes
        Note='removed after the library copy was proven'
      })
      Log "deleted $($g.Name)"
    } else {
      Log "WARNING: $($g.Name) not fully removed ($($still.Count) file(s) remain)"
    }
  }
}

$nSafe = ($safeGroups | ForEach-Object { $_.Count } | Measure-Object -Sum).Sum
$nBlk  = ($blockedGroups | ForEach-Object { $_.Count } | Measure-Object -Sum).Sum
$safeBytes = ($safeGroups | ForEach-Object { ($_.Group | Measure-Object Bytes -Sum).Sum } | Measure-Object -Sum).Sum
Log ""
Log "== $Mode : $($safeGroups.Count) $scope(s) proven / $($blockedGroups.Count) blocked =="
Log "   proven files      : $([int]$nSafe)  ($([math]::Round(($safeBytes/1GB),2)) GB)"
Log "   unproven files    : $([int]$nBlk)"
if ($Delete) { Log "   reclaimed         : $([math]::Round(($freed/1GB),2)) GB" }
else         { Log "   nothing deleted (re-run with -Delete to reclaim the proven ones)" }
Log "   audit             : $AUDIT"

VLedgerRun 'RUN_END' "verified=$([int]$nSafe) unverified=$([int]$nBlk) safe_${scope}s=$($safeGroups.Count) deleted=$($Delete.IsPresent)"
Log '== DONE =='
