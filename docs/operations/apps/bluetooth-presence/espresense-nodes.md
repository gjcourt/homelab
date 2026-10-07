# ESPresense scanner nodes — runbook (REFERENCE / DRAFT)

> **Status: reference / draft.** This runbook is used hands-on during **P2**
> (1-node PoC) and **P3** (8-node rollout + calibration) of the
> [Bluetooth-presence build](../../../plans/2026-06-21-bluetooth-presence-system.md).
> Nothing here is wired into the cluster — ESPresense runs as firmware on
> off-cluster XIAO ESP32-C3 boards on the IoT VLAN, publishing to mosquitto over
> MQTT.

## Hardware

- **Board:** Seeed XIAO ESP32-C3 (BLE 5.0, RISC-V, u.FL antenna connector).
  ESPresense support is confirmed and documented:
  <https://wiki.seeedstudio.com/xiao-esp32c3-espresense/>
- **Antenna:** external 2.4 GHz u.FL antenna on every scanner. **Antenna quality
  drives RSSI/detection accuracy far more than anything else** — fit it before
  calibrating.
- **Power:** USB-C from a multi-port supply. In the 5 rooms that also have an
  mmWave node, the scanner + mmWave pair can share one supply.

## The 8 nodes

One scanner per room (see the [README room map](README.md)). Set each node's
name in the ESPresense web UI to exactly:

| Node name | Room |
|---|---|
| `presence-living` | Living |
| `presence-dining` | Dining |
| `presence-foyer` | Foyer (entryway) |
| `presence-office` | Office |
| `presence-master` | Master (SW) |
| `presence-son` | Son's / Niccolo (SE) |
| `presence-guest` | Guest (mid) |
| `presence-kitchen` | Kitchen |

The node name becomes the room key everywhere downstream: ESPresense publishes
nearest-device data under `espresense/rooms/<node>` and HA resolves each person
to a room via `mqtt_room` / the ESPresense integration.

## Flashing

1. Web-flash ESPresense to the XIAO C3 from <https://espresense.com/firmware>
   (or the Seeed wiki link above). Pick the **ESP32-C3** build.
2. Join the node's setup AP, point it at the **IoT-VLAN SSID** (`10.42.7.0/24`).
3. Configure MQTT (next section).
4. Set **Node Name** = the value from the table above.
5. Save; the node reboots and connects.

> Do the PoC (P2) on **one** node end-to-end — beacon → MQTT → HA entity that
> updates as you move the beacon — before bulk-flashing the other 7.

## MQTT settings (per node)

Set these in the ESPresense node's web UI → MQTT:

| Field | Value |
|---|---|
| Host | `<MOSQUITTO_LB_IP>` — the mosquitto **LoadBalancer** IP recorded in plan P2 (off-cluster nodes reach the broker by LB IP, not the in-cluster ClusterIP) |
| Port | `1883` |
| Username | `espresense` — the P2 MQTT user **shared by all 8 scanners** |
| Password | from the P2 SOPS-managed mosquitto password file (operator-entered; never committed) |
| Base topic | `espresense/` |
| Discovery | **on** — publishes HA discovery under `homeassistant/` |

The `espresense` user's ACL (set in P2) grants `espresense/#` + `homeassistant/#`
(for discovery). All 8 scanners use this one shared user — per-node creds buy
little and add SOPS churn.

> **MQTT schema caveat:** the exact JSON ESPresense publishes under
> `espresense/rooms/<node>` and `espresense/devices/<id>` should be confirmed
> against the flashed firmware at P2 before any HA `value_template` hard-codes
> field names. The HA package treats `sensor.<person>_room` as coming from the
> ESPresense HA integration / `mqtt_room`, which insulates most of HA from the
> raw schema.

## Per-node RSSI calibration recipe (P3)

Run this **per node** after placement. Goal: ESPresense's reported distance
should track real distance so nearest-node arbitration is stable.

1. **1 m reference (`rssi@1m`).** Place a beacon exactly **1 metre** from the
   node, line-of-sight. Read the RSSI ESPresense reports for that beacon and set
   the node's **`Calibration / RSSI @ 1m`** to that value. (Typical XIAO-C3 +
   u.FL values land around −59 to −65 dBm, but **measure — don't assume**; the
   antenna and orientation shift it.)
2. **Absorption tuning.** Walk the beacon to **1 m, 3 m, 5 m** and compare
   ESPresense's reported distance to the tape-measured distance. Raise/lower the
   node's **`absorption`** until reported ≈ actual across all three. Higher
   absorption = distance grows faster with attenuation (more walls/bodies).
3. **Walk-test acceptance.** Carry the beacon on a normal 10-minute path through
   the house. Accept the node/room set when the **correct room shows ≥ ~90% of
   dwell time** with no rapid flapping at boundaries.

## Hysteresis / nearest-node-wins (P3)

To stop room-flapping at boundaries:

- Enable **`Max Distance`** per node so a far-away node doesn't claim a beacon
  it can barely hear (set just beyond the room's far wall).
- Rely on ESPresense / `mqtt_room` **nearest-node-wins** arbitration in HA, and
  add a **dwell/hysteresis** so a person must read closer to a new node for a
  short sustained window before the room flips. Tune the window up if you see
  flapping, down if room changes lag.

## What the first live node taught (office, 2026-06-24)

The office scanner is the one node in service so far. It is named **`office-presence`** (room
slug `office_presence`) rather than `presence-office` from the table above, so anything keyed on the
room slug has to use what the node actually publishes. It runs ESPresense v4.0.6 at `10.42.7.135`
and publishes to the mosquitto LoadBalancer at `10.42.2.46:1883` as the shared `espresense` user.

**Enrolling a phone (IRK) — two traps, each cost a diagnosis cycle:**

1. **The enrolled id is `phone:<slug>`, not the bare enroll name.** Read the real id from
   `espresense/settings/irk:<key>/config` (`{"id":"phone:<slug>",...}`). The HA `mqtt_room` sensor's
   `device_id` and `state_topic` must use that exact id.
2. **Restart the node after enrolling.** The IRK is saved to settings immediately, but the running
   BLE scanner loads it into its resolver only at boot. Until a restart nothing publishes under
   `phone:<slug>`. Restart over MQTT: publish `PRESS` to `espresense/rooms/<room>/restart/set`.

**Single-node `max_distance`.** With one node, nearest-node-wins always picks that node, so BLE's
range made `mqtt_room` report the office from the kitchen (~12 m away). On 2026-06-24 the office
node's `max_distance` was lowered from 16 m to 6 m over MQTT
(`espresense/rooms/office_presence/max_distance/set`) to get a usable in-office/away signal.
**Put it back to ~16 m when a second scanner is added** — multi-node arbitration needs every node to
hear the phone so distances can be compared, and a low cap breaks that.

**Antenna.** A XIAO without its u.FL antenna plugged in reports "No networks found" / "Probe Request
Unsuccessful", which looks like wrong Wi-Fi credentials. Check the antenna first.

**HA side.** The node's own entities (connectivity, uptime, Restart / Update / Enroll buttons)
appear through MQTT discovery under `homeassistant/#` with no YAML. Only per-device room tracking
needs an `mqtt_room` sensor. mmWave nodes run ESPHome, not ESPresense; their pinout and build notes
are in `firmware/esphome/mmwave-office.yaml`.

## HolyIOT beacon → person (major / minor) map

**3 HolyIOT iBeacons**, one per household member. Each broadcasts a fixed iBeacon
**UUID** (shared across the 3 — pick one and reuse it) plus a per-beacon
**major / minor** that encodes identity. Configure each via the **"Holyiot
Beacon"** app:

1. Power the beacon; open the Holyiot Beacon app; scan and connect (default PIN
   is usually `0000` / `123456` — verify on the unit).
2. Set the **iBeacon UUID** to the shared household UUID (generate one with
   `uuidgen`; record it in the SOPS-managed secret store, not here).
3. Set **Major / Minor** per the table below.
4. Optionally set a friendly device name and lengthen the advertising interval
   for battery life (≈ 300–1000 ms is fine for room-level presence).
5. Save / write; power-cycle; **verify** with a BLE scanner app (e.g. nRF
   Connect) that the major/minor stuck **before** mapping it in HA.

| Person | Role | Major | Minor | Notes |
|---|---|---|---|---|
| George | adult | `1` | `1` | keyfob |
| Mara | adult (wife) | `1` | `2` | keyfob |
| Niccolo | son | `1` | `3` | **clip beacon** (attaches to clothing/bag) |

> Major/minor values above are the convention this build uses; they are arbitrary
> but must be **set + verified** on the physical beacon and then mapped to the
> matching `sensor.<person>_room` / `person.*` in HA at P4. The shared UUID lets
> all 3 be discovered under one ESPresense device class while major/minor
> disambiguates identity.

### Beacon-configurability gate (P0 → prove in P2)

The chosen HolyIOT SKU **must** expose settable major/minor via the app/NFC —
many cheap keyfobs ship fixed vendor IDs. Confirm the SKU in P0; prove it on the
PoC beacon in P2 before bulk-ordering. **Whether HolyIOT also broadcasts a
battery level is unconfirmed** — if it doesn't, the HA "beacon-stale" alert falls
back to last-seen timing rather than a battery reading (see the HA package).

## Done-when

- **P2:** Zigbee still flows (regression check) **and** the PoC beacon's room /
  RSSI appears as an HA entity that updates as you move it near/away from the node.
- **P3:** the 10-minute walk test shows the correct room ≥ ~90% of dwell time
  with no rapid flapping, on all 8 nodes.
