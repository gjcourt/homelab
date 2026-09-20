#!/usr/bin/env bash
#
# Hash index of a media library, run ON HESTIA by verify-before-delete.ps1.
#
# Emits one row per file:
#   sha256 <TAB> bytes <TAB> path-relative-to-root <TAB> audio_md5
#
# A file that could not be read emits  MISSING <TAB> 0 <TAB> path  rather than
# vanishing from the output - a verifier must be able to tell "not there" from
# "not asked about".
#
# audio_md5 is FLAC's STREAMINFO checksum: the MD5 of the UNENCODED audio, written
# by the encoder and stored at a fixed offset. It is invariant under tag edits,
# which the file's own sha256 is not - measured 2026-09-20, where eleven library
# tracks were byte-for-byte 52 bytes larger than the rips they came from (one
# extra metadata block) while the audio was identical. Without this the verifier
# refuses to bless anything that has been retagged since import, and that set only
# grows. '-' for any file that is not FLAC; there is no equivalent invariant for
# mp3 or m4a, so those stay on sha256 alone.
#
# This is the "ledger proof for what is already in the library". The ledger
# itself stays a movement log - it records things happening, not an inventory -
# so the inventory lives here, in a file whose OWN sha256 is written into the
# ledger. That anchors it: a LIBRARY_INDEXED row plus this file is a claim you
# can re-check later, and editing the index breaks the anchor.
#
# ⚠️ Files are hashed from STDIN (`sha256sum < "$f"`), never by passing the path
# to sha256sum. sha256sum escapes paths containing a backslash or newline by
# prefixing the line with '\' and rewriting the path - which silently corrupts
# exactly the rows you would least want to be wrong. Reading stdin means the
# path never round-trips through sha256sum's output format at all.
#
# Usage:
#   index-library.sh <root> <out.tsv> music            # every audio file under root
#   index-library.sh <root> <out.tsv> video            # every video file under root
#   index-library.sh <root> <out.tsv> list:<file>      # only these relative paths
#
# The list: form exists so the video path does not have to hash 1.6 TB to check
# the handful of files a run actually cares about.
set -uo pipefail

ROOT=${1:?usage: index-library.sh <root> <out.tsv> <music|video|list:FILE> [jobs]}
OUT=${2:?usage: index-library.sh <root> <out.tsv> <music|video|list:FILE> [jobs]}
MODE=${3:-music}
JOBS=${4:-8}

ROOT=${ROOT%/}
[ -d "$ROOT" ] || { echo "index-library: no such root: $ROOT" >&2; exit 2; }

FINDEXPR=""
case "$MODE" in
  music) EXTS='flac mp3 m4a ogg opus wav aiff aif' ;;
  video) EXTS='mkv mp4 m4v avi mpg mpeg ts' ;;
  list:*) EXTS='' ;;
  *) echo "index-library: unknown mode: $MODE" >&2; exit 2 ;;
esac
# Built by hand rather than with a bash array: this script is also read and
# edited from a Windows box, and arrays are the first thing to break there.
for e in $EXTS; do FINDEXPR="$FINDEXPR -o -iname *.$e"; done
FINDEXPR=${FINDEXPR# -o }

produce() {
  case "$MODE" in
    list:*)
      LIST=${MODE#list:}
      [ -f "$LIST" ] || { echo "index-library: no such list: $LIST" >&2; exit 2; }
      while IFS= read -r rel; do
        [ -n "$rel" ] && printf '%s\0' "$ROOT/$rel"
      done < "$LIST"
      ;;
    *)
      # shellcheck disable=SC2086
      find "$ROOT" -type f \( $FINDEXPR \) -print0
      ;;
  esac
}

TMP="$OUT.partial.$$"
: > "$TMP" || { echo "index-library: cannot write $TMP" >&2; exit 2; }

# -I{} runs one file per invocation, so each printf is a single sub-4KiB write
# to the pipe and is therefore atomic - parallel workers cannot interleave
# halves of a row. Anything larger would need a lock.
produce | xargs -0 -P "$JOBS" -I{} sh -c '
      f="$1"
      h=$(sha256sum < "$f" 2>/dev/null | cut -c1-64)
      s=$(stat -c %s "$f" 2>/dev/null)
      if [ -z "$h" ]; then h=MISSING; s=0; fi
      # FLAC layout: "fLaC" (4) + block header (4) + STREAMINFO (34), whose last
      # 16 bytes are the audio MD5. So bytes 26..41 = hex chars 53..84 of the
      # first 42. The magic is checked first: anything else gets "-".
      m=-
      hdr=$(dd if="$f" bs=42 count=1 2>/dev/null | od -An -v -tx1 | tr -d " \n")
      case "$hdr" in
        664c6143*) m=$(printf "%s" "$hdr" | cut -c53-84) ;;
      esac
      [ ${#m} -eq 32 ] || m=-
      printf "%s\t%s\t%s\t%s\n" "$h" "$s" "${f#'"$ROOT"'/}" "$m"
    ' _ {} >> "$TMP"

LC_ALL=C sort -t "$(printf '\t')" -k3,3 "$TMP" > "$OUT" && rm -f "$TMP"

FILES=$(wc -l < "$OUT" | tr -d ' ')
MISS=$(grep -c '^MISSING	' "$OUT" 2>/dev/null || true)
BYTES=$(awk -F'\t' '{s+=$2} END {printf "%d", s+0}' "$OUT")
IDXSHA=$(sha256sum < "$OUT" | cut -c1-64)

# Read back by the caller - keep the key=value shape stable.
echo "root=$ROOT"
echo "mode=$MODE"
echo "files=$FILES"
echo "missing=${MISS:-0}"
echo "bytes=$BYTES"
echo "index=$OUT"
echo "index_sha256=$IDXSHA"
