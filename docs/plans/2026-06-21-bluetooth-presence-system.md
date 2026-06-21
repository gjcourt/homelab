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
| Scanner hardware | **Seeed XIAO ESP32-C3 + external u.FL antenna** — ESPresense support confirmed ([Seeed wiki](https://wiki.seeedstudio.com/xiao-esp32c3-espresense/)) |
| Scale | **8 rooms / 8 scanner nodes**, **3 beacons** to start |

### Why these choices
- **MAC randomization** (iOS 8+/Android 8+ rotate BLE MAC ~every 15 min) makes passive phone identification unreliable → carried beacons with a stable iBeacon major/minor are the robust identity path.
- **ESPresense over Bermuda**: beacon→room + arbitrary-device enumeration (the guest-count signal) over MQTT, which we already run (mosquitto). Bermuda is slicker for known-beacon trilateration but weaker at counting unknown devices. (Note: the ESP32-C6 fallback via ESPHome BT-proxy is a *different* data path — it would NOT feed the guest-count enumeration.)
- **XIAO ESP32-C3**: BLE-only RISC-V (no wasted Classic-BT power), lowest practical idle-scan draw (~0.3 W), $5, and a **u.FL connector** for an external antenna. For always-on plugged-in nodes the watt differences between boards are a few $/yr; **antenna quality drives detection accuracy far more than power**. ESPresense flashing for this exact board is documented by Seeed.

## Architecture

```
[BLE beacons] --advert--> [8x XIAO ESP32-C3 / ESPresense] --MQTT--> [mosquitto (LB IP)]
                                                                        |
                                                              [Home Assistant]
                                                       person / area / occupancy / count
                                                                        |
                                                          [HA prometheus: /api/prometheus]
                                                                        |
                                                        [ServiceMonitor → Prometheus → Grafana]
```

- **ESPresense nodes** publish per-beacon RSSI + nearest-room and enumerate all BLE devices, over MQTT.
- **Home Assistant** = brain: `person` entities, per-person room sensor (`mqtt_room` / ESPresense integration), Bayesian **area occupancy** (fuse BLE-room + UniFi/WiFi + Zigbee motion), a `template` "people home" count, beacon **battery-low** + node-**offline** alerts.
- **Guest count** is derived from the 8 nodes' device enumeration (distinct non-beacon devices, heavily smoothed) — **no dedicated counting node needed**.
- **Grafana**: HA `prometheus:` endpoint → ServiceMonitor → Prometheus → history dashboards (same pattern as thermalscope).

## Current infra state (verified) — the gates live here

| Component | State today | What this build needs |
|---|---|---|
| mosquitto | **Singleton** infra-controller (`mosquitto.mosquitto:1883`), `allow_anonymous true`, shared by prod HA + staging HA + **zigbee2mqtt (connects anonymously)**. **No live staging broker** — `apps/staging/mosquitto` exists & is valid but isn't listed in `apps/staging/kustomization.yaml`, so no `mosquitto-stage` is deployed → an auth change is a **flag-day** on the single prod broker. (Wiring the overlay in would give a staging broker to rehearse the flip — optional.) | **LoadBalancer IP for the off-cluster ESP32 nodes only** (HA + z2m keep the in-cluster ClusterIP — no netpol change). Per-client **auth/ACL for all three clients**, flipped via a **two-commit interlock** (add creds while anonymous still on → then flip `allow_anonymous false`; see P2). |
| Network | LB pool `home-c-pool` (`10.42.2.40–254`) announced on the **Lab VLAN** (Cilium L2 **and** BGP). ESP32 nodes go on **IoT VLAN `10.42.7.0/24`** (cross-subnet). | Workers BGP-peer the UCGF (`10.42.2.1`, which is *also* the IoT gateway) → it learns the LB /32, so IoT clients route to it through the UCGF. Add a UCGF allow `10.42.7.0/24 → <LB IP>:1883`. **Validate from a real IoT-VLAN host** — L2 announcement makes the same IP reachable from the Lab VLAN and will *mask a missing BGP route* during same-VLAN testing. |
| Home Assistant | git-versioned ConfigMap; **no `mqtt:`, no `prometheus:`, no UniFi** blocks. CiliumNetworkPolicy **already allows egress to mosquitto:1883** (half-done) | Enable MQTT + Prometheus integrations; UniFi integration for P1 |
| zigbee2mqtt | deployed, wired to mosquitto, `permit_join: true`; **no paired-device list in repo** | **Inventory** paired motion/occupancy sensors (P0) for Bayesian fusion |
| Patterns to copy | `apps/base/adguard/service.yaml` (LB), `apps/base/thermalscope/servicemonitor.yaml` (metrics; `release: kube-prometheus-stack` selector is **mandatory**) | — |

### Declarative-YAML vs UI/`.storage` state (read before P1)
HA config here is a committed ConfigMap, but several integrations are **config-flow (UI) only** — their state lives in HA's `.storage/` on the PVC, **not** in git:
- **Declarative (committable as a package):** `mqtt:` sensors, `template` sensors, `prometheus:` block, Bayesian `binary_sensor`, the people-home count.
- **UI / `.storage` (not in git):** UniFi integration, the MQTT *broker* connection, ESPresense device discovery, the Prometheus long-lived token.
**Decision needed:** document how the `.storage` pieces are reproduced (setup runbook) and that they're captured by the HA PVC backup, so a rebuild isn't lossy.

## Phase gating & dependency map (the sequencing contract)

> Read top-to-bottom: each phase's **gates** must be green before it starts. Long-lead items are called out so they're kicked off early.

| Phase | Gates (must be true BEFORE start) | Produces / unblocks | Effort |
|---|---|---|---|
| **P0 — Inventory & long-lead** | none | Zigbee motion-sensor list; chosen 8 rooms + power map; **beacon SKU confirmed configurable**; hardware ordered (lead time) | ~1–2 h |
| **P1 — Home/away (no HW)** | UniFi local read-only account + URL; `person` entities created; (SOPS secret for creds) | `device_tracker`/`person` home-away = the WiFi signal P4 fuses | ~2–3 h |
| **P2 — BLE PoC (1 node)** | mosquitto **LB IP** live + **IoT→:1883 firewall** + **auth/ACL + SOPS**; HA **MQTT integration** enabled; ESPresense flashed to C3 (✓ supported); 1 beacon with **set+verified major/minor** | Validated beacon→MQTT→HA path; firmware image + node-config template for P3 | ~3–4 h (mostly the broker exposure) |
| **P3 — Roll out + calibrate (8 nodes)** | P2 green; 8 nodes flashed; placement/power map (from P0); firewall covers all nodes | 8 calibrated room sensors + hysteresis; room-resolution substrate | ~3–4 h |
| **P4 — Identity + fusion** | 3 beacons configured (major/minor → person); P3 room sensors live; motion sensors inventoried (P0); battery/offline signals available | person-room sensors; Bayesian occupancy; people-home count; alerts | ~2–3 h |
| **P5 — Guest count + history + automations** | HA **`prometheus:`** enabled + **ServiceMonitor** (label `release: kube-prometheus-stack`) + token; P4 entities stable | Grafana history; guest-count trend; automations | ~2–3 h |

**Hard cross-phase dependencies (the things that bite if mis-ordered):**
1. **mosquitto cross-subnet exposure + auth** gates P2 and everything after — it is the single biggest infra task, not a one-liner. Build it *before* buying the PoC node.
2. **The `allow_anonymous false` flip is a flag-day** on the singleton broker shared with **zigbee2mqtt** (which connects anonymously and is the P4 motion source). The two-commit interlock (add all three clients' creds first, then flip) is mandatory — getting this wrong silently kills Zigbee fusion.
3. **Configurable beacon (settable major/minor)** gates ALL of P4 — verify on the PoC beacon in P2, not at 3-beacon rollout.
4. **HA MQTT integration** gates P2; **HA Prometheus + a minted long-lived token + ServiceMonitor** gates P5.

## Bill of materials (~$97)

| Item | Qty | ~Unit | ~Total | Notes |
|---|---|---|---|---|
| Seeed XIAO ESP32-C3 | 8 | $5 | $40 | ESPresense-supported (Seeed wiki); BLE 5.0, u.FL connector |
| External 2.4 GHz u.FL antenna | 8 | $1.5 | $12 | The accuracy upgrade — better/steadier RSSI |
| 4-port USB power supply + USB-C cables | 2 | $15 | $30 | Power 4 nodes each (better efficiency than 8 cheap warts) |
| **Configurable** BLE iBeacon keyfob | 3 | $5 | $15 | **Must expose major/minor** via app/NFC (verify before ordering); one as Niccolo's clip; coin-cell |
| **Total** | | | **~$97** | Buy **1 node + 1 beacon first** for the P2 PoC before bulk-ordering |

**Beacon SKU gate:** pin an exact model documented as user-configurable (major/minor settable + a known config app/NFC method). Many cheap keyfobs ship fixed vendor IDs — buying the wrong one sinks the P4 identity layer. Confirm in P0; prove in P2.

**Future-proof option (not now):** one Seeed **XIAO ESP32-C6** (BLE 5 + WiFi 6 + Thread/Zigbee) as a Matter/Thread foothold — **ESPresense support is unconfirmed**; if used, it runs via the ESPHome BT-proxy route (separate data path, no guest-count enumeration).

## Phased plan (tasks)

### P0 — Inventory & long-lead (do first, unblocks everything)
- [ ] List zigbee2mqtt paired devices (`kubectl exec` / z2m UI); enumerate HA `binary_sensor.*` motion/occupancy entities → fusion inputs for P4 (or note none exist).
- [ ] Choose the 8 rooms + mark power drops / node mounting points.
- [ ] Confirm a **configurable** iBeacon SKU (major/minor settable) + its config method.
- [ ] Order 1 node + 1 beacon for PoC (and queue the rest).

### P1 — Home/away (zero new hardware)
- [ ] Create a UniFi **local read-only** account; add the **UniFi integration** in HA (config-flow; creds via SOPS); note this is `.storage`, not committed YAML.
- [ ] Create `person` entities; map `device_tracker.*` (phones) → persons.
- [ ] Tune away-timeout (phones disassociate when asleep → false "away"); combine with periodic ping if needed.
- [ ] **Done when:** home/away flips correctly within ~1–2 min for each household phone over a day of use.

### P2 — BLE PoC + the broker exposure (de-risk the hard infra)
*Deploy path: all HA/mosquitto changes ship via the normal Flux flow (PR → CI builds `staging` branch → validate → merge to `master`); `mosquitto` is a plain-name app so prod is the only live broker. Optional rehearsal: wire `apps/staging/mosquitto` into `apps/staging/kustomization.yaml` first and dry-run Commit A/B against `mosquitto-stage`.*

**The auth change is a flag-day on a singleton broker — do it as TWO commits so no client locks out:**
- [ ] **Commit A (additive; broker stays `allow_anonymous true`):** mosquitto password file + ACL as a **SOPS secret**, with **three** users — **zigbee2mqtt** (`zigbee2mqtt/#` rw), **homeassistant** (read-all + `homeassistant/#` rw for discovery), **espresense** (`espresense/#` + `homeassistant/#` rw so discovery auto-creates entities). **One shared `espresense` user** for all 8 nodes (per-node creds buy little; 8× SOPS churn).
- [ ] In the SAME commit, wire creds into each client: z2m configmap `user:`/`password:`, HA MQTT creds, ESPresense MQTT user. Confirm **all three authenticate with creds while anonymous is still allowed**.
- [ ] **Commit B:** flip `allow_anonymous false`. **Backout:** revert Commit B → anonymous back on; clients keep working on creds (low-risk).
- [ ] **mosquitto LB Service** for the **ESP32 nodes only** (`lbipam.cilium.io/ip-pool: home-c-pool`, copy `apps/base/adguard/service.yaml`); record the IP. **HA + z2m keep in-cluster `mosquitto.mosquitto:1883`** (no netpol change, no UCGF hairpin). **Backout:** delete the LB Service → broker returns ClusterIP-only.
- [ ] **UCGF firewall** allow `10.42.7.0/24 → <LB IP>:1883`. **Validate from a real IoT-VLAN host** (a Lab-VLAN test false-passes via L2 announcement).
- [ ] Enable HA **MQTT integration** → in-cluster `mosquitto.mosquitto:1883`, creds from SOPS.
- [ ] Flash ESPresense to the XIAO C3 (Seeed wiki); set node name; point at the **LB IP**; confirm `discovery: true` publishes under `homeassistant/`.
- [ ] **Set + verify major/minor** on the PoC beacon; map to a person.
- [ ] **Done when:** **Zigbee still flows (regression check)** AND the beacon's room/RSSI appears as an HA entity and updates as you move it near/away from the node.

### P3 — Roll out + calibrate (8 nodes)
- [ ] Flash + place 8 nodes (one per room, per P0 map), USB-powered, IoT VLAN.
- [ ] **Calibration recipe per node:** place a beacon at 1 m, record RSSI → set `rssi@1m`; tune `absorption` until ESPresense distance tracks real distance across 1/3/5 m.
- [ ] Add **hysteresis / nearest-node-wins** to prevent room-flapping.
- [ ] **Done when:** a 10-min walk test shows correct room ≥ ~90% of dwell time with no rapid flapping.

### P4 — Identity + fusion
- [ ] Configure all 3 beacons (unique major/minor → person); commit the beacon→person map.
- [ ] HA config (committed): per-person room sensor; **Bayesian `binary_sensor`** per area (BLE-room + WiFi + Zigbee motion from P0); `template` "people home" count. *Append the Bayesian sensors to the existing `binary_sensors.yaml` (already `!include`d) — a second top-level `binary_sensor:` key is rejected by HA. `mqtt:`/`prometheus:` are single keys → straight into `configuration.yaml`.*
- [ ] **Alerts:** per-beacon **battery-low** + per-node **offline** (MQTT LWT / Prometheus `up`).
- [ ] **Done when:** each person resolves to the right room/away; on a known 2-person evening the people-home count reads `2` sustained ≥30 min; Zigbee motion still feeds the Bayesian sensor.

### P5 — Guest count + history + automations
- [ ] Add a **`prometheus:`** block to HA's `configuration.yaml` (or a new `!include`d file + configMapGenerator entry). Serves `/api/prometheus` on the normal **8123** port.
- [ ] In the running HA UI, **mint a long-lived access token** (it's `.storage`, not git) → store as SOPS Secret `homeassistant-prometheus-token` in HA's namespace. *(Ordering: token must exist before the Secret/ServiceMonitor can scrape.)*
- [ ] HA **Service**: add a second named port `metrics` → **same `targetPort: 8123`** (not a new listener).
- [ ] **ServiceMonitor**: copy thermalscope's **label/selector** (`release: kube-prometheus-stack`) but NOT its auth posture (thermalscope is unauthenticated) — set `endpoints[].port: metrics`, `path: /api/prometheus`, `scheme: HTTP`, **`bearerTokenSecret: {name: homeassistant-prometheus-token, key: token}`**. **Backout:** delete the ServiceMonitor → no scrape, HA unaffected.
- [ ] Grafana presence/occupancy/count history dashboard.
- [ ] Guest count: distinct non-beacon devices across nodes, **heavily debounced** (MAC randomization over-counts → trend, not a number).
- [ ] Automations: per-room lights/climate; "everyone left → away mode"; guest-detected notifications.
- [ ] **Done when:** Grafana shows presence history and the guest-count trend tracks actual gatherings.

## Accuracy & gotchas (set expectations)
- BLE = **room-level, not metres**; RSSI is noisy, walls attenuate. Nearest-node + hysteresis is the realistic resolution.
- Guest count is a **fuzzy trend**, not a precise headcount.
- **Beacon battery + node power** are load-bearing — a dead beacon or unplugged node reads as "away" (hence both alerts).
- **Power-supply efficiency > board efficiency** — use decent multi-port USB, not 8 no-name bricks.
- **mosquitto must not stay anonymous** once it has a LAN-routable LB IP reachable from the IoT VLAN.

## Privacy
- All self-hosted (homelab guarantees no cloud).
- Household beacons are **opt-in**; guest count is **anonymous/aggregate** only; be transparent with visitors.

## Artifact locations (exact)
- This plan: `docs/plans/2026-06-21-bluetooth-presence-system.md`.
- mosquitto LB Service + password/ACL SOPS secret + `allow_anonymous` config: `infra/controllers/mosquitto/`.
- zigbee2mqtt creds (same commit as the broker auth flip): `infra/controllers/zigbee2mqtt/`.
- HA mqtt/template/bayesian/prometheus blocks: `apps/base/homeassistant/files/` (in `configuration.yaml` or a new `!include`d file, registered in the configMapGenerator — **no `packages/` dir today**).
- HA ServiceMonitor + `homeassistant-prometheus-token` SOPS secret: `apps/base/homeassistant/`.
- ESPresense node config: documented under `docs/operations/apps/` (firmware-config, not k8s).

## Open questions
- Exact UCGF firewall mechanism for IoT→mosquitto (UniFi rule vs Cilium policy) — confirm during P2.
- HA `.storage` reproduction/backup runbook for the UI-only integrations.
