# Prometheus + Grafana wiring — P5 (REFERENCE / DRAFT)

> **Status: reference / draft.** These snippets are lifted into the cluster
> during **P5** of the [Bluetooth-presence build](../../../plans/2026-06-21-bluetooth-presence-system.md).
> Nothing here is applied yet. The dashboard JSON referenced at the bottom
> ([`grafana-presence-dashboard.json`](grafana-presence-dashboard.json)) shows
> "no data" until these are live and HA has scraped a few cycles — expected.

## What P5 wires

```
HA `prometheus:` block on :8123/api/prometheus
        │  (bearer-token auth — HA Prometheus endpoint requires it)
        ▼
HA Service: 2nd named port `metrics` → targetPort 8123
        │
        ▼
ServiceMonitor (release: kube-prometheus-stack)  ── bearerTokenSecret ──┐
        │  path: /api/prometheus                                        │
        ▼                                                  homeassistant-prometheus-token
Prometheus (kube-prometheus-stack) ──► Grafana dashboards
```

> **Key difference from thermalscope:** copy thermalscope's
> `release: kube-prometheus-stack` selector label (mandatory — without it the
> ServiceMonitor is invisible to the kube-prometheus-stack Prometheus), but
> **NOT** its auth posture. thermalscope's `/metrics` is **unauthenticated**;
> HA's `/api/prometheus` **requires a bearer token**, so this ServiceMonitor
> adds `bearerTokenSecret` + `path: /api/prometheus`.

## Step 1 — HA `prometheus:` block

`prometheus:` is a single top-level key. Add it to
`apps/base/homeassistant/files/configuration.yaml` (or an `!include`d file
registered in the `configMapGenerator`). It serves on the normal **8123** port
at `/api/prometheus` — **no new listener**.

```yaml
# configuration.yaml (P5 addition)
prometheus:
  namespace: hass            # metric prefix -> hass_* (matches the dashboard)
  # Trim cardinality: only export the presence/occupancy entities we chart.
  # Widen later if you want full HA metrics.
  filter:
    include_entities:
      - sensor.people_home
      - binary_sensor.anyone_home
      - sensor.george_room
      - sensor.mara_room
      - sensor.niccolo_room
      - binary_sensor.living_occupied
      - binary_sensor.dining_occupied
      - binary_sensor.foyer_occupied
      - binary_sensor.office_occupied
      - binary_sensor.master_occupied
      - binary_sensor.son_occupied
      - binary_sensor.guest_occupied
      - binary_sensor.kitchen_occupied
      - sensor.guests_estimated     # P5 guest-count trend (debounced)
```

> The HA Prometheus integration requires a **long-lived access token** on the
> scrape request. Minting it is a **UI / `.storage`** action (Step 4), not
> committable YAML.

## Step 2 — HA Service: add a `metrics` port

Add a second named port to `apps/base/homeassistant/service.yaml`. Same
`targetPort: 8123` — the metrics endpoint is on the existing HTTP listener, this
just gives the ServiceMonitor a named port to select.

```yaml
# apps/base/homeassistant/service.yaml (P5 — add the metrics port)
apiVersion: v1
kind: Service
metadata:
  name: homeassistant
  namespace: homeassistant
  labels:
    app: homeassistant
spec:
  type: ClusterIP
  ports:
    - name: http
      port: 8123
      protocol: TCP
      targetPort: 8123
    - name: metrics        # <-- added at P5
      port: 8123           # same backend; distinct named port for the SM
      protocol: TCP
      targetPort: 8123
```

> Two ports sharing `port: 8123` is fine for a ServiceMonitor (it selects by
> port **name**, not number). If a strict same-port validation ever complains,
> give `metrics` a distinct service `port` (e.g. `8124`) while keeping
> `targetPort: 8123`.

## Step 3 — the bearer-token SOPS Secret

Create `homeassistant-prometheus-token` in the `homeassistant` namespace,
SOPS-encrypted, in `apps/base/homeassistant/`. **SOPS edits are operator-only** —
ship a `.yaml.example` template and let the operator encrypt the real value.

```yaml
# apps/base/homeassistant/homeassistant-prometheus-token.sops.yaml.example
apiVersion: v1
kind: Secret
metadata:
  name: homeassistant-prometheus-token
  namespace: homeassistant
type: Opaque
stringData:
  # The HA long-lived access token minted in Step 4. Encrypt with SOPS before
  # commit (key ref: .sops.yaml). NEVER commit the plaintext token.
  token: "<HA_LONG_LIVED_ACCESS_TOKEN>"
```

## Step 4 — mint the long-lived token (UI / `.storage`)

In the running HA UI: profile → Security → **Long-lived access tokens** →
Create. This token lives in HA's `.storage` (captured by the PVC backup), **not
git**. Paste it into the SOPS secret (Step 3). **Ordering: the token must exist
before the ServiceMonitor can scrape** — mint it first.

## Step 5 — the ServiceMonitor

Add `apps/base/homeassistant/servicemonitor.yaml`. Copies thermalscope's
mandatory `release: kube-prometheus-stack` selector label; adds the HA-specific
bearer-token auth + `/api/prometheus` path.

```yaml
# apps/base/homeassistant/servicemonitor.yaml (P5)
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: homeassistant
  namespace: homeassistant
  labels:
    # MANDATORY — kube-prometheus-stack selects ServiceMonitors by this label
    # (serviceMonitorSelectorNilUsesHelmValues=true). Without it the SM is
    # invisible. (Same rule as apps/base/thermalscope/servicemonitor.yaml.)
    release: kube-prometheus-stack
    app.kubernetes.io/name: homeassistant
    app.kubernetes.io/component: web
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: homeassistant
      app.kubernetes.io/component: web
  # No namespaceSelector — defaults to the SM's own namespace.
  endpoints:
    - port: metrics                 # the named port added in Step 2
      path: /api/prometheus         # HA's Prometheus endpoint (NOT /metrics)
      scheme: HTTP
      interval: 30s
      scrapeTimeout: 10s
      # HA REQUIRES auth on this endpoint — thermalscope does NOT. This is the
      # one place we diverge from thermalscope's posture.
      bearerTokenSecret:
        name: homeassistant-prometheus-token
        key: token
      relabelings:
        # Stable instance label so the HA target is identifiable in Grafana.
        - targetLabel: instance
          replacement: homeassistant
        - targetLabel: job
          replacement: homeassistant
```

## Step 6 — Grafana dashboard

Import [`grafana-presence-dashboard.json`](grafana-presence-dashboard.json)
(Grafana → Dashboards → Import, or via the Grafana provisioning sidecar config
map if you manage dashboards as code). It uses the `hass_*` metric names the
`prometheus:` `namespace: hass` produces:

| Metric | From entity |
|---|---|
| `hass_sensor_unit_people{entity="sensor.people_home"}` | `sensor.people_home` |
| `hass_binary_sensor_state{entity="binary_sensor.<room>_occupied"}` | the 8 Bayesian sensors |
| `hass_sensor_state{entity="sensor.<person>_room"}` | per-person room (string → use as a table/state-timeline) |
| `hass_sensor_unit_people{entity="sensor.guests_estimated"}` | P5 guest-count trend |

> Exact HA metric names/labels depend on the entity's `unit_of_measurement` and
> device_class (HA's Prometheus integration derives the metric family from
> them). Confirm the emitted series names with `curl -H "Authorization: Bearer
> <token>" http://<ha>:8123/api/prometheus | grep hass_` once Step 1 is live,
> and adjust the dashboard queries if they differ.

## Backout

Delete the ServiceMonitor → no scrape, HA unaffected. Removing the
`prometheus:` block + `metrics` port reverts HA to no metrics endpoint.
