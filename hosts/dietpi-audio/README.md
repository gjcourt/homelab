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
| `/etc/asound.conf` | shared dmix (`default` → `hw:0`) |
| `/usr/local/bin/snapclient-autodev` | waits for a USB DAC, runs snapclient via dmix |
| `snapclient.service.d/override.conf` | uses the wrapper, retries indefinitely |
| `/etc/udev/rules.d/99-audio-node.rules` | restart both on DAC add/remove |

## Knobs (env vars at the top of the script)

- `SNAPSERVER_HOST` (default `10.42.2.37`) · `GLR_VERSION` (`v0.7.4`) ·
  `ZEROCONF_PORT` (`4070`).

## Notes

- **DAC assumption:** the USB DAC is ALSA card `0` (the only sound card on a
  headless node). If you enable onboard/HDMI audio, the dmix slave (`hw:0`) needs
  to follow the USB card index instead.
- **Software volume** (`--mixer software`) is deliberate — it survives DAC swaps
  (no dependence on the DAC exposing a hardware mixer). HASS controls it via the
  native Snapcast integration (server control port `1705`).
- **Spotify Connect across VLANs:** nodes sit on the IoT VLAN. If a node doesn't
  appear in the Spotify app (phone on another VLAN), that's mDNS not crossing
  VLANs — the port is pinned (`4070`) so it's allowable, but you'd need an mDNS
  reflector + firewall allow between the phone's VLAN and the node's.

Validated 2026-07-13 on `snap-test` (ADAM Audio D3V DAC).
