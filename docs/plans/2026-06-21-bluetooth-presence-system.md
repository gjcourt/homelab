---
status: planned
last_modified: 2026-06-21
summary: "BLE beacon presence/occupancy system (ESPresense → HA → Grafana) for who/how-many is home, per-room"
---

# Bluetooth Presence & Occupancy System

## Objective

Build a self-hosted "presence sensor" that answers **who is home, how many people, and which room** — using BLE beacons (carried per-person) detected by ESP32 scanners, fused in Home Assistant, with history in Grafana. Plus an approximate, anonymous guest headcount.

## Decisions (locked)

| Question | Decision |
|---|---|
| Granularity | **Both, phased** — WiFi home/away first, then per-room BLE |
| Identity method | **Carried BLE beacons** (reliable across iOS/Android; iPhones can't be ID'd passively due to MAC randomization) |
| Scope | **Household + rough guest count** (guest count anonymous/aggregate) |
| Scanner stack | **ESPresense (MQTT)** — beacon/device-centric, enumerates all adverts (needed for guest count), reuses existing mosquitto |
| Scanner hardware | **Seeed XIAO ESP32-C3 + external u.FL antenna** |
| Scale | **8 rooms / 8 scanner nodes**, **3 beacons** to start |

### Why these choices
- **MAC randomization** (iOS 8+/Android 8+ rotate BLE MAC ~every 15 min) makes passive phone identification unreliable → carried beacons with a stable iBeacon major/minor are the robust identity path.
- **ESPresense over Bermuda**: beacon→room + arbitrary-device enumeration (the guest-count signal) over MQTT, which we already run (mosquitto). Bermuda is slicker for known-beacon trilateration but weaker at counting unknown devices.
- **XIAO ESP32-C3**: BLE-only RISC-V (no wasted Classic-BT power), lowest practical idle-scan draw (~0.3 W), $5, and a **u.FL connector** for an external antenna — the cheap accuracy upgrade that reduces room-flapping. For always-on plugged-in nodes the watt differences between boards are a few $/yr; **antenna quality drives detection accuracy far more than power**.

## Architecture

```
[BLE beacons] --advert--> [8x XIAO ESP32-C3 / ESPresense] --MQTT--> [mosquitto]
                                                                        |
                                                              [Home Assistant]
                                                       person / area / occupancy / count
                                                                        |
                                                          [HA Prometheus integration]
                                                                        |
                                                                  [Grafana history]
```

- **ESPresense nodes** publish per-beacon RSSI + nearest-room and enumerate all BLE devices, over MQTT.
- **Home Assistant** = brain: `person` entities, per-person room sensor (`mqtt_room` / ESPresense integration), Bayesian **area occupancy** (fuse BLE-room + UniFi/WiFi + any motion), a `template` "people home" count, beacon **battery-low** alerts.
- **Guest count** is derived from the 8 nodes' device enumeration (distinct non-beacon devices, heavily smoothed) — **no dedicated counting node needed**.
- **Grafana**: HA → Prometheus integration exposes presence/occupancy/count as metrics → history dashboards (same pattern as thermalscope).

## Bill of materials (~$95)

| Item | Qty | ~Unit | ~Total | Notes |
|---|---|---|---|---|
| Seeed XIAO ESP32-C3 | 8 | $5 | $40 | One scanner per room; BLE 5.0, u.FL antenna connector |
| External 2.4 GHz u.FL antenna | 8 | $1.5 | $12 | The accuracy upgrade — better/steadier RSSI |
| 4-port USB power supply | 2 | $15 | $30 | Power 4 nodes each (better efficiency than 8 cheap warts) + USB-C cables |
| BLE iBeacon keyfob | 3 | $5 | $15 | Unique major/minor → person; one as Niccolo's backpack clip |
| **Total** | | | **~$97** | Buy **1 node first** for the Phase-2 PoC before bulk-ordering |

**Future-proof option (not now):** one Seeed **XIAO ESP32-C6** (BLE 5 + WiFi 6 + Thread/Zigbee) as a Matter/Thread foothold — **verify ESPresense firmware support first**, or run that node via the ESPHome BT-proxy route instead.

## Phased plan

### Phase 1 — Home/away now (zero new hardware)
- [ ] HA **UniFi** integration → `device_tracker` per household phone → `person` entities.
- [ ] Tune away-timeout (phones disassociate when asleep → false "away"); combine with periodic ping if needed.
- [ ] Ship value + validate before buying hardware.

### Phase 2 — BLE proof of concept (1 node, 1 beacon)
- [ ] Buy **one** XIAO ESP32-C3 + antenna; flash ESPresense (web flasher); **confirm the exact board is supported**.
- [ ] Point it at mosquitto (creds, topic prefix); place centrally.
- [ ] Pair one beacon, map to a person, confirm room/RSSI surfaces in HA. De-risks the stack before bulk-buy.

### Phase 3 — Roll out + calibrate (8 nodes)
- [ ] Flash + place 8 nodes (one per room), USB-powered, on the IoT VLAN with MQTT reachability.
- [ ] Per-node RSSI calibration (absorption factor + 1 m reference).
- [ ] Add **hysteresis / nearest-node-wins** so a person between rooms doesn't flap.

### Phase 4 — Identity + fusion sensors
- [ ] Beacon→person map for all 3 beacons (major/minor → name).
- [ ] HA: per-person room sensor; **Bayesian area occupancy** (BLE-room + WiFi + motion); `template` "people home" count.
- [ ] Per-beacon **battery-low** alert (silent dead beacon = false away).

### Phase 5 — Guest count + history + automations
- [ ] Derive **"≈ extra devices present"** from node device-enumeration; debounce hard (MAC randomization over-counts → trend, not a number).
- [ ] HA → **Prometheus → Grafana** presence/occupancy history dashboard.
- [ ] Automations: per-room lights/climate on presence; "everyone left → away mode"; guest-detected notifications.

## Accuracy & gotchas (set expectations)
- BLE = **room-level, not metres**; RSSI is noisy, walls attenuate. Nearest-node + hysteresis is the realistic resolution.
- Guest count is a **fuzzy trend**, not a precise headcount.
- **Beacon battery management** is load-bearing — a dead beacon reads as "away."
- **Power-supply efficiency > board efficiency** — use decent multi-port USB, not 8 no-name bricks.

## Privacy
- All self-hosted (homelab guarantees no cloud).
- Household beacons are **opt-in**; guest count is **anonymous/aggregate** only; be transparent with visitors.

## Where artifacts will live
- This plan: `docs/plans/2026-06-21-bluetooth-presence-system.md`.
- ESPresense node configs + HA presence package: a `hosts/` or `apps/` entry in this repo once Phase 3 lands.

## Open questions
- Which 8 rooms specifically (placement map + power drops)?
- IoT VLAN MQTT reachability from the scanner subnet — firewall rule needed?
- Motion sensors available per area to strengthen the Bayesian fusion?
