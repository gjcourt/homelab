#!/usr/bin/env bash
# watchdog.sh: entrypoint of the gha-runner-watchdog Custom App (HOMELAB-108).
#
# Runs one tick of watchdog.py every INTERVAL_SECONDS, in hestia's HOST
# namespaces: the host python3 ships truenas_api_client and the host docker CLI
# sees the runner container. The container is privileged with pid: host so
# `nsenter -t 1` can reach host init (see the compose file for why that adds
# no privilege beyond the runner's docker socket).
#
# The script is piped on stdin, so the host process list shows `python3 -`.
# A tick that fails is logged and the loop carries on; outcomes are reported
# through the homelabscope textfile metrics, not this process's exit code.
set -uo pipefail

INTERVAL_SECONDS="${INTERVAL_SECONDS:-300}"
SCRIPT=/app/watchdog.py

echo "gha-runner-watchdog: tick every ${INTERVAL_SECONDS}s"
while true; do
  nsenter -t 1 -m -u -i -n -p -- /usr/bin/python3 - < "$SCRIPT"
  rc=$?
  [ "$rc" -ne 0 ] && echo "tick exited $rc"
  sleep "$INTERVAL_SECONDS"
done
