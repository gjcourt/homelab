#!/usr/bin/env bash
# deployer.sh: entrypoint of the hestia-deployer Custom App (HOMELAB-111).
#
# Every INTERVAL_SECONDS, one tick in two halves (see deployer.py):
#   1. fetch      IN THE CONTAINER: git ls-remote + depth-1 fetch of master's
#                 head into DATA_DIR/cache.git, discovery -> DATA_DIR/desired.json
#   2. reconcile  in hestia's HOST namespaces with the host python3, which ships
#                 truenas_api_client: reads desired.json from the same DATA_DIR
#                 (bind-mounted at the same path), diffs against app.config, and
#                 writes the homelabscope-deploy.prom textfile.
# The container is privileged with pid: host so `nsenter -t 1` reaches host
# init. The script is piped on stdin, so the host process list shows
# `python3 - reconcile`. Environment (DRY_RUN, DATA_DIR, ...) passes through
# nsenter unchanged.
#
# A failed half is logged and the loop carries on; outcomes are reported
# through the textfile metrics, not this process's exit code. A failed fetch
# keeps the last desired.json, so reconcile still runs its local drift check.
set -uo pipefail

INTERVAL_SECONDS="${INTERVAL_SECONDS:-300}"
SCRIPT=/app/deployer.py

echo "hestia-deployer: tick every ${INTERVAL_SECONDS}s, DRY_RUN=${DRY_RUN:-1 (default)}"
while true; do
  python3 "$SCRIPT" fetch
  rc=$?
  [ "$rc" -ne 0 ] && echo "fetch exited $rc"
  nsenter -t 1 -m -u -i -n -p -- /usr/bin/python3 - reconcile < "$SCRIPT"
  rc=$?
  [ "$rc" -ne 0 ] && echo "reconcile exited $rc"
  sleep "$INTERVAL_SECONDS"
done
