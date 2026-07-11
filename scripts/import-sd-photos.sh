#!/usr/bin/env bash
# import-sd-photos.sh — import photos/videos off an SD card into hestia's
# family photo library, sorted by EXIF capture date into <person>/YYYY/MM.
#
# Safe by design:
#   - the SD card is only ever READ (files are copied, never moved/deleted)
#   - hestia is never OVERWRITTEN (rsync --ignore-existing); collisions are reported
#   - dry-run by default; pass --commit to actually write to hestia
#
# Usage:
#   import-sd-photos.sh --person mara                 # auto-detect SD, dry-run
#   import-sd-photos.sh --person mara --sd "/Volumes/NO NAME"
#   import-sd-photos.sh --person mara --commit        # SSH-stage + emit the sudo finish
#
#   # SMB mode: write straight to a mounted share as the mara/george user (correct
#   # ownership, no sudo). Point --smb at the photos-library root on the Mac, i.e.
#   # the dir that contains <person>/ — for a share of /mnt/main/family mounted at
#   # /Volumes/family that is /Volumes/family/images/photos:
#   import-sd-photos.sh --person mara --smb /Volumes/family/images/photos --commit
#
# Requires: exiftool, rsync. SSH access to hestia (key-based) for the default
# mode; --smb mode needs only the mounted share (no SSH/sudo).
set -euo pipefail

# ---- config -------------------------------------------------------------
HESTIA="${HESTIA:-truenas_admin@10.42.2.10}"
HESTIA_BASE="${HESTIA_BASE:-/mnt/main/family/media/photos}"
STAGE_ROOT="${STAGE_ROOT:-$HOME/sd-import}"
# Media we care about (camera + iPhone). Case-insensitive match.
EXTS="jpg jpeg heic heif png tif tiff mov mp4 m4v cr2 cr3 nef arw raf dng orf rw2"

# ---- args ---------------------------------------------------------------
PERSON=""; SD=""; COMMIT=0; SMB_DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --person) PERSON="$2"; shift 2;;
    --sd)     SD="$2"; shift 2;;
    --commit) COMMIT=1; shift;;
    --smb)    SMB_DEST="$2"; shift 2;;
    --stage)  STAGE_ROOT="$2"; shift 2;;
    -h|--help) grep '^#' "$0" | sed 's/^# \?//'; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$PERSON" ] || { echo "ERROR: --person <name> required (e.g. mara, george)" >&2; exit 2; }

# ---- locate the SD card -------------------------------------------------
if [ -z "$SD" ]; then
  for v in /Volumes/*; do
    [ -d "$v/DCIM" ] && { SD="$v"; break; }
  done
fi
[ -n "$SD" ] && [ -d "$SD" ] || { echo "ERROR: no SD card found (looked for a /Volumes/* with DCIM). Pass --sd." >&2; exit 1; }
if [ -n "$SMB_DEST" ]; then
  [ -d "$SMB_DEST" ] || { echo "ERROR: --smb path '$SMB_DEST' not found — is the share mounted? (e.g. smb://10.42.2.10/family -> /Volumes/family)" >&2; exit 1; }
  DEST_DESC="$SMB_DEST/$PERSON/<YYYY>/<MM>   [SMB direct write — ownership via the logged-in user]"
else
  DEST_DESC="$HESTIA:$HESTIA_BASE/$PERSON/<YYYY>/<MM>   [SSH stage + sudo finish]"
fi
echo "SD card:   $SD"
echo "Person:    $PERSON   ->   $DEST_DESC"
echo "Mode:      $( [ "$COMMIT" = 1 ] && echo 'COMMIT (will write)' || echo 'DRY-RUN (no writes)')"

STAGE="$STAGE_ROOT/$PERSON"
RAW="$STAGE/raw"; SORTED="$STAGE/sorted"
# Fresh staging each run so cards don't contaminate each other (source of truth
# is the SD card + hestia, never this scratch).
rm -rf "$RAW" "$SORTED"
mkdir -p "$RAW" "$SORTED"

# ---- build the find expression for media extensions ---------------------
find_args=(); first=1
for e in $EXTS; do
  if [ $first = 1 ]; then find_args+=( -iname "*.$e" ); first=0
  else find_args+=( -o -iname "*.$e" ); fi
done

# ---- 1) copy media off the card (READ-ONLY on the SD) -------------------
echo
echo "== Copying media off the card (excluding ._* AppleDouble + system dirs) =="
# Prefer DCIM (where cameras/phones store media) to avoid the SD's macOS
# system dirs (.Trashes etc., which deny access); fall back to the whole card.
SRC="$SD"; [ -d "$SD/DCIM" ] && SRC="$SD/DCIM"
# -pn: never overwrite an existing staged file (idempotent re-runs). The trailing
# `|| true` keeps a perm-denied system entry from aborting under `set -e`.
find "$SRC" \( -name '.Trashes' -o -name '.Spotlight-V100' -o -name '.fseventsd' \) -prune -o \
     -type f \( "${find_args[@]}" \) ! -name '._*' -print0 2>/dev/null \
  | while IFS= read -r -d '' f; do cp -pn "$f" "$RAW/" 2>/dev/null || true; done || true
echo "staged in raw/: $(find "$RAW" -type f | wc -l | tr -d ' ') files"

# ---- 2) sort by capture date into sorted/YYYY/MM ------------------------
# Preference: DateTimeOriginal > CreateDate > FileModifyDate (listed last wins).
echo
echo "== Sorting by EXIF capture date into YYYY/MM =="
exiftool -q -m \
  '-Directory<FileModifyDate' \
  '-Directory<CreateDate' \
  '-Directory<DateTimeOriginal' \
  -d "$SORTED/%Y/%m" -r "$RAW" >/dev/null 2>&1 || true
left=$(find "$RAW" -type f | wc -l | tr -d ' ')
[ "$left" = 0 ] || echo "WARNING: $left file(s) left unsorted in $RAW (no readable date) — left in place."

# ---- 3) distribution report --------------------------------------------
echo
echo "== Date distribution (YYYY/MM : count) =="
( cd "$SORTED" && find . -type f | sed 's#^\./##; s#/[^/]*$##' | sort | uniq -c )
total=$(find "$SORTED" -type f | wc -l | tr -d ' ')
echo "TOTAL sorted: $total"

# ---- 4) collision check vs destination ---------------------------------
echo
echo "== Collision check ($( [ -n "$SMB_DEST" ] && echo 'SMB share' || echo hestia ) — same YYYY/MM/name) =="
if [ -n "$SMB_DEST" ]; then
  ( cd "$SMB_DEST/$PERSON" 2>/dev/null && find . -type f | sed 's#^\./##' | sort ) > "$STAGE/.existing" 2>/dev/null || : > "$STAGE/.existing"
else
  ssh -o BatchMode=yes "$HESTIA" "find '$HESTIA_BASE/$PERSON' -type f -printf '%P\n' 2>/dev/null" \
    | sort > "$STAGE/.existing" || : > "$STAGE/.existing"
fi
( cd "$SORTED" && find . -type f | sed 's#^\./##' | sort ) > "$STAGE/.incoming"
collisions=$(comm -12 "$STAGE/.existing" "$STAGE/.incoming" | tee "$STAGE/.collisions" | wc -l | tr -d ' ')
if [ "$collisions" = 0 ]; then
  echo "no collisions — all $total files are new names in their target month."
else
  echo "WARNING: $collisions incoming file(s) already exist at the destination (will NOT be overwritten):"
  head -20 "$STAGE/.collisions" | sed 's/^/  /'
  [ "$collisions" -gt 20 ] && echo "  ... (+$((collisions-20)) more in $STAGE/.collisions)"
fi

# ---- 5) merge (only with --commit) -------------------------------------
echo
if [ "$COMMIT" != 1 ]; then
  echo "DRY-RUN complete. Review the distribution + collisions above."
  echo "To write, re-run with --commit."
  exit 0
fi

if [ -n "$SMB_DEST" ]; then
  # ---- SMB mode: write directly to the mounted share ------------------------
  # Files go in as whoever you authenticated the share with (mara/george), so
  # ownership is correct automatically — no SSH, no sudo, no chown.
  echo "== Writing to the SMB share (rsync --ignore-existing; never overwrites) =="
  mkdir -p "$SMB_DEST/$PERSON"
  rsync -a --ignore-existing "$SORTED/" "$SMB_DEST/$PERSON/"
  added=$(comm -13 "$STAGE/.existing" "$STAGE/.incoming" | wc -l | tr -d ' ')
  echo "wrote ~$added new file(s) to $SMB_DEST/$PERSON/  (skipped $collisions existing)"
  echo
  echo "DONE. Next: trigger an Immich rescan (photos.burntbytes.com -> Administration"
  echo "      -> Libraries -> Scan), confirm, then wipe the SD card."
  exit 0
fi

# ---- SSH-stage mode (default) --------------------------------------------
# The photo library is owned per-person (mode 755, owner-only write) and there's
# no passwordless sudo, so we can't write into it directly. Stage to a scratch
# dir truenas_admin CAN write, then emit the one sudo command to finish.
SCRATCH="/mnt/main/downloads/photo-import/$PERSON"
echo "== Staging to hestia scratch (truenas_admin-writable): $SCRATCH =="
ssh -o BatchMode=yes "$HESTIA" "mkdir -p '$SCRATCH'"
rsync -a "$SORTED/" "$HESTIA:$SCRATCH/"
staged=$(ssh -o BatchMode=yes "$HESTIA" "find '$SCRATCH' -type f | wc -l" | tr -d ' ')
echo "staged on hestia: $staged files"

OWNER_UID=$(ssh -o BatchMode=yes "$HESTIA" "stat -c %u '$HESTIA_BASE/$PERSON'" 2>/dev/null)

# ---- 6) emit the privileged finish step --------------------------------
# One sudo command: rsync sets ownership inline via --chown (no separate, easily
# line-wrapped chown step). --ignore-existing means it never overwrites, and only
# the newly-copied files get the ownership applied.
echo
echo "== STAGED ($staged files). Finish on hestia (needs sudo — library owned by uid $OWNER_UID): =="
cat <<EOF

  ssh $HESTIA
  sudo rsync -a --ignore-existing --chown=${OWNER_UID}:users $SCRATCH/ $HESTIA_BASE/$PERSON/
  rm -rf $SCRATCH    # cleanup (truenas_admin owns it; no sudo needed)

EOF
echo "After that: trigger an Immich rescan, confirm in Immich, then wipe the SD card."
