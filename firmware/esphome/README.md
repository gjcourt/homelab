# mmWave presence node firmware (ESPHome)

Ready-to-flash ESPHome config for the Seeed "24 GHz mmWave for XIAO" (LD2410)
radar stacked on a Seeed XIAO ESP32-C3. Publishes presence to the mosquitto
broker (`10.42.2.46`, `mmwave` user); Home Assistant auto-discovers the entities.

## One-time setup
```bash
brew install esphome                 # toolchain
cp secrets.yaml.template secrets.yaml
$EDITOR secrets.yaml                  # fill in WiFi + the mmwave MQTT password
```

## Flash (first time = USB)
```bash
esphome config mmwave-office.yaml     # validate
esphome run mmwave-office.yaml        # compile + flash over USB, then tails logs
```
After the first flash, updates are wireless: `esphome run mmwave-office.yaml`
picks the OTA target.

## Verify
- Node web UI: `http://<node-ip>/` — watch "Presence" flip as you move / sit still.
- Broker: `mosquitto_sub -h 10.42.2.46 -t 'mmwave/mmwave-office/#' -v` (anon ok).
- HA: `binary_sensor.mmwave_office_presence` appears under the auto-discovered
  "mmWave Office" device. Stays **on** while you sit still; clears after the
  Clear Delay once you leave.

## More rooms later
Copy `mmwave-office.yaml`, change only the three `substitutions:`
(`node` / `room` / `friendly_room`), re-flash. No re-enroll, no broker changes.

## UART pins — the one gotcha (verified by flashing 2026-06-26)
The radar talks to the XIAO over soft-serial on **D2 (GPIO4)** and **D3 (GPIO5)**
at 256000 8N1. Working config:

```yaml
uart:
  rx_pin: GPIO4   # D2 — carries data FROM the radar (its TX line)
  tx_pin: GPIO5   # D3 — carries data TO the radar (its RX line)
```

The [Seeed wiki](https://wiki.seeedstudio.com/mmwave_for_xiao/) labels these
"D2 - TX, D3 - RX" by **data-flow direction on each line**, not by the XIAO's pin
roles (and never says whose perspective). The trap: reading "D2=TX" as "set the
XIAO's `tx_pin` to D2" gives **zero radar data** — firmware-version sensor empty,
distances NA, presence stuck OFF, and *no* serial garbage (silence ≠ wrong baud;
it's RX listening on the wrong line). Pin numbers per the official
[XIAO ESP32-C3 pinout](https://wiki.seeedstudio.com/XIAO_ESP32C3_Getting_Started/):
D2=GPIO4, D3=GPIO5.

> Chipset: confirmed **LD2410** (the firmware-version sensor reports
> `v2.04.23022511`). If a future board shows no radar frames *with the pins
> above correct*, it may be an MR24HPC1 — switch the `ld2410:`/`platform: ld2410`
> blocks to `seeed_mr24hpc1` and baud `115200`.
