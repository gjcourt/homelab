#!/bin/bash
# Run the mirror on start (seeds immediately), then every INTERVAL_SECONDS. A
# simple loop instead of cron so the container runs cleanly as a non-root uid —
# the write-protected admin/backups tree requires writes as uid 1028 (george),
# and busybox crond wants root. A run failing (e.g. a transient GitHub blip)
# never kills the loop; it just retries next cycle.
set -uo pipefail

INTERVAL_SECONDS="${INTERVAL_SECONDS:-86400}" # default: daily

while true; do
  /usr/local/bin/github-mirror.sh || echo "[$(date -Is)] mirror run exited non-zero — retrying next cycle"
  echo "[$(date -Is)] sleeping ${INTERVAL_SECONDS}s until next run"
  sleep "${INTERVAL_SECONDS}"
done
