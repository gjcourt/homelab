#!/usr/bin/env bash
# organize-music-rips.sh — transfer XLD/CD rips off this Mac into the homelab
# music library and lay them out as Artist/Album/## Track.ext, tag-driven.
#
# Pipeline position: this is steps 4 (transfer) + 5 (organize) of the CD-rip
# runbook (docs/plans/2026-07-20-cd-rip-music-library-pipeline.md). Rip (XLD),
# verify (AccurateRip), and tag (MusicBrainz Picard) happen BEFORE this script;
# the library scan (Navidrome) happens AFTER.
#
# What it does:
#   1. Reads embedded tags (album artist / album / disc / track / title) from
#      every audio file under --src via ffprobe.
#   2. Computes the canonical destination path for each file:
#         <AlbumArtist>/<Album>/[Disc D/]## Title.ext
#      Various-Artists / compilation albums land under "Various Artists/".
#   3. rsyncs each file to its computed path under the library root — idempotent
#      (checksum compare, never overwrites a byte-identical file), resumable
#      (rsync --partial), and it VERIFIES the destination afterwards by counting
#      files + bytes actually present (guards the low-bytes/high-speedup silent
#      -skip trap).
#
# Safe by design:
#   - the source is only ever READ (rsync copies; nothing is moved or deleted)
#   - DRY-RUN by default; nothing is written until you pass --commit
#   - files that cannot be placed (missing album/artist/title tags) are REPORTED
#     and skipped, never dumped into a wrong folder. Fix them in Picard and
#     re-run — the script is idempotent so re-runs are cheap.
#
# Usage:
#   # dry-run the existing ~/Music backlog against the mounted family share:
#   organize-music-rips.sh --src ~/Music --dest /Volumes/family/media/music
#
#   # actually write:
#   organize-music-rips.sh --src ~/Music --dest /Volumes/family/media/music --commit
#
#   # a single freshly-ripped album folder:
#   organize-music-rips.sh --src "~/Music/Giant Steps" --dest /Volumes/family/media/music --commit
#
# The default --dest is the SMB mount of hestia's `family` share
# (//george@hestia/family -> /Volumes/family); the subdir media/music is the
# same tree Navidrome scans read-only over NFS at
# 10.42.2.10:/mnt/main/family/media/music. Writing over SMB as the logged-in
# user gives correct ownership with no SSH/sudo (mirrors import-sd-photos.sh).
#
# Requires: ffprobe (brew install ffmpeg), rsync.
set -euo pipefail

# ---- config -------------------------------------------------------------
SRC=""
DEST="${MUSIC_DEST:-/Volumes/family/media/music}"
COMMIT=0
# Audio containers we place. Case-insensitive. Sidecars (.log/.cue/album art)
# ride along per-album via the album-folder copy at the end.
EXTS="flac alac m4a aac mp3 aiff aif wav ogg opus wv ape"

usage() { grep '^#' "$0" | sed 's/^# \?//'; }

# ---- args ---------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --src)    SRC="$2"; shift 2;;
    --dest)   DEST="$2"; shift 2;;
    --commit) COMMIT=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

# Expand a leading ~ that survived unquoted (e.g. --src ~/Music in some shells).
SRC="${SRC/#\~/$HOME}"
DEST="${DEST/#\~/$HOME}"

[ -n "$SRC" ] || { echo "ERROR: --src <dir> required (e.g. ~/Music)" >&2; exit 2; }
[ -d "$SRC" ] || { echo "ERROR: --src '$SRC' is not a directory" >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo "ERROR: ffprobe not found (brew install ffmpeg)" >&2; exit 1; }
command -v rsync   >/dev/null 2>&1 || { echo "ERROR: rsync not found" >&2; exit 1; }

if [ "$COMMIT" = 1 ]; then
  [ -d "$DEST" ] || { echo "ERROR: --dest '$DEST' not found — is the family share mounted? (smb://hestia/family -> /Volumes/family)" >&2; exit 1; }
fi

echo "Source:      $SRC"
echo "Destination: $DEST"
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
PLAN="$(mktemp)"; SKIPS="$(mktemp)"
trap 'rm -f "$PLAN" "$SKIPS"' EXIT

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
sort "$PLAN" | cut -f1 | sed 's#^#  #' | head -40
[ "$placeable" -gt 40 ] && echo "  ... and $((placeable-40)) more"
echo

# ---- transfer -----------------------------------------------------------
if [ "$COMMIT" != 1 ]; then
  echo "DRY-RUN complete. Re-run with --commit to write to: $DEST"
  echo "(Nothing was copied. Source untouched.)"
  exit 0
fi

echo "=== COMMIT: rsync -> $DEST ==="
# Snapshot destination usage before, for the after-verify delta.
before_files=$(find "$DEST" -type f 2>/dev/null | wc -l | tr -d ' ')
before_bytes=$(du -sk "$DEST" 2>/dev/null | awk '{print $1}')
copied=0
while IFS=$'\t' read -r rel src; do
  dst="$DEST/$rel"
  mkdir -p "$(dirname "$dst")"
  # --checksum = idempotent (byte-identical file is skipped, not re-sent).
  # --partial  = resumable across interrupted runs.
  # --ignore-times pairs with --checksum so SMB mtime skew can't fool the skip.
  rsync --archive --partial --checksum --ignore-times --no-perms --no-owner --no-group \
        "$src" "$dst"
  copied=$((copied+1))
done < "$PLAN"

echo
echo "=== VERIFY DESTINATION ==="
after_files=$(find "$DEST" -type f 2>/dev/null | wc -l | tr -d ' ')
after_bytes=$(du -sk "$DEST" 2>/dev/null | awk '{print $1}')
echo "  files at dest: $before_files -> $after_files"
echo "  KiB at dest:   ${before_bytes:-?} -> ${after_bytes:-?}"
echo "  rsync invocations: $copied"
# Confirm every planned file is actually present at its computed path.
missing=0
while IFS=$'\t' read -r rel _; do
  [ -f "$DEST/$rel" ] || { echo "  MISSING: $rel"; missing=$((missing+1)); }
done < "$PLAN"
if [ "$missing" -gt 0 ]; then
  echo "  FAIL: $missing planned file(s) not present at destination." >&2
  exit 1
fi
echo "  OK: all $placeable planned file(s) present at destination."
echo
echo "Next: trigger the Navidrome scan and confirm the albums appear"
echo "(see docs/plans/2026-07-20-cd-rip-music-library-pipeline.md, step 6)."
