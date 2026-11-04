#!/bin/bash
# One reverse pass, no trim, no scrape (-R -n -N), over what is still non-tried.
# Useful when the drive locks up partway through a forward pass: the far end of
# the disc gets read before the bad area is hit again.
#   rescue-reverse.sh ET
NAME=${1:-${NAME:?usage: rescue-reverse.sh <Name>}}
DIR=${DIR:-/cygdrive/c/media-work/rescue}
DEV=${DEV:-/dev/sr0}
cd "$DIR" || exit 1
[ -f "$NAME.map" ] || { echo "no $NAME.map in $DIR"; exit 1; }
LOG="$NAME-reverse.log"
echo "== $(date '+%F %T') reverse pass (-R -n -N) start ==" >> "$LOG"
ddrescue -b 2048 -n -N -R "$DEV" "$NAME.iso" "$NAME.map" >> "$LOG" 2>&1
echo "== $(date '+%F %T') reverse pass exit $? ==" >> "$LOG"
