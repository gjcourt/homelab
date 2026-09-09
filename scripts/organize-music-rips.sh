#!/usr/bin/env bash
# organize-music-rips.sh — organize XLD/CD rips into Artist/Album/## Track layout
# and push them to the homelab music library over SSH (rsync -> hestia dataset).
#
# Pipeline position: this is steps 4 (transfer) + 5 (organize) of the CD-rip
# runbook (docs/plans/2026-07-20-cd-rip-music-library-pipeline.md). Rip (XLD),
# verify (AccurateRip), and tag (MusicBrainz Picard) happen BEFORE this script;
# the library scan (Navidrome) happens AFTER.
#
# Transport = rsync over SSH (NOT the SMB mount). The SMB share /Volumes/family
# is flaky and, worse, blocked by macOS TCC for non-interactive/agent processes
# ("Operation not permitted" on ls), so a mount-based path can't run from a
# plain shell. rsync-over-SSH removes SMB entirely: source is local ~/Music
# (readable everywhere), destination is the TrueNAS dataset over SSH. This
# mirrors the sibling photo pipeline (scripts/import-sd-photos.sh): stage to a
# truenas_admin-writable scratch dir, then one `sudo rsync --chown` places the
# files into the owner-only-writable library with correct ownership.
#
# What it does:
#   1. Reads embedded tags (album artist / album / disc / track / title) from
#      every audio file under --src via ffprobe.
#   2. Computes the canonical layout for each file:
#         <AlbumArtist>/<Album>/[Disc NN/]## Title.ext
#      Various-Artists / compilation albums land under "Various Artists/".
#   3. Builds that tree locally (hardlinks — near-free, same filesystem as the
#      source), rsyncs it to a scratch dir on the dataset host, then
#      `sudo rsync --chown`s it into the library. VERIFIES the destination over
#      SSH afterwards (du + file-count + every-planned-file-present) — guards the
#      low-bytes/high-speedup silent-skip trap.
#
# Completeness gate + audit trail (added 2026-09-02, after real data loss):
#   Before any transfer, every album in the batch is checked against the disc
#   and track totals PICARD ALREADY WROTE INTO THE TAGS (TOTALDISCS /
#   TOTALTRACKS). If an album declares more discs or tracks than are present,
#   the script REFUSES to run (exit 2) unless --allow-incomplete is passed.
#   Albums with no totals tags are reported UNVERIFIED, never silently passed.
#
#   Why: on 2026-08-29 a 2xCD rip (The Clash - London Calling) transferred with
#   only disc 2, the source rips were deleted on the strength of a check that
#   reported "untagged/suspect files: 0", and disc 1 was lost permanently. That
#   check validated that tags EXISTED, not that the album was WHOLE -- nine
#   correctly-tagged files are nine correctly-tagged files. The surviving files
#   carried TOTALDISCS=2 the entire time, so no network lookup was ever needed;
#   the evidence was inside the files that were transferred.
#
#   Grouping is by MUSICBRAINZ_ALBUMID where present, because two discs of one
#   release often carry different ALBUM strings and grouping on the string
#   splits a complete release into two half-albums. A gate that cries wolf is a
#   gate that gets ignored.
#
#   The audit trail (sha256 + size + tags + dest path, one row per file, plus a
#   per-album verdict file) is written on EVERY run including dry-runs, kept
#   locally under ~/.local/share/music-rip-audit/, and copied to
#   hestia:/mnt/main/archive/_inventory/rips/ on commit. It is the evidence that
#   a batch was what it claimed to be, and it must outlive the source rips.
#
# Safe by design:
#   - the source is only ever READ (hardlink/rsync copies; nothing is moved)
#   - DRY-RUN by default; nothing is written until you pass --commit
#   - files that cannot be placed (missing album/artist/title tags) are REPORTED
#     and skipped, never dumped into a wrong folder. Fix them in Picard and
#     re-run — the script is idempotent (rsync --checksum) so re-runs are cheap.
#
# Usage:
#   # dry-run the existing ~/Music backlog (prints layout + skip list, no writes):
#   organize-music-rips.sh --src ~/Music
#
#   # transfer for real (refuses if any album is provably incomplete):
#   organize-music-rips.sh --src ~/Music --commit
#
#   # deliberately ship a known-partial album (e.g. you only own one disc):
#   organize-music-rips.sh --src ~/Music --commit --allow-incomplete
#
#   # actually write (stage over SSH + sudo rsync --chown into the library):
#   organize-music-rips.sh --src ~/Music --commit
#
#   # a single freshly-ripped album folder, custom host/path:
#   organize-music-rips.sh --src "~/Music/Giant Steps" \
#     --host truenas_admin@10.42.2.10 --dest /mnt/main/family/media/music --commit
#
# The default --host/--dest point at the TrueNAS dataset that prod Navidrome
# reads read-only over NFS (10.42.2.10:/mnt/main/family/media/music, mounted at
# /music, ND_MUSICFOLDER=/music). The library is owned by uid 1028 (george):users
# mode 755; this script derives that owner uid dynamically and applies it via
# `sudo rsync --chown`, exactly like import-sd-photos.sh.
#
# Requires: ffprobe (brew install ffmpeg), rsync, ssh (key-based access to --host).
set -euo pipefail

# ---- config -------------------------------------------------------------
SRC=""
HESTIA="${MUSIC_HOST:-truenas_admin@10.42.2.10}"
DEST="${MUSIC_DEST:-/mnt/main/family/media/music}"
SCRATCH_ROOT="${MUSIC_SCRATCH:-/mnt/main/downloads/music-import}"
COMMIT=0
# Audio containers we place. Case-insensitive. (Sidecars like .log/.cue/cover.*
# are handled per-album by Picard/XLD; this script places audio.)
EXTS="flac alac m4a aac mp3 aiff aif wav ogg opus wv ape"

usage() { grep '^#' "$0" | sed 's/^# \?//'; }

ALLOW_INCOMPLETE=0

# ---- args ---------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --src)    SRC="$2"; shift 2;;
    --host)   HESTIA="$2"; shift 2;;
    --dest)   DEST="$2"; shift 2;;
    --commit) COMMIT=1; shift;;
    --allow-incomplete) ALLOW_INCOMPLETE=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

# Expand a leading ~ that survived unquoted (e.g. --src ~/Music in some shells).
SRC="${SRC/#\~/$HOME}"

[ -n "$SRC" ] || { echo "ERROR: --src <dir> required (e.g. ~/Music)" >&2; exit 2; }
[ -d "$SRC" ] || { echo "ERROR: --src '$SRC' is not a directory" >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo "ERROR: ffprobe not found (brew install ffmpeg)" >&2; exit 1; }
command -v rsync   >/dev/null 2>&1 || { echo "ERROR: rsync not found" >&2; exit 1; }
command -v ssh     >/dev/null 2>&1 || { echo "ERROR: ssh not found" >&2; exit 1; }

echo "Source:      $SRC"
echo "Dest host:   $HESTIA"
echo "Dest path:   $DEST   (prod Navidrome reads this RO over NFS)"
echo "Mode:        $( [ "$COMMIT" = 1 ] && echo 'COMMIT (will write)' || echo 'DRY-RUN (no writes)')"
echo

# ---- helpers ------------------------------------------------------------

# read_tag FILE KEY [KEY2...] -> first non-empty value among the given tag keys
# (ffprobe lowercases some keys and preserves others; we try several spellings).
read_tag() {
  local file="$1"; shift
  local blob key val
  blob="$(ffprobe -v quiet -show_entries format_tags -of default=noprint_wrappers=1 "$file" 2>/dev/null || true)"
  for key in "$@"; do
    val="$(printf '%s\n' "$blob" | awk -F= -v k="TAG:$key" 'tolower($1)==tolower(k){sub(/^[^=]*=/,""); print; exit}')"
    if [ -n "$val" ]; then printf '%s' "$val"; return 0; fi
  done
  return 0
}

# sanitize STRING -> filesystem-safe path segment (no slashes, no leading dots,
# trimmed). Keeps unicode; only strips characters that break paths or shares.
sanitize() {
  # shellcheck disable=SC2001
  printf '%s' "$1" \
    | sed 's#[/\\]# #g; s/[:*?"<>|]//g' \
    | sed 's/^[.[:space:]]*//; s/[[:space:]]*$//'
}

# zero-pad a track/disc number to 2 digits; non-numeric -> empty
pad2() {
  local n="${1%%/*}"          # "3/12" -> "3"
  n="$(printf '%s' "$n" | tr -cd '0-9')"
  [ -n "$n" ] && printf '%02d' "$((10#$n))" || printf ''
}

# ---- plan ---------------------------------------------------------------
# Build a newline-delimited plan: "<relative-dest>\t<source>" for placeable
# files; collect unplaceable ones separately for the report.
PLAN="$(mktemp)"; SKIPS="$(mktemp)"; STAGE=""
# Audit trail: written for EVERY run (dry-run included) and deliberately NOT
# deleted by cleanup — it is the evidence that a batch was what it claimed to be.
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
AUDIT_DIR="${AUDIT_DIR:-$HOME/.local/share/music-rip-audit}"
mkdir -p "$AUDIT_DIR"
AUDIT="$AUDIT_DIR/${RUN_ID}.files.tsv"
AUDIT_SUM="$AUDIT_DIR/${RUN_ID}.albums.tsv"
: > "$AUDIT"
if command -v sha256sum >/dev/null 2>&1; then SHA256="sha256sum"; else SHA256="shasum -a 256"; fi
cleanup() { rm -f "$PLAN" "$SKIPS"; [ -n "$STAGE" ] && rm -rf "$STAGE"; }
trap cleanup EXIT

find_expr=()
for e in $EXTS; do find_expr+=(-iname "*.$e" -o); done
unset 'find_expr[${#find_expr[@]}-1]'   # drop trailing -o

total=0
while IFS= read -r -d '' f; do
  total=$((total+1))
  album="$(read_tag "$f" ALBUM)"
  title="$(read_tag "$f" TITLE title)"
  track="$(read_tag "$f" track TRACK TRACKNUMBER)"
  disc="$(read_tag "$f" DISCNUMBER disc DISC)"
  disctotal="$(read_tag "$f" DISCTOTAL TOTALDISCS disctotal)"
  tracktotal="$(read_tag "$f" TRACKTOTAL TOTALTRACKS tracktotal)"
  mbid="$(read_tag "$f" MUSICBRAINZ_ALBUMID musicbrainz_albumid)"
  barcode="$(read_tag "$f" BARCODE barcode)"
  artist="$(read_tag "$f" ARTIST artist)"
  albumartist="$(read_tag "$f" album_artist ALBUMARTIST ALBUM_ARTIST)"
  compilation="$(read_tag "$f" COMPILATION compilation)"

  # Folder artist: album-artist wins; VA/compilation collapses to a bucket so a
  # multi-artist album stays a single folder instead of exploding per track.
  folder_artist="$albumartist"
  if [ "$compilation" = "1" ] || [ -z "$folder_artist" ]; then
    if [ -z "$folder_artist" ] && [ -n "$artist" ] && [ "$compilation" != "1" ]; then
      folder_artist="$artist"      # single-artist album that just lacks albumartist
    else
      folder_artist="Various Artists"
    fi
  fi

  # Reject anything we can't place deterministically — report, don't guess.
  if [ -z "$album" ] || [ -z "$title" ] || [ -z "$folder_artist" ]; then
    { printf '%s\t' "$f"
      printf 'missing:'
      [ -z "$folder_artist" ] && printf ' artist'
      [ -z "$album" ]         && printf ' album'
      [ -z "$title" ]         && printf ' title'
      printf '\n'
    } >> "$SKIPS"
    continue
  fi

  ext="${f##*.}"; ext="$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')"
  a="$(sanitize "$folder_artist")"
  al="$(sanitize "$album")"
  ti="$(sanitize "$title")"
  tn="$(pad2 "$track")"
  prefix=""; [ -n "$tn" ] && prefix="$tn "

  # Disc subfolder only for genuine multi-disc sets (disctotal>1, or a disc tag
  # >1 when total is unknown). Single-disc rips stay flat.
  discdir=""
  dn="$(pad2 "$disc")"
  if { [ -n "$disctotal" ] && [ "$(pad2 "$disctotal")" != "01" ] && [ -n "$(pad2 "$disctotal")" ]; } \
     || { [ -z "$disctotal" ] && [ -n "$dn" ] && [ "$dn" != "01" ]; }; then
    [ -n "$dn" ] && discdir="Disc ${dn}/"
    # Prefix track with disc for a flat, sortable name inside the disc folder too.
    [ -n "$dn" ] && [ -n "$tn" ] && prefix="${dn}-${tn} "
  fi

  rel="${a}/${al}/${discdir}${prefix}${ti}.${ext}"
  printf '%s\t%s\n' "$rel" "$f" >> "$PLAN"
  # --- audit trail: one immutable row per file, BEFORE anything moves -------
  sum="$($SHA256 "$f" 2>/dev/null | awk '{print $1}')"
  bytes="$(wc -c < "$f" | tr -d ' ')"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$sum" "$bytes" "${folder_artist}" "${album}" "${disc:-1}" "${disctotal}" \
    "${track}" "${tracktotal}" "${title}" "${mbid}" "${barcode}" "$f" "$rel" >> "$AUDIT"
done < <(find "$SRC" -type f \( "${find_expr[@]}" \) -print0)

placeable="$(wc -l < "$PLAN" | tr -d ' ')"
skipped="$(wc -l < "$SKIPS" | tr -d ' ')"

echo "Scanned $total audio file(s): $placeable placeable, $skipped need attention."
echo

if [ "$skipped" -gt 0 ]; then
  echo "=== SKIPPED (fix tags in Picard, then re-run) ==="
  sed 's#^#  #' "$SKIPS"
  echo
fi

echo "=== PLANNED LAYOUT (first 40) ==="
# awk-limit instead of `head` so an early pipe close can't SIGPIPE-abort the
# upstream sort under `set -e -o pipefail` (that killed the dry-run mid-print).
sort "$PLAN" | cut -f1 | sed 's#^#  #' | awk 'NR<=40'
[ "$placeable" -gt 40 ] && echo "  ... and $((placeable-40)) more"
echo

# ---- completeness gate --------------------------------------------------
# WHY THIS EXISTS: on 2026-08-29 a 2xCD rip (The Clash - London Calling) was
# transferred with only disc 2 present, the source was deleted, and disc 1 was
# lost permanently. The files themselves carried TOTALDISCS=2 the whole time.
# The import check that ran only validated that tags EXISTED ("untagged/suspect
# files: 0"), which is true and useless for this: 9 correctly-tagged files are
# 9 correctly-tagged files. Nothing compared 9 against the declared 19.
#
# This gate is purely local -- no network, no MusicBrainz lookup -- because the
# evidence needed is already embedded in the tags Picard writes.
echo "=== COMPLETENESS ==="
sort -t"$(printf '\t')" -k3,3 -k4,4 "$AUDIT" | awk -F'\t' '
{
  # Group by MusicBrainz release id when Picard supplied one: two discs of the
  # same release routinely carry DIFFERENT album strings ("Mezzanine - Mad
  # Professor" vs "Mezzanine - XX/2018 remaster"), and grouping on the string
  # would split one complete release into two half-albums and cry wolf. A gate
  # with false positives gets ignored, which is how the real one gets missed.
  key=($10!="" ? $10 : $3 " :: " $4)
  label[key]=$3 " :: " $4
  disc=($5==""?1:$5+0); dtot=($6==""?0:$6+0); ttot=($8==""?0:$8+0)
  albums[key]=1
  seen[key SUBSEP disc]=1
  if (dtot>maxdt[key]) maxdt[key]=dtot
  n[key SUBSEP disc]++
  if (ttot>tt[key SUBSEP disc]) tt[key SUBSEP disc]=ttot
  if ($10!="") mbid[key]=$10
}
END{
  bad=0; unver=0; ok=0
  for (k in albums) {
    ndisc=0; missing=""
    for (c in seen) { split(c,parts,SUBSEP); if (parts[1]==k) ndisc++ }
    verdict="OK"; detail=""
    if (maxdt[k]>0 && ndisc<maxdt[k]) {
      verdict="INCOMPLETE"
      for (d=1; d<=maxdt[k]; d++) if (!((k SUBSEP d) in seen)) missing=missing (missing==""?"":",") d
      detail="declared " maxdt[k] " discs, have " ndisc " (missing disc " missing ")"
    }
    if (verdict=="OK") {
      for (c in seen) {
        split(c,parts,SUBSEP)
        if (parts[1]!=k) continue
        d=parts[2]
        if (tt[k SUBSEP d]>0 && n[k SUBSEP d]<tt[k SUBSEP d]) {
          verdict="INCOMPLETE"
          detail=detail (detail==""?"":"; ") "disc " d ": " n[k SUBSEP d] "/" tt[k SUBSEP d] " tracks"
        }
      }
    }
    if (verdict=="OK" && maxdt[k]==0) {
      allz=1
      for (c in seen) { split(c,parts,SUBSEP); if (parts[1]==k && tt[k SUBSEP parts[2]]>0) allz=0 }
      if (allz) { verdict="UNVERIFIED"; detail="no TOTALDISCS/TOTALTRACKS tags - cannot prove completeness" }
    }
    if (verdict=="INCOMPLETE") bad++
    else if (verdict=="UNVERIFIED") unver++
    else ok++
    printf "%s\t%s\t%s\t%s\n", verdict, label[k], detail, mbid[k]
  }
  printf "__TOTALS__\t%d\t%d\t%d\n", ok, bad, unver
}' > "$AUDIT_SUM"

grep -v '^__TOTALS__' "$AUDIT_SUM" | sort | while IFS=$'\t' read -r v k d m; do
  case "$v" in
    OK)         printf '  \033[32mOK\033[0m          %s\n' "$k" ;;
    UNVERIFIED) printf '  \033[33mUNVERIFIED\033[0m  %s  (%s)\n' "$k" "$d" ;;
    INCOMPLETE) printf '  \033[31mINCOMPLETE\033[0m  %s  (%s)\n' "$k" "$d" ;;
  esac
done
TOT_OK="$(awk -F'\t' '/^__TOTALS__/{print $2}' "$AUDIT_SUM")"
TOT_BAD="$(awk -F'\t' '/^__TOTALS__/{print $3}' "$AUDIT_SUM")"
TOT_UNV="$(awk -F'\t' '/^__TOTALS__/{print $4}' "$AUDIT_SUM")"
echo
echo "  complete: ${TOT_OK}   incomplete: ${TOT_BAD}   unverified: ${TOT_UNV}"
echo "  audit trail: $AUDIT"
echo "               $AUDIT_SUM"
echo

if [ "${TOT_BAD:-0}" -gt 0 ] && [ "$ALLOW_INCOMPLETE" != 1 ]; then
  echo "REFUSING TO PROCEED: ${TOT_BAD} album(s) are provably incomplete." >&2
  echo "Re-rip the missing disc(s), or pass --allow-incomplete if this is deliberate." >&2
  echo "DO NOT delete the source rips until this reports 0 incomplete." >&2
  exit 2
fi

# ---- transfer -----------------------------------------------------------
if [ "$COMMIT" != 1 ]; then
  echo "DRY-RUN complete. Re-run with --commit to write to: $HESTIA:$DEST"
  echo "(Nothing was copied. Source untouched.)"
  exit 0
fi

# Confirm SSH + destination reachability, and derive the library owner uid.
echo "=== COMMIT: rsync over SSH -> $HESTIA:$DEST ==="
OWNER_UID="$(ssh -o BatchMode=yes "$HESTIA" "stat -c %u '$DEST'" 2>/dev/null || true)"
[ -n "$OWNER_UID" ] || { echo "ERROR: cannot stat '$DEST' on $HESTIA — is the host reachable and the path present?" >&2; exit 1; }
echo "Destination owner uid: $OWNER_UID (files will be chowned to ${OWNER_UID}:users)"

# 1) Build the organized tree locally via hardlinks (same filesystem as the
#    source => near-instant, no extra disk). Falls back to copy across devices.
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/music-organize.XXXXXX")"
echo "Staging organized tree locally: $STAGE"
while IFS=$'\t' read -r rel src; do
  mkdir -p "$STAGE/$(dirname "$rel")"
  ln "$src" "$STAGE/$rel" 2>/dev/null || cp "$src" "$STAGE/$rel"
done < "$PLAN"

# 2) rsync the tree to a truenas_admin-writable scratch dir on the dataset host.
#    --checksum = idempotent (byte-identical files skipped), --partial +
#    --append-verify = resumable across interrupted runs.
SCRATCH="$SCRATCH_ROOT"
echo "Staging to scratch on host: $HESTIA:$SCRATCH"
ssh -o BatchMode=yes "$HESTIA" "mkdir -p '$SCRATCH'"
rsync -a --checksum --partial --append-verify -e "ssh -o BatchMode=yes" \
      "$STAGE/" "$HESTIA:$SCRATCH/"

# 3) sudo rsync --chown into the owner-only-writable library (mirrors the photo
#    pipeline). --checksum keeps it idempotent; ownership is set inline so the
#    files land as the library owner, not truenas_admin. hestia has passwordless
#    sudo, so `sudo -n` runs non-interactively (works from cron/agents, no TTY).
echo "Placing into library with correct ownership (sudo rsync --chown)..."
ssh -o BatchMode=yes "$HESTIA" \
  "sudo -n rsync -a --checksum --partial --chown=${OWNER_UID}:users '$SCRATCH/' '$DEST/'"

# ---- verify (silent-skip guard) -----------------------------------------
echo
echo "=== VERIFY DESTINATION (over SSH) ==="
ssh -o BatchMode=yes "$HESTIA" \
  "printf '  du -sh: %s\n' \"\$(sudo -n du -sh '$DEST' | cut -f1)\"; printf '  files: %s\n' \"\$(sudo -n find '$DEST' -type f | wc -l | tr -d ' ')\""
# Confirm every planned file is actually present at its computed path. The
# library is mode 700, so read it via sudo (else find/du hit permission denied
# and every planned file falsely reports as missing).
ssh -o BatchMode=yes "$HESTIA" "sudo -n bash -c \"cd '$DEST' && find . -type f | sed 's#^\\./##'\"" \
  | sort > "$STAGE/.remote-list"
cut -f1 "$PLAN" | sort > "$STAGE/.planned-list"
missing="$(comm -23 "$STAGE/.planned-list" "$STAGE/.remote-list" | tee "$STAGE/.missing" | wc -l | tr -d ' ')"
if [ "$missing" -gt 0 ]; then
  echo "  FAIL: $missing planned file(s) NOT present at destination:" >&2
  sed 's#^#    #' "$STAGE/.missing" | head -20 >&2
  exit 1
fi
echo "  OK: all $placeable planned file(s) present at $HESTIA:$DEST."
echo
echo "Scratch left at $HESTIA:$SCRATCH — remove when satisfied:"
echo "  ssh $HESTIA 'rm -rf $SCRATCH'"
echo
echo "Next: trigger the Navidrome scan and confirm the albums appear"
echo "(see docs/plans/2026-07-20-cd-rip-music-library-pipeline.md, step 6)."

# ---- ship the audit trail -----------------------------------------------
# The evidence has to outlive the source. Copy the manifests next to the other
# archive inventories on hestia so "what was in this batch, and was it whole?"
# is answerable months later, from a host that is backed up and snapshotted.
REMOTE_AUDIT="/mnt/main/archive/_inventory/rips"
if ssh -o BatchMode=yes "$HESTIA" "sudo mkdir -p '$REMOTE_AUDIT' && sudo chown truenas_admin:truenas_admin '$REMOTE_AUDIT'" 2>/dev/null; then
  if scp -q "$AUDIT" "$AUDIT_SUM" "$HESTIA:$REMOTE_AUDIT/" 2>/dev/null; then
    echo "Audit trail archived: $HESTIA:$REMOTE_AUDIT/$(basename "$AUDIT")"
    echo "                      $HESTIA:$REMOTE_AUDIT/$(basename "$AUDIT_SUM")"
  else
    echo "WARNING: could not copy audit trail to $HESTIA — local copy retained at $AUDIT" >&2
  fi
else
  echo "WARNING: could not create $REMOTE_AUDIT on $HESTIA — local copy retained at $AUDIT" >&2
fi

echo
echo "DONE. Source left untouched at: $SRC"
echo "Before deleting any source rip, confirm the line above reads 'incomplete: 0'."
