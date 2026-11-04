#!/bin/bash
# Stop a running ddrescue cleanly: SIGINT (not kill -9), then wait up to 60 s for
# it to exit, so the map file is flushed and the next run resumes where it stopped.
P=$(ps | awk '/ddrescue/{print $1}')
[ -z "$P" ] && { echo "no ddrescue running"; exit 0; }
echo "stopping ddrescue pid(s): $P"
kill -INT $P
for _ in $(seq 1 12); do
  sleep 5
  ps | grep -q ddrescue || { echo ddrescue-stopped; exit 0; }
done
echo still-running
exit 1
