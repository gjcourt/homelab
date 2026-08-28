# dietpi-audio — reproducible Snapcast + Spotify Connect audio node

Provisions a Raspberry Pi (DietPi) into a house audio endpoint identical to the
validated **snap-test** node:

- **snapclient** joins the Snapcast multi-room group (server `10.42.2.37`).
- **go-librespot** advertises the box as a **Spotify Connect** target (point-to-point).
- Both share one USB DAC via an ALSA **dmix** — no exclusive-access fight.
- A wrapper **auto-detects whatever USB DAC is plugged in** (robust to swaps); a
  udev rule restarts both services the instant a DAC is added/removed.

This is the "convert all audio nodes to one setup" bundle — the design record is
[lab `01-016`](https://github.com/gjcourt/lab/blob/main/01-audio-midi/01-016-diy-digital-domain-streamer.md).

## Provision a node

1. **Flash DietPi** to the SD/USB.
2. On the boot partition, edit **`dietpi.txt`**:
   - `AUTO_SETUP_NET_HOSTNAME=<room>` — this becomes the Snapcast **and** Spotify
     device name (e.g. `kitchen`, `living-room`). One unique name per node.
   - WiFi: `AUTO_SETUP_NET_WIFI_ENABLED=1` + SSID/key in `dietpi-wifi.txt`
     (put the node on the IoT VLAN, same as the others).
   - `AUTO_SETUP_INSTALL_SOFTWARE_ID=192` — Snapcast Client (the script also
     installs it if missing).
3. Copy **`Automation_Custom_Script.sh`** to the boot partition root as
   `/boot/Automation_Custom_Script.sh`.
4. **Boot.** DietPi installs snapclient, then runs the script: it dual-homes the
   node (both NICs — see below), then sets up go-librespot, the dmix
   `asound.conf`, the auto-detect wrapper + systemd overrides, and the udev
   rule. When you plug a USB DAC in, both services bind automatically.

**Networking — dual-homed by default.** DietPi only configures its "primary"
adapter and comments out the other, so a WiFi-provisioned node comes up
WiFi-only (`#allow-hotplug eth0`). Step 0 of the script uncomments the disabled
NIC so **both wired and WiFi come up** — a node then works in any room whether or
not there's an ethernet drop; the unused NIC just sits with no carrier. So you
can hand a node WiFi creds and still drop it on wired later with no changes. (It
takes effect on DietPi's end-of-first-run reboot.)

## What it installs

| Path | Purpose |
|---|---|
| `/usr/local/bin/go-librespot` | Spotify Connect daemon (release `v0.7.4`) |
| `/root/.config/go-librespot/config.yml` | device name = hostname, output via dmix, zeroconf on `4070` |
| `/etc/systemd/system/go-librespot.service` | runs it, `Restart=always` |
| `/etc/asound.conf` | generated: shared dmix at the DAC's **detected** card index, plus a plug-wrapped `pcm.snapdmix` alias. Written `0644` — ALSA silently ignores a root-only config for unprivileged clients and falls back to card 0 |
| `/usr/local/bin/snapclient-autodev` | waits for a USB DAC, runs snapclient via dmix |
| `snapclient.service.d/override.conf` | uses the wrapper, retries indefinitely |
| `/usr/local/bin/audio-dmix-detect` | prints the first **playback-capable** USB card index |
| `/usr/local/bin/audio-dmix-refresh` | writes `asound.conf` at the DAC's **current** card index (root only) |
| `/usr/local/bin/audio-capture-detect` | prints the first **capture-capable** USB card index (the TV optical input) |
| `/usr/local/bin/tvloop-autodev` | bridges the TV optical input into the shared dmix; polls quietly while the TV is off |
| `/etc/systemd/system/tvloop.service` | runs it; inert on nodes with no capture device |
| `/opt/toppingctl-venv` + `/opt/toppingctl` | `toppingctl` and its `hid` binding |
| `/usr/local/bin/toppingctl` | wrapper so it is on PATH without activating the venv |
| `/etc/udev/rules.d/99-audio-node.rules` | refresh config, then restart both, on DAC add/remove |
| `/root/.ssh/authorized_keys` | root SSH key, so a node is reachable without a password from first boot |

## Knobs (env vars at the top of the script)

- `SNAPSERVER_HOST` (default `10.42.2.37`) · `GLR_VERSION` (`v0.7.4`) ·
  `ZEROCONF_PORT` (`4070`) · `SSH_PUBKEY` (defaults to the operator key; extra
  keys can also be dropped one-per-line in `/boot/authorized_keys` and are merged).

**Why the key is provisioned:** `kitchen` and `living-room` were both briefly
reachable by nobody — password auth with a password that had since changed, and
no key installed. The recovery path was physically pulling the SD card. A node
should arrive reachable.

## Notes

- **Detection is playback-aware.** A node with a USB *capture* interface (an
  optical input for TV audio) enumerates that as a sound card too, and it can
  come first. Measured on the living-room node 2026-08-28: card 0 was the
  capture device (`pcm0c`), card 1 the DAC (`pcm0p`) — picking "the first USB
  card" aimed dmix at the TV input. `audio-dmix-detect` requires a `pcm*p`.
### TV optical input (`tvloop`)

A node with a USB capture interface (living room: a HiFime UR27, which
enumerates as `SA9227 USB Audio`) bridges the TV's optical output into the
**same dmix** snapclient and go-librespot already share. TV audio therefore
mixes with music instead of fighting for the device, and inherits the Home
Assistant volume control for free — that drives the DAC's own hardware mixer,
underneath all three sources.

`tvloop.service` is installed on **every** node and simply idles where there is
no capture device, so provisioning stays uniform.

**Why the wrapper polls instead of letting systemd restart it.** With no optical
carrier the receiver has nothing to clock off, so the capture device opens and
then errors — `alsaloop` prints `Poll FD initialization failed` and exits 1, and
`arecord` reports `Input/output error`. That is the normal state whenever the TV
is off. Driven by `Restart=`, it becomes a crash loop: **7,650 journal lines per
hour, measured 2026-08-28**, onto a `/var/log` that is a **50 MB tmpfs**. The
wrapper polls every 5s and logs only on transitions — two full on/off cycles
produce 10 lines — and still reconnects within ~5s of the TV coming on.

**Do not tune the buffer settings to fix lip-sync.** `-t 20000` was chosen
empirically: `5000` produced 22 underruns and an explicit `-B 1024 -E 256`
produced 9. The measured A/V offset is ~75 ms and does **not** respond to these
values, nor to the dmix buffer size — halving dmix was predicted to cut 22 ms
and instead moved 9 ms the wrong way. The delay is upstream, in the TV and/or
the capture hardware. `buffer_size` is a ring capacity, not a fixed delay.

- - **`audio-dmix-refresh` writes `/etc`, so it is root-only.** `snapclient` runs
  unprivileged and must not call it; the unit invokes it via `ExecStartPre=+`
  (which runs as root regardless of `User=`), and the wrapper only *gates* on
  `audio-dmix-detect`.
- **`toppingctl` is installed on every node but is inert** until used: it refuses
  to drive any device whose USB product string is not a confirmed model. Five
  Topping models share USB product ID `0x152a:8750` with colliding register maps,
  so PID cannot identify hardware.
- ⚠️ **Do not `apt install python3-hid`** — Debian ships cython-hidapi under that
  name (`hid.device()`), while `toppingctl` needs `hid.Device(path=...)` from the
  PyPI `hid` package. The apt one installs cleanly, imports fine, then fails on a
  missing attribute. Hence the venv.
- **DAC card index is detected, not assumed.** `audio-dmix-refresh` finds the USB
  card in `/proc/asound/cards` and writes `asound.conf` to match, re-running on
  every plug/unplug via udev. This used to hardcode `hw:0` on the assumption that
  the USB DAC was the only sound card — **an assumption that was already false**:
  with onboard audio enabled, `bcm2835` takes card 0 and the DAC lands on card 1,
  so every stream went to the 3.5 mm headphone jack while both services reported
  healthy. Silence, no error. Verified and fixed on `office`, 2026-08-27.
- **Software volume** (`--mixer software`) is deliberate — it survives DAC swaps
  (no dependence on the DAC exposing a hardware mixer). HASS controls it via the
  native Snapcast integration (server control port `1705`).
- **Spotify Connect across VLANs:** nodes sit on the IoT VLAN. If a node doesn't
  appear in the Spotify app (phone on another VLAN), that's mDNS not crossing
  VLANs — the port is pinned (`4070`) so it's allowable, but you'd need an mDNS
  reflector + firewall allow between the phone's VLAN and the node's.

Validated 2026-07-13 on `snap-test` (ADAM Audio D3V DAC).
