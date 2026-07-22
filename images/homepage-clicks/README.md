# homepage-clicks

A tiny click-beacon exporter for the [Homepage](https://gethomepage.dev)
dashboard. Homepage tiles are plain `<a href>` links, so nothing is recorded
when a tile is clicked. This service is the counting half of a lightweight,
self-hosted usage tracker in the same "scope" spirit as
[`mqttscope`](../mqttscope/) / netscope / thermalscope.

## How it works

1. A delegated click handler injected into Homepage's `custom.js`
   (`apps/base/homepage/config/custom.js`) fires
   `navigator.sendBeacon('/api/clicks', '{"service","group"}')` on every tile
   click, deriving the service + group from the tile DOM
   (`.service-name` / `.service-group-name`).
2. The production gateway routes `home.burntbytes.com/api/clicks` (a
   same-origin path match on the Homepage HTTPRoute) to this Deployment.
3. This exporter increments `homepage_tile_clicks_total{service,group}` and
   serves it at `/metrics`; kube-prometheus-stack scrapes it via a
   ServiceMonitor.
4. A Grafana dashboard (`infra/configs/dashboards/homepage-clicks-cm.yaml`)
   renders clicks-over-time, top tiles, and never-clicked tiles.

`/metrics` and `/healthz` are served on the pod port but are **not** exposed
through the gateway — only `POST /api/clicks` is routed publicly.

## Metrics

| metric | type | labels | meaning |
|---|---|---|---|
| `homepage_tile_clicks_total` | counter | `service`, `group` | tile clicks |
| `homepage_beacon_requests_total` | counter | `result` | POSTs by outcome |
| `homepage_beacon_series` | gauge | – | distinct `{service,group}` pairs |

`result` is one of `accepted`, `rejected_origin`, `rejected_ratelimit`,
`rejected_payload`, `rejected_series_cap`.

## Privacy / abuse posture

The Homepage dashboard is **public**, so `/api/clicks` is reachable by anyone
who can reach the gateway. The exporter stores only a service label +
timestamp — **no PII, no IP, no href, no user agent**. Three cheap defences
bound the blast radius of a bad actor:

1. **Origin allowlist** — rejects POSTs whose `Origin` isn't the dashboard's
   own host. A filter, not a security boundary (a non-browser client can forge
   `Origin`); it stops casual cross-site beacons.
2. **Global token-bucket rate limit** — caps sustained + burst intake.
3. **Series cap** — refuses to register a *new* `{service,group}` pair beyond
   `MAX_SERIES`, so a spammer cannot explode Prometheus cardinality. Known
   pairs keep counting.

Label values are also length-capped and charset-restricted before becoming a
metric label.

## Config (env)

| var | default | meaning |
|---|---|---|
| `LISTEN_PORT` | `9107` | metrics + beacon HTTP port |
| `BEACON_PATH` | `/api/clicks` | POST path the gateway forwards here |
| `ALLOWED_ORIGINS` | `https://home.burntbytes.com` | comma-separated allowlist; empty disables |
| `MAX_LABEL_LEN` | `64` | max chars per label value |
| `MAX_SERIES` | `256` | max distinct `{service,group}` pairs |
| `RATE_QPS` | `20` | sustained beacons/sec (token refill) |
| `RATE_BURST` | `40` | token-bucket capacity |
| `MAX_BODY_BYTES` | `4096` | max request body read |

## Local test

```sh
pip install -r requirements.txt
python exporter.py &
curl -s -XPOST -H 'Origin: https://home.burntbytes.com' \
  -d '{"service":"Immich","group":"Media"}' localhost:9107/api/clicks -i
curl -s localhost:9107/metrics | grep homepage_tile_clicks_total
```

## Build

Built + pushed by `.github/workflows/build-homepage-clicks.yml` on changes to
`images/homepage-clicks/**`, tagged `YYYY-MM-DD-<sha7>`. Repin the deployment
image to the emitted `tag@sha256:digest` in a follow-up PR.
