# Incident: HifiBerry UI Unavailable — beocreate2 TCP Socket Exhaustion

**Date:** 2026-04-25
**Status:** Resolved
**Severity:** Low — HifiBerry web UIs unreachable; audio playback unaffected
**Duration:** Unknown onset; resolved ~2026-04-25 03:45 UTC
**Environments affected:** `kitchen` (10.42.2.38), `living-room` (10.42.2.39)
**Authors:** Copilot

---

## Summary

Both HifiBerry OS devices (`kitchen` and `living-room`) had their web UIs
unreachable externally. Port 80 appeared "filtered" to nmap — packets were
silently dropped rather than rejected.

**Root cause:** The `beocreate2` Node.js server process accumulated hundreds of
TCP connections stuck in `CLOSE_WAIT` state on port 80. The kernel's listen
backlog filled, causing new connection attempts to be silently dropped. The
process itself was alive and registered as `active (running)` in systemd, so
`Restart=always` never triggered a recovery.

Audio playback (Spotify via go-librespot, AirPlay via shairport-sync) was
unaffected as those services run in separate Docker containers and do not depend
on the beocreate2 web server.

---

## Affected Services

| Service / Host | Impact |
|---|---|
| `kitchen` HifiBerry UI (`10.42.2.38`) | Web UI unreachable |
| `living-room` HifiBerry UI (`10.42.2.39`) | Web UI unreachable |
| Spotify Connect / AirPlay | Not affected |
| Homepage links | Broken (added in PR #305 during this incident) |

---

## Timeline

| Time (UTC) | Event |
|---|---|
| Unknown | beocreate2 process on both devices begins accumulating CLOSE_WAIT connections |
| 2026-04-25 ~03:30 | User reports HifiBerry UIs are not working |
| 2026-04-25 ~03:35 | nmap confirms port 80 `filtered` on both .38 and .39; SSH reachable on port 22 |
| 2026-04-25 ~03:37 | iptables confirmed clean — no firewall rules blocking port 80 |
| 2026-04-25 ~03:38 | `/proc/net/tcp6` inspection reveals 300+ connections in `CLOSE_WAIT` (state `08`) on port 80 with full send queues |
| 2026-04-25 ~03:40 | `kill -9` of beocreate2 node PID on .38 — systemd restarts, HTTP 200 restored |
| 2026-04-25 ~03:44 | `kill -9` of beocreate2 node PID on .39 — systemd restarts, HTTP 200 restored |

---

## Root Cause

`beocreate2` is a Node.js HTTP server listening on port 80. Node.js (libuv)
maintains a fixed-size accept backlog. When clients connect and the server does
not drain the socket fast enough — or when clients disconnect without completing
the TCP teardown — connections pile up in `CLOSE_WAIT`.

`CLOSE_WAIT` means the remote end sent FIN (closed its side), but the local
process has not called `close()` on the socket. This is a server-side bug: the
Node.js event loop is not closing connections it has finished with. Over time
hundreds of these accumulate, the file descriptor table fills, and new
`accept()` calls return errors. The kernel then stops acknowledging new SYN
packets, making the port appear filtered.

Systemd's `Restart=always` did not help because the process remained alive —
it was not crashing, just stuck.

A previous occurrence of the same symptom was noted in the STP TCN incident
report (2026-03-10), where both HifiBerry nodes dropped SSH during network
blackouts, suggesting the socket leak may be triggered by network interruptions
that leave half-open connections uncleared.

---

## Resolution

Killed the stuck `node` process on each device. Systemd's `Restart=always`
with `RestartSec=10s` brought beocreate2 back cleanly within seconds.

```bash
# On each device (10.42.2.38 and 10.42.2.39):
kill -9 <node-pid>
# systemd auto-restarts — verify with:
systemctl is-active beocreate2
curl -o /dev/null -w "%{http_code}" http://<ip>/
```

---

## Contributing Factors

- No socket timeout or keep-alive configuration in beocreate2 to reap stale connections
- No external health check or watchdog to detect a live-but-stuck process
- Network interruptions (STP TCN events, DHCP renewals) may trigger connection
  half-opens that are never cleaned up

---

## Action Items

| # | Action | Owner | Status |
|---|---|---|---|
| 1 | Add nightly watchdog cron on both HifiBerry devices to curl localhost and restart beocreate2 if unresponsive | George | Pending |
| 2 | Investigate beocreate2 upstream for socket timeout / keep-alive fix or open issue | George | Pending |
| 3 | Add homepage Music section links for both devices | Copilot | Done (PR #305) |

---

## Lessons Learned

- A process can be `active (running)` in systemd while functionally dead — liveness
  checks must go beyond process existence
- `nmap` reporting `filtered` on a listening port is a strong indicator of kernel
  backlog exhaustion, not necessarily a firewall rule
- `/proc/net/tcp6` with state `08` (CLOSE_WAIT) and non-zero `tx_queue` is the
  definitive diagnostic for this failure mode
- `systemctl restart` hangs when the process holds file descriptors that block shutdown;
  `kill -9` followed by systemd auto-restart is the safe recovery path
