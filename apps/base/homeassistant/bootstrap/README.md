# Home Assistant — bootstrap snapshot (UI-managed `.storage`)

Point-in-time snapshots of Home Assistant config that lives only in HA's runtime
registry (`/config/.storage/`) and is **not** otherwise in git — captured so the setup is
reproducible / restorable.

> **These files are NOT live.** The `homeassistant-config` ConfigMap
> (`../kustomization.yaml`) lists its inputs **explicitly** (it does not glob this
> directory), so nothing here is mounted or served. Editing these files does nothing to
> the running HA — they are a reference/backup only.

## What's git-managed vs. snapshotted here

| Config | Where it lives |
|---|---|
| `configuration.yaml`, `automations.yaml`, `binary_sensors.yaml` | git + mounted (`../files/`) |
| **House dashboard** (was "Control"; URL `/home-control`): Overview / Auto-shutoff / Light schedules / Maintenance / Audio-Visual / Lights | git + mounted (`../files/dashboards/control.yaml`) — YAML-mode, authoritative |
| **Areas** (`core.area_registry.json`) | UI-only → **snapshot here** |
| **Floor Plan / Map** dashboards (`lovelace.floorplan.json`, `lovelace.map.json`) | UI-only (storage-mode) → **snapshot here** |
| **Switchboard** (`lovelace.switchboard.json`) | **retired** — superseded by the git `Lights` view; snapshot kept for history |

## HACS components the git dashboards depend on

Not in git: HACS downloads them into `/config/www/community/` on the PVC and
registers them as Lovelace resources in `.storage/lovelace_resources`. A fresh
HA, or a restore without `.storage` and `www/community/`, needs them
reinstalled through HACS (HACS → search → Download → reload the browser).

| Component | Used by | Without it |
|---|---|---|
| [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) (`thomasloven/lovelace-auto-entities`, tested v1.16.1) | House → Maintenance view and its Maintenance intervals subview (HASS-2): every task list | Each list shows HA's "Custom element doesn't exist: auto-entities" card; tasks, reminders and the phone Done action still work |

The vendored cards (Mushroom, card-mod, clock-weather) are different: they are
in git (`../files/www/`) and load through `frontend.extra_module_url`.

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
the House dashboard already is) — e.g. Floor Plan when it's revisited. Until then, this snapshot is the
git-tracked record. No secret-bearing `.storage` files (auth, tokens, credentials) are ever
copied here.
