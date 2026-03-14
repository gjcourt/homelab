# HifiBerry OS – Spotify Connect Setup

This guide documents the changes required to make Spotify Connect work reliably
on a **HifiBerry OS** (Buildroot/BusyBox) device that also runs Docker-based
extensions (snapcast, shairport-sync, raat, etc.).

Two problems were encountered and resolved on the `kitchen` device (`10.42.2.38`):

1. **avahi advertising Docker bridge IPs** — Spotify could see the device but
   could not connect because mDNS resolved the hostname to a private Docker
   bridge address (`172.x.x.x`) instead of the LAN IP.
2. **vollibrespot 0.2.4 revoked client ID** — After connecting, Spotify
   immediately dropped because vollibrespot's built-in client ID
   (`9223bb6a6d924c8da9b02519d03c987a`) was revoked by Spotify; every playback
   attempt returned HTTP 403 from the keymaster endpoint and caused the process
   to panic.

---

## Background

HifiBerry OS runs `vollibrespot` as a systemd service (`spotify.service`).
When Docker extensions are enabled, Docker creates bridge network interfaces
(`docker0`, `br-*`) on the host with private `172.x.x.x` addresses.

`avahi-daemon` calls `getifaddrs()` to discover all host IP addresses and
includes **all of them** in mDNS A records. As a result,
`kitchen.local` resolves to whichever IP avahi happens to advertise first —
often a Docker bridge address that is unreachable from the LAN.

`vollibrespot 0.2.4` is a HifiBerry fork of librespot 0.1.x. Its embedded
Spotify client ID was revoked by Spotify (exact date unknown, observed March
2026). Every time a client tries to play audio, vollibrespot requests a
decryption token from `hm://keymaster/token/authenticated?client_id=9223bb6a…`,
gets a 403, and panics with `cannot poll Map twice`.

---

## Fix 1 — avahi Docker Bridge IP Poisoning

### Symptoms

```
avahi-browse -r _spotify-connect._tcp
address = [172.18.0.1]   ← Docker bridge, not LAN IP
```

Spotify app shows the device but immediately fails to connect.

### What was done

**1. `/etc/avahi/avahi-daemon.conf` — restrict avahi to eth0**

Under the `[server]` section, add:

```ini
allow-interfaces=eth0
```

This prevents avahi from listening on or publishing records for Docker bridge
interfaces. `deny-interfaces=docker0` alone is insufficient because NSCD/avahi
still queries all IPs when building A records; `allow-interfaces` is the
explicit allowlist.

**2. `/usr/bin/avahi-docker-fix.sh` — strip Docker bridge IPs at boot**

```sh
#!/bin/sh
# Strip Docker bridge interface IPs before avahi-daemon starts so that avahi
# does not include them in mDNS A records.  Docker re-adds them when containers
# start, but avahi reads the address list only at startup.
for addr in $(ip addr show docker0 2>/dev/null | grep "inet " | awk '{print $2}'); do
    ip addr del "$addr" dev docker0 2>/dev/null || true
done
for iface in $(ip link show | awk -F': ' '/^[0-9]+: br-/{print $2}'); do
    for addr in $(ip addr show "$iface" 2>/dev/null | grep "inet " | awk '{print $2}'); do
        ip addr del "$addr" dev "$iface" 2>/dev/null || true
    done
done
exit 0
```

Install it:

```bash
chmod +x /usr/bin/avahi-docker-fix.sh
```

**3. `/etc/systemd/system/avahi-daemon.service.d/remove-docker-ips.conf` — run the script as ExecStartPre**

```ini
[Unit]
After=docker.service

[Service]
ExecStartPre=/usr/bin/avahi-docker-fix.sh
```

> **Why ExecStartPre on avahi-daemon, not a standalone service?**
> A standalone oneshot service that calls `systemctl restart avahi-daemon`
> from within systemd deadlocks because D-Bus job ordering prevents a unit from
> restarting another unit mid-transaction. Using `ExecStartPre` on the target
> service itself avoids this entirely.

Apply:

```bash
systemctl daemon-reload
systemctl restart avahi-daemon
```

### Verify

```bash
avahi-browse -r _spotify-connect._tcp -t --no-fail
# address should be the LAN IP (e.g. 10.42.2.38), not 172.x.x.x

avahi-resolve -4 -n kitchen.local
# kitchen.local   10.42.2.38
```

---

## Fix 2 — Replace vollibrespot with go-librespot

### Symptoms

```
journalctl -u spotify -n 30
...
error 403 for uri hm://keymaster/token/authenticated?client_id=9223bb6a…
Error: MercuryError
thread 'main' panicked: cannot poll Map twice
```

Spotify app connects, then immediately drops. `spotify.service` restarts in a
crash loop (exit code 101).

### Root cause

`vollibrespot 0.2.4` (the HifiBerry-bundled build from May 2024) embeds client
ID `9223bb6a6d924c8da9b02519d03c987a`. Spotify revoked this client ID; all
token requests return HTTP 403. The crash (`cannot poll Map twice`) is a
consequence of vollibrespot's futures-0.1 runtime not handling the error path
correctly.

`go-librespot` (by devgianlu) is an actively maintained Go reimplementation of
librespot that uses a current auth flow. Version 0.6.2 is already used in this
repo's Docker image (`images/go-librespot/`).

### What was done

**1. Download and install the go-librespot ARM64 binary**

```bash
curl -fsSL -o /tmp/go-librespot.tgz \
  https://github.com/devgianlu/go-librespot/releases/download/v0.6.2/go-librespot_linux_arm64.tar.gz

# Verify checksum (from images/go-librespot/Dockerfile)
echo "39eda84dad7e28ad0326e00eeea6ff508ce6c8779aefab952296dfb576b8f60c  /tmp/go-librespot.tgz" \
  | sha256sum -c -

tar -xzf /tmp/go-librespot.tgz -C /tmp
cp /tmp/go-librespot /usr/bin/go-librespot
chmod +x /usr/bin/go-librespot
```

For `amd64` HifiBerry devices use:
`go-librespot_linux_x86_64.tar.gz` with sha256 `5dce7e0902c414ec5d7637c1a529f4fc0dd8f93c3db1020de0625900fca00a15`

**2. `/etc/go-librespot/config.yml` — go-librespot configuration**

```yaml
# go-librespot v0.6.2 configuration
# Replaces vollibrespot (which uses a revoked Spotify client ID)
device_name: "Kitchen"
device_type: "speaker"

credentials:
  # Zeroconf: Spotify app on the same LAN authorizes playback on-the-fly.
  # No username/password needed.
  type: zeroconf

# Use the system default ALSA device (asound.conf routes to softvol -> HifiBerry DAC)
audio_backend: alsa
audio_device: default

log_level: info
```

> go-librespot expects a **directory** path (not a file path). The config file
> must be named `config.yml` inside that directory. The flag is
> `--config_dir <dir>` (note underscore, not hyphen).

**3. `/etc/systemd/system/spotify.service.d/go-librespot.conf` — systemd drop-in**

```ini
# Replaces the vollibrespot ExecStart with go-librespot.
# Vollibrespot v0.2.4 uses a revoked Spotify client ID (9223bb6…) that returns
# HTTP 403 from the keymaster endpoint, causing it to crash on every connection.
# go-librespot v0.6.2 uses a current auth flow and does not have this problem.
[Service]
# HOME must be set; systemd does not set it when running as root without User=
Environment=HOME=/root
# Clear the original ExecStart, then set the replacement.
ExecStart=
ExecStart=/usr/bin/go-librespot --config_dir /etc/go-librespot
```

> Clearing `ExecStart=` before re-setting it is required in systemd drop-ins
> to replace (not append to) the original value.
>
> `HOME` must be set explicitly. The base `spotify.service` unit does not set
> `User=`, so systemd does not populate `HOME`; go-librespot needs it to
> resolve `$XDG_CONFIG_HOME`.

Apply:

```bash
systemctl daemon-reload
systemctl restart spotify
```

### Verify

```bash
journalctl -u spotify -f
# Expect:
# running go-librespot 0.6.2
# zeroconf server listening on port XXXXX
# Started Vollibrespot.   ← description unchanged, that's fine

pgrep -a go-librespot
# 12345 /usr/bin/go-librespot --config_dir /etc/go-librespot
```

Then open Spotify on any device on the same LAN — the device should appear and
stay connected.

---

## File Summary

| File | Change |
|------|--------|
| `/etc/avahi/avahi-daemon.conf` | Added `allow-interfaces=eth0` under `[server]` |
| `/usr/bin/avahi-docker-fix.sh` | New script: strips Docker bridge IPs before avahi starts |
| `/etc/systemd/system/avahi-daemon.service.d/remove-docker-ips.conf` | New drop-in: runs fix script as `ExecStartPre` |
| `/usr/bin/go-librespot` | New binary: go-librespot v0.6.2 ARM64 |
| `/etc/go-librespot/config.yml` | New config: device name, zeroconf auth, ALSA default |
| `/etc/systemd/system/spotify.service.d/go-librespot.conf` | New drop-in: replaces `vollibrespot` `ExecStart` with `go-librespot` |

---

## Notes for New Devices

- All changes are made directly on the device filesystem. HifiBerry OS is not
  managed by Flux; treat each device as an appliance configured imperatively.
- If HifiBerry OS is updated/reflashed, these changes will be lost and must be
  re-applied.
- The go-librespot binary survives normal package updates because it is not
  managed by the HifiBerry package system (it's a standalone binary in
  `/usr/bin/`).
- The `vollibrespot` binary is left in place at `/usr/bin/vollibrespot`; the
  drop-in simply replaces the `ExecStart`. To revert, delete the drop-in and
  reload.
- For devices with a different ALSA card layout, adjust `audio_device` in
  `config.yml`. The `default` ALSA device works on the HifiBerry DAC (PCM5102A)
  because `/etc/asound.conf` routes it through the softvol plugin.
- go-librespot stores its Zeroconf session token in `$HOME/.local/share/go-librespot/`
  (i.e. `/root/.local/share/go-librespot/`). This is populated automatically on
  first connection from the Spotify app — no pre-configuration needed.
