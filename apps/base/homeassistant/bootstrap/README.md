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
| **Control dashboard** (Overview / Control / Audio-Visual / Lights) | git + mounted (`../files/dashboards/control.yaml`) — YAML-mode, authoritative |
| **Areas** (`core.area_registry.json`) | UI-only → **snapshot here** |
| **Floor Plan / Map** dashboards (`lovelace.floorplan.json`, `lovelace.map.json`) | UI-only (storage-mode) → **snapshot here** |
| **Switchboard** (`lovelace.switchboard.json`) | **retired** — superseded by the git `Lights` view; snapshot kept for history |

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
Control already is) — e.g. Floor Plan when it's revisited. Until then, this snapshot is the
git-tracked record. No secret-bearing `.storage` files (auth, tokens, credentials) are ever
copied here.
