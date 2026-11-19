# Home Assistant — bootstrap snapshot (UI-managed `.storage`)

Point-in-time snapshots of Home Assistant config that lives only in HA's runtime
registry (`/config/.storage/`) and is **not** otherwise in git — captured so the setup is
reproducible / restorable.

> **These files are NOT live.** The `homeassistant-config` ConfigMap
> (`../kustomization.yaml`) lists its inputs **explicitly** (it does not glob this
> directory), so nothing here is mounted or served. Editing these files does nothing to
> the running HA — they are a reference/backup only.

## Depends on

Home Assistant depends on two controllers in [`infra/controllers/`](../../../../infra/controllers/):

- **mosquitto** (MQTT broker, `infra/controllers/mosquitto/`). The ESPresense `mqtt_room` sensor in [`../files/configuration.yaml`](../files/configuration.yaml) subscribes to the broker to report which room each tracked device is closest to. The Home Assistant network policy allows egress to mosquitto on TCP 1883.
- **zigbee2mqtt** (Zigbee bridge, `infra/controllers/zigbee2mqtt/`). The dependency is indirect. The Home Assistant network policy has no zigbee2mqtt rule; zigbee2mqtt publishes Zigbee devices and their availability to the mosquitto broker, and HA's MQTT integration, which is set up in the UI and so not in git, reads them from there. [`../files/automations.yaml`](../files/automations.yaml) relies on zigbee2mqtt availability for the dining room chandelier LEDs: they go `unavailable` when unpowered, and the automation waits for them to rejoin before sending a light command.

## What's git-managed vs. snapshotted here

| Config | Where it lives |
|---|---|
| `configuration.yaml`, `automations.yaml`, `binary_sensors.yaml` | git + mounted (`../files/`) |
| **Config dashboard** (was "Control"; URL `/home-control`): Overview / Auto-shutoff / Light schedules / Maintenance / Audio-Visual / Lights | git + mounted (`../files/dashboards/control.yaml`) — YAML-mode, authoritative |
| **Home dashboard** (URL `/home-page`, HASS-10): the main page — HA's Overview rebuilt from its own cards plus the Config Overview content | git + mounted (`../files/dashboards/home.yaml`) — YAML-mode, authoritative |
| **Default dashboard** (which page opens first) | UI-only (`.storage/frontend.system_data`, key `core.default_panel`); see below |
| **Areas** (`core.area_registry.json`) | UI-only → **snapshot here** |
| **Floor Plan / Map** dashboards (`lovelace.floorplan.json`, `lovelace.map.json`) | UI-only (storage-mode) → **snapshot here** |
| **Switchboard** (`lovelace.switchboard.json`) | **deleted 2026-10-31** (redundant with the Config dashboard's Lights view); snapshot kept for history |

## Default dashboard (UI-only)

HA has no YAML setting for the default dashboard: core's `frontend` and
`lovelace` config schemas (2026.10.4) have no such key, and the frontend reads
it from `frontend.system_data` → `core.default_panel`, written by the
dashboards page. Without it HA opens its built-in Overview (`/home`). So once,
and again after any restore without `.storage`:

1. Settings → Dashboards.
2. On the **Home** row, open the ⋮ menu → **Set as default** → OK.

This sets it for every user whose Profile → Dashboard is "Auto (use system
settings)"; a per-user choice there wins over it (frontend `getDefaultPanelUrlPath`).
HA's built-in **Overview** stays in the sidebar, for its generated area pages
(`/home/areas-…`).

## HACS components the git dashboards depend on

Not in git: HACS downloads them into `/config/www/community/` on the PVC. Their
Lovelace resource is in git, though: `lovelace.resource_mode: yaml` in
configuration.yaml (HASS-13) means HA reads resources only from
`lovelace.resources` there and ignores `.storage/lovelace_resources`, so HACS
can't register or re-tag them. After HACS installs or updates one, add it or
bump its `?v=` in that list, or browsers keep the cached old file. A fresh
HA, or a restore without `.storage` and `www/community/`, needs them
reinstalled through HACS (HACS → search → Download → reload the browser).

| Component | Used by | Without it |
|---|---|---|
| [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) (`thomasloven/lovelace-auto-entities`, tested v1.16.1) | Config → Maintenance view and its Maintenance intervals subview (HASS-2): every task list | Each list shows HA's "Custom element doesn't exist: auto-entities" card; tasks, reminders and the phone Done action still work |

The vendored cards (Mushroom, card-mod, clock-weather) are different: they are
in git (`../files/www/`) and load through `lovelace.resources` in
configuration.yaml (until HASS-13, `frontend.extra_module_url`).

## Refresh a snapshot

```sh
POD=$(kubectl get pod -n homeassistant-prod -l app=homeassistant -o name | head -1)
kubectl exec -n homeassistant-prod "$POD" -- cat /config/.storage/lovelace.floorplan \
  | python3 -m json.tool --sort-keys > apps/base/homeassistant/bootstrap/lovelace.floorplan.json
```

## Restore / bootstrap onto a fresh HA

- **Dashboards** (`lovelace.*.json`): in HA create the dashboard, open its **raw config
  editor**, and paste the `data.config` block (the `views`). Or restore `/config/.storage/`
  from an HA backup.
- **Areas** (`core.area_registry.json`): recreate under Settings → Areas & Zones, or
  restore `.storage`. Areas are the backbone of the git `Lights`/area-card dashboards and
  the presence work, so keep this current.

## Direction

Migrate these UI-managed dashboards to YAML-mode in `../files/dashboards/` over time (as
the Config dashboard already is) — e.g. Floor Plan when it's revisited. Until then, this snapshot is the
git-tracked record. No secret-bearing `.storage` files (auth, tokens, credentials) are ever
copied here.
