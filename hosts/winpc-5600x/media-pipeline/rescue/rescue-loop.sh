#!/bin/bash
# Overnight retry loop for a damaged disc that rescue-disc.ps1 could not finish.
#
# rescue-disc.ps1 runs a fixed retry count and stops when the drive locks up
# (ddrescue exits with errno 13, "Permission denied"). This alternates forward and
# reverse (-R) single retry passes, so each restart after a lock-up works a
# different area of the disc. ddrescue stats are logged after every pass. Create a
# STOP-LOOP file in the work dir to stop after the current pass.
#
# Run under Cygwin (see README "Why Cygwin, not WSL"), against an image that
# rescue-disc.ps1 already started, so <Name>.disc has pinned the disc identity.
#   rescue-loop.sh ET                    # 60 passes on /dev/sr0
#   NAME=ET PASSES=20 DEV=/dev/sr1 rescue-loop.sh
# First used 2026-10 on the E.T. disc (ET.iso, 99.73% recovered).
NAME=${1:-${NAME:?usage: rescue-loop.sh <Name>}}
DIR=${DIR:-/cygdrive/c/media-work/rescue}
DEV=${DEV:-/dev/sr0}
PASSES=${PASSES:-60}
cd "$DIR" || exit 1
[ -f "$NAME.map" ] || { echo "no $NAME.map in $DIR: start with rescue-disc.ps1 first"; exit 1; }
LOG="$NAME-loop.log"
for i in $(seq 1 "$PASSES"); do
  if [ $((i % 2)) -eq 0 ]; then dir="-R"; else dir=""; fi
  echo "== $(date '+%F %T') loop $i ${dir:-forward} ==" >> "$LOG"
  ddrescue -b 2048 -r1 $dir "$DEV" "$NAME.iso" "$NAME.map" >> "$LOG" 2>&1
  rc=$?
  echo "== $(date '+%F %T') loop $i exit $rc ==" >> "$LOG"
  ddrescuelog -t -b 2048 "$NAME.map" | grep -E 'rescued|bad-sector|non-scraped|non-trimmed' >> "$LOG"
  [ -f STOP-LOOP ] && { echo "== stop file seen ==" >> "$LOG"; rm -f STOP-LOOP; break; }
  sleep 60
done
echo "== $(date '+%F %T') LOOP DONE ==" >> "$LOG"
