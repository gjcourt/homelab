#!/bin/sh
# beocreate-watchdog.sh
#
# Watches the beocreate2 web UI on port 80. If the port stops responding
# (TCP backlog exhaustion from CLOSE_WAIT leak), kills the stuck node process
# so systemd's Restart=always can bring it back cleanly.
#
# Installed on HifiBerry OS devices as /usr/bin/beocreate-watchdog
# Run every 5 minutes via busybox crond (see docs/guides/hifiberry-os-watchdog.md)
#
# Incident context: docs/incidents/2026-04-25-hifiberry-ui-tcp-socket-exhaustion.md

if ! curl -sf --connect-timeout 5 --max-time 8 http://127.0.0.1/ > /dev/null 2>&1; then
    logger -t beocreate-watchdog 'port 80 unresponsive, killing beocreate2 node process'
    pkill -9 -f 'node.*beo-server.js' || true
    # systemd Restart=always brings it back within RestartSec (10s)
fi
