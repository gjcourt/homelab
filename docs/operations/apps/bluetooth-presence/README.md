# Bluetooth Presence & Occupancy — reference / draft config artifacts

> **Status: reference / draft.** Nothing in this directory is wired into Flux,
> Home Assistant, or Grafana. These are firmware-config templates, runbooks, and
> HA/observability snippets that get **lifted into place during P1–P5** of the
> build. Until then they are zero-prod-impact reference material.
>
> Authoritative plan: [`docs/plans/2026-06-21-bluetooth-presence-system.md`](../../../plans/2026-06-21-bluetooth-presence-system.md)
> — read it for the architecture, the two-firmware (ESPresense + ESPHome mmWave)
> design, the four MQTT clients, the broker-auth flag-day, and the P-phase
> sequencing contract.

## What this system does

Answers **who is home, how many people, and which room**, using carried BLE
beacons detected by ESP32 scanners, fused in Home Assistant, with history in
Grafana — plus an approximate, anonymous guest headcount.

- **8× ESPresense** BLE scanners (XIAO ESP32-C3, one per room) → per-room nearest
  device + arbitrary-device enumeration (the guest-count signal), over MQTT.
- **5× ESPHome mmWave** nodes (XIAO ESP32-C3 + Seeed 24 GHz radar) in the
  back-half static-presence rooms → static-presence `binary_sensor`s, over MQTT.
- **1× UniFi Protect** front-zone person-detection → `binary_sensor.entry_camera_person`.
- **Zigbee** motion sensors → optional redundant fusion observation.
- **Home Assistant** = brain: per-person room sensors, zone-aware Bayesian
  per-room occupancy, people-home count, node-offline + beacon-stale alerts.
- **Prometheus + Grafana** = history (same ServiceMonitor pattern as thermalscope).

## Locked room → node → presence map (authoritative)

House runs N–S; front = North. 8 ESPresense scanner rooms; 5 of those 8 also get
an mmWave node; the front zone is one camera person-detection sensor. **The 5
mmWave rooms are 5 of the 8 scanner rooms — not 5 extra rooms** (still 8 rooms).

| Room | ESPresense scanner node | mmWave (ESPHome) node | Other presence |
|---|---|---|---|
| Living | `presence-living` | — | front camera zone + BLE |
| Dining | `presence-dining` | — | front camera zone + BLE |
| Foyer (entryway) | `presence-foyer` | — | front camera zone + BLE |
| Office | `presence-office` | `mmwave-office` | mmWave |
| Master (SW) | `presence-master` | `mmwave-master` | mmWave (French doors → aim radar inward) |
| Son's / Niccolo (SE) | `presence-son` | `mmwave-son` | mmWave (French doors; Niccolo's clip beacon) |
| Guest (mid) | `presence-guest` | `mmwave-guest` | mmWave |
| Kitchen | `presence-kitchen` | `mmwave-kitchen` | mmWave (tune out range-hood fan/vibration) |

- **Front zone** (Living / Dining / Foyer) is covered by **one** UniFi Protect
  camera as a single person-detection sensor → `binary_sensor.entry_camera_person`.
  BLE scanners still give per-room resolution *within* the front zone.
- **Persons:** George, Mara (wife), Niccolo (son, clip beacon) → **3 HolyIOT iBeacons**.

## Naming conventions (used consistently across every file here)

| Thing | Convention |
|---|---|
| ESPresense MQTT base topic | `espresense/` |
| ESPresense per-room nearest device | `espresense/rooms/<node>` (e.g. `espresense/rooms/presence-kitchen`) |
| Per-person nearest-room sensor in HA | `sensor.<person>_room` (`sensor.george_room`, `sensor.mara_room`, `sensor.niccolo_room`) via ESPresense HA integration / `mqtt_room` |
| mmWave node MQTT prefix | `mmwave/<node>` (e.g. `mmwave/mmwave-kitchen`) |
| mmWave presence sensor in HA (MQTT discovery) | `binary_sensor.mmwave_<room>_presence` |
| Bayesian per-room occupancy | `binary_sensor.<room>_occupied` |
| People count | `sensor.people_home` (count of `person.*` == home) |
| Anyone-home | `binary_sensor.anyone_home` |
| MQTT users (plan P2) | `espresense`, `mmwave` (+ existing `zigbee2mqtt`, `homeassistant`) |

`<room>` values: `living`, `dining`, `foyer`, `office`, `master`, `son`, `guest`,
`kitchen`. `<node>` values are the scanner / mmWave node names from the table above.

## Files — and which phase each is used in

| File | Used in | Purpose |
|---|---|---|
| [`README.md`](README.md) | — | This index: room map, naming, phase guide. |
| [`esphome-mmwave-node.yaml`](esphome-mmwave-node.yaml) | **P3b** | Parameterized ESPHome firmware config for the 5 mmWave nodes. Flash one per node, overriding `substitutions:`. |
| [`espresense-nodes.md`](espresense-nodes.md) | **P2 (PoC), P3 (rollout)** | Runbook for the 8 ESPresense scanners: flashing, MQTT settings, RSSI calibration recipe, hysteresis, HolyIOT beacon → person major/minor map. |
| [`ha-presence-package.yaml.example`](ha-presence-package.yaml.example) | **P4** | HA package: `mqtt_room` per-person sensors, zone-aware Bayesian `binary_sensor.<room>_occupied` ×8, `sensor.people_home` / `binary_sensor.anyone_home`, node-offline + beacon-stale alerts. Lifted into `apps/base/homeassistant/files/` — see the "how to wire in" note below. |
| [`prometheus-grafana.md`](prometheus-grafana.md) | **P5** | HA `prometheus:` block, the HA Service `metrics` port, and the authenticated ServiceMonitor (bearer-token + `/api/prometheus`). |
| [`grafana-presence-dashboard.json`](grafana-presence-dashboard.json) | **P5** | Grafana dashboard: per-room occupancy timeline, people-home count, per-person room history, guest-count trend. "No data" until metrics exist. |

### Phase-by-phase

- **P1 — Home/away (no new HW):** UniFi Network + UniFi Protect integrations
  (config-flow / `.storage`, not in this repo). Produces `binary_sensor.entry_camera_person`
  and `device_tracker.*` / `person.*`. No file here is *applied* yet, but the
  Bayesian sensors in `ha-presence-package.yaml.example` consume `entry_camera_person`.
- **P2 — BLE PoC + broker exposure:** flash one ESPresense node per
  [`espresense-nodes.md`](espresense-nodes.md); prove beacon → MQTT → HA. Broker
  auth/LB/firewall work happens in `infra/controllers/mosquitto/` (out of scope
  for this docs PR).
- **P3 — Roll out + calibrate 8 scanners:** [`espresense-nodes.md`](espresense-nodes.md)
  calibration recipe + hysteresis.
- **P3b — mmWave nodes (parallel to P3):** flash 5 nodes from
  [`esphome-mmwave-node.yaml`](esphome-mmwave-node.yaml).
- **P4 — Identity + fusion:** lift [`ha-presence-package.yaml.example`](ha-presence-package.yaml.example)
  into HA (see below).
- **P5 — Guest count + history:** wire [`prometheus-grafana.md`](prometheus-grafana.md)
  + import [`grafana-presence-dashboard.json`](grafana-presence-dashboard.json).

### How `ha-presence-package.yaml.example` gets wired in (P4)

There is **no `packages/` directory** in this HA deployment. Config is a
committed ConfigMap built by `configMapGenerator` from `!include`d files in
`apps/base/homeassistant/files/`. So at P4 the contents of
`ha-presence-package.yaml.example` are **split by HA top-level key**, not copied wholesale:

- **`mqtt:`** and **`template:`** blocks → these are single top-level keys.
  `configuration.yaml` already uses `template:` once and has no `mqtt:` yet.
  Add an `!include`d file (e.g. `files/presence.yaml` via
  `mqtt: !include presence_mqtt.yaml`) **or** merge into the existing keys, and
  register any new file in the `configMapGenerator` `files:` list.
- **Bayesian `binary_sensor:`** entries → **APPEND to the existing
  `files/binary_sensors.yaml`** (already `!include`d as `binary_sensor:`). A
  *second* top-level `binary_sensor:` key in `configuration.yaml` is **rejected
  by HA** — there must be exactly one. The Bayesian platform blocks go at the
  bottom of `binary_sensors.yaml`.
- **`automation:`** alerts → append to `files/automations.yaml` (already
  `!include`d as `automation:`).

Several pieces are **config-flow / `.storage`** state, *not* committable YAML:
the MQTT broker connection, ESPresense device discovery, UniFi integrations, and
the Prometheus long-lived token. These are reproduced via the setup runbook and
captured by the HA PVC backup.

## Real uncertainties (verify, don't invent)

- **Seeed mmWave ESPHome component name** — the exact `external_components` /
  platform identifier for the Seeed 24 GHz mmWave Human Static Presence module is
  unconfirmed. Verify at the P2/P3b PoC. The YAML flags this inline.
- **HolyIOT battery broadcast** — whether the chosen HolyIOT beacon advertises a
  battery level (vs. needing a connect) is unconfirmed; the "beacon-stale" alert
  is therefore last-seen-based, not battery-based. Confirm at P0/P2.
- **Exact ESPresense MQTT schema** — the precise JSON published under
  `espresense/rooms/<node>` and `espresense/devices/<id>` is taken from the
  ESPresense docs; confirm field names against the flashed firmware at P2 before
  hard-coding `value_template`s.
