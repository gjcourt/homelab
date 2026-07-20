# Vibrato (ito + leva! espresso controller)

## 1. Overview
Vibrato is a self-hosted, clean-room web app that **monitors and controls an
ito + leva! espresso controller** over its TCP port-23 "MC" serial protocol. It
replaces the two closed vendor apps (Status Monitor / Leva-Companion) with a
homelab-deployed SPA: a live display mirror, a direct-command control surface, a
profile planner, shot history, and an MQTT bridge into Home Assistant.

Source: [github.com/gjcourt/vibrato](https://github.com/gjcourt/vibrato) (private).

## 2. Architecture
Deployed as a single-replica Kubernetes `Deployment` named `vibrato` in the
`vibrato-prod` and `vibrato-stage` namespaces. It is a TypeScript npm-workspaces
monorepo (`@leva/core` codec + ports, `@leva/server` Fastify HTTP/WS, transports,
stores, `@leva/mqtt-bridge`), run with `node --import tsx` (no build step).

- **Machine link**: `@leva/transport-tcp` holds a socket to the ito's esp-link
  bridge at **`10.42.7.11:23`** (an IoT-VLAN device). Two firmware modes:
  `MCr` rich telemetry (10 Hz) and `MC@` virtual-display/LCD (the Monitor tab).
- **Persistence**: shots + profiles via `@leva/store-*` (JSON files by default)
  under the container's data dir. No external DB.
- **HASS bridge**: when `LEVA_MQTT_URL` is set, `@leva/mqtt-bridge` publishes
  retained MQTT discovery configs + a throttled state topic (node = `LEVA_NODE_ID`).
- **Networking**: `CiliumNetworkPolicy` allows gateway ingress on `8080`, and
  egress to DNS, **`world:23`** (the ito, IoT-VLAN), and `mosquitto:1883`. Exposed
  via the Gateway API `HTTPRoute` (prod → `app-gateway-production`, staging →
  `-staging`). Public ghcr image, no pull secret.
- **Health**: `GET /healthz` → `{"status":"ok","node":"...","ito":"connected"}`.
  Readiness probes `/healthz`; liveness is a TCP check.

## 3. URLs
- **Production**: https://vibrato.burntbytes.com
- **Staging**: https://vibrato.stage.burntbytes.com

## 4. Configuration
Env in `apps/base/vibrato/deployment.yaml`; overlays override per environment.

| Var | Value | Notes |
|---|---|---|
| `LEVA_HOST` | `10.42.7.11` | The **bench** ito. Repoint to the installed machine's ito later. |
| `LEVA_PORT` | `23` | esp-link telnet bridge. |
| `LEVA_MQTT_URL` | `mqtt://mosquitto.mosquitto.svc:1883` | Anonymous. HASS bridge. |
| `LEVA_NODE_ID` | `espresso` (prod) / `vibrato-stage` (staging) | HASS node + `/healthz` label. |
| `LEVA_TRANSPORT` | **`sim`** on staging only | Staging runs the **simulator** — see the staging rule below. |

**Staging MUST NOT share the physical ito.** The ito serves a single telemetry
stream; a second client (staging's watchdog + re-handshake) re-grabs it on a cycle
and **starves prod**, and a staging preview could send real commands. Staging is
therefore pinned to `LEVA_TRANSPORT=sim` (canned frames); **prod owns the ito
exclusively**. Never point a staging/preview overlay at the real machine.

## 5. Deployment
GitOps via Flux (reconciling `master`).

1. Merge to `vibrato` `main` → CI builds + pushes `ghcr.io/gjcourt/vibrato`
   tagged `<full-sha>` + date + `latest`.
2. Bump **both** `apps/{staging,production}/vibrato/deployment-patch.yaml` to the
   new full-sha tag (plain image bump, no immutable fields). Base pins `:latest`;
   overlays pin the sha (avoids the `IfNotPresent` cache trap).
3. Open a homelab PR off `origin/master`, get CI green, squash-merge.
4. Reconcile:
   ```bash
   flux reconcile source git flux-system
   flux reconcile kustomization apps-production --with-source
   flux reconcile kustomization apps-staging
   kubectl -n vibrato-prod rollout status deploy/vibrato
   ```
5. **Cache-bust the browser**: after a UI change, hard-reload (⌘⇧R) — the browser
   serves the old asset otherwise. Verify with `curl .../app.js?cb=$(date +%s)`.

## 6. Usage — the tabs
- **Brew** — target-curve chart + a dose/yield/ratio/temp planner; Start-Shot.
- **Profiles** — pressure-profile editor (a **local planner**: the firmware has no
  curve-write command; profile _select_ sends `MCcPROFILE n`).
- **History** — saved shots with ratings, ratio, replay.
- **Control** — direct-command card grid (machine / brew / tuning / profiles) +
  a read-only `MCu` config viewer. Commands go over the WS `/api/stream`; the
  server enforces an `MCC_FORBIDDEN` deny-list (restart / contact-only).
- **Monitor** — a live amber-OLED mirror of the ito's 4×16 display + a
  keyboard/mouse/touch control bar (Back · Up/Down · a context primary that reads
  **Menu** on the home screen / **Select** in a menu). Switches the machine to
  `MC@` LCD mode while open; reverts to `MCr` on close.

## 7. Troubleshooting
- **Monitor blank while `/healthz` says `ito:connected`** — the machine is idle
  (deep standby streams zero frames) or the LCD mode didn't take. The tab sends
  `MCa` (wake) + `mode:lcd` on mount; a deep-standby unit may need a **physical
  button press** to wake. Probe the WS (`wss://…/api/stream`) — snapshot-only in
  ≥7 s = stale, frames climbing = live.
- **`ERR:TSTAT` on the display** — a **config** fault, not hardware: a PID's
  Control strategy got set to `2-P` (thermostat) with no thermostat contact. Fix:
  **Setup → PID 1 → Control → OFF** (the sensorless bench wants `OFF`). Easiest via
  Leva-Companion's `set_menu_field` closed-loop writer; `Auto save` is on so it
  persists.
- **Setpoint won't nudge (`MC+`/`MC-`)** — relative nudges are ignored while
  `ERR:TSTAT` is active; only absolute `MCc` commands get through. Clear the fault
  first, then nudge.
- **Staging re-grabbing the ito / prod Monitor blank** — staging isn't on `sim`.
  Confirm `LEVA_TRANSPORT=sim` on the staging overlay.
- **Config submenus render but deep-editing doesn't stick** — config submenus only
  render in `MC@` mode; there is **no** general write path — numeric config is
  menu-nav-only by firmware design (not a bug).

## 8. Known limitations / backlog
- **Live numeric telemetry is not decoded** — `parseRichFrame` extracts only
  `state`; pressure/flow/temp/weight offsets are **unpinned** because the bench ito
  has no sensors. Needs an **on-machine sensored capture** (pull a shot, read the
  columns off `core/src/protocol/CAPTURE.md`, fill the offset map), plus repointing
  `LEVA_HOST` to the installed machine.
- **No profile-curve upload / config write** — firmware exposes no write command;
  the Profiles editor is a local planner and Control config is read-only.
- **Stale HASS `bench` test device** — early testing left retained MQTT discovery
  configs (`homeassistant/sensor/bench/*/config`, `vibrato/bench/status`) on the
  broker; clear them if the ghost device reappears in Home Assistant.
