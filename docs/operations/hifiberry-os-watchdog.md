# HifiBerry OS — beocreate2 Watchdog Setup

Documents the watchdog installed on `kitchen` (10.42.2.38) and `living-room`
(10.42.2.39) to automatically recover the beocreate2 web UI when it gets stuck.

**Incident context:** [2026-04-25 HifiBerry UI TCP socket exhaustion](incidents/2026-04-25-hifiberry-ui-tcp-socket-exhaustion.md)

---

## Problem

The `beocreate2` Node.js server accumulates TCP connections in `CLOSE_WAIT` on
port 80 over time. The kernel backlog fills, new connections are silently
dropped (port appears `filtered`), but the process stays `active (running)` in
systemd so `Restart=always` never triggers.

Max undetected downtime without the watchdog: unbounded.
Max undetected downtime with the watchdog: ~5 minutes.

---

## What's Installed

| File on device | Source in repo |
|---|---|
| `/usr/bin/beocreate-watchdog` | `scripts/hifiberry/beocreate-watchdog.sh` |
| `/usr/lib/systemd/system/crond.service` | `scripts/hifiberry/crond.service` |
| `/var/spool/cron/crontabs/root` | see crontab entry below |

**Crontab entry** (`/var/spool/cron/crontabs/root`):
```
*/5 * * * * /usr/bin/beocreate-watchdog
```

---

## How It Works

Every 5 minutes busybox crond runs the watchdog script. It does a `curl` to
`http://127.0.0.1/` with a 5s connect timeout and 8s total timeout.

- If the request succeeds: nothing happens.
- If it fails: logs via `logger` (visible in `journalctl -t beocreate-watchdog`)
  and sends `kill -9` to any process matching `node.*beo-server.js`. Systemd's
  `Restart=always` with `RestartSec=10s` brings beocreate2 back within seconds.

---

## Installation (for new devices)

HifiBerry OS is Buildroot-based (BusyBox) — extremely stripped down. A few
gotchas apply.

### 1. Copy the watchdog script

SCP is unreliable on these devices. Use base64 over SSH instead:

```bash
base64 -i scripts/hifiberry/beocreate-watchdog.sh | \
  ssh root@<ip> "base64 -d > /usr/bin/beocreate-watchdog && chmod +x /usr/bin/beocreate-watchdog"
```

> Note: `/usr/local/` does not exist on HifiBerry OS. Use `/usr/bin/`.

### 2. Install the crond systemd unit

Busybox crond is present but has no systemd unit by default:

```bash
base64 -i scripts/hifiberry/crond.service | \
  ssh root@<ip> "base64 -d > /usr/lib/systemd/system/crond.service"
```

### 3. Create the crontab

```bash
ssh root@<ip> "mkdir -p /var/spool/cron/crontabs && \
  echo '*/5 * * * * /usr/bin/beocreate-watchdog' > /var/spool/cron/crontabs/root && \
  chmod 600 /var/spool/cron/crontabs/root"
```

### 4. Enable and start crond

```bash
ssh root@<ip> "systemctl daemon-reload && systemctl enable crond && systemctl start crond"
```

### 5. Verify

```bash
ssh root@<ip> "systemctl is-active crond && crontab -l"
# run watchdog manually to confirm it works cleanly:
ssh root@<ip> "/usr/bin/beocreate-watchdog && echo ok"
```

---

## Checking Watchdog Logs

When the watchdog intervenes it logs via syslog:

```bash
ssh root@<ip> "journalctl -t beocreate-watchdog"
```

---

## Caveats

- These changes are made directly on the device filesystem. HifiBerry OS is
  not managed by Flux — treat each device as an appliance configured
  imperatively.
- If HifiBerry OS is reflashed, all of this must be re-applied.
- The crond systemd unit and watchdog script are **not** managed by the
  HifiBerry package system and will survive normal package updates.
