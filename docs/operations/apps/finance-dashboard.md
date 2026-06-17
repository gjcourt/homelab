---
title: finance-dashboard
status: Stable
created: 2026-06-17
updated: 2026-06-17
updated_by: gjcourt
tags: [operations, apps, internal]
---

# finance-dashboard

Internal-only net-worth + IPS-allocation dashboard. Renders the family-office
`positions.yaml` to static HTML and serves it on the LAN. **No public ingress.**

## Overview

| | |
|---|---|
| Image | `ghcr.io/gjcourt/finance-dashboard` (built from `images/finance-dashboard/`, **code-only**) |
| Namespace | `finance-dashboard` (production-only; no staging variant → plain name) |
| Data | `positions.yaml` via SOPS Secret `finance-dashboard-positions`, mounted at `/data` |
| Exposure | ClusterIP only (kubectl port-forward); no Gateway ingress |
| Prices | static/offline in v1 (NetworkPolicy is DNS-egress-only) |

## Architecture

The image carries only `portfolio.py` + `report_html.py` (no financial data). On
pod start, `entrypoint.sh` renders `/data/positions.yaml` → `/srv/html/index.html`
(an emptyDir) and serves it with stdlib `http.server` on `:8080`. The numbers live
only in the SOPS-encrypted Secret and in the running pod's memory.

## Access

```bash
kubectl -n finance-dashboard port-forward svc/finance-dashboard 8080:8080
# open http://localhost:8080
```

## Update the numbers

```bash
sops apps/base/finance-dashboard/secret-positions.yaml   # edit positions.yaml inline
git commit -am "chore: update finance-dashboard positions"
# after Flux applies:
kubectl -n finance-dashboard rollout restart deploy/finance-dashboard   # re-render
```

## Image builds

Push to `master` touching `images/finance-dashboard/**` → `build-finance-dashboard.yml`
→ `ghcr.io/gjcourt/finance-dashboard:YYYY-MM-DD`. Pin that tag in
`apps/base/finance-dashboard/deployment.yaml` (Renovate will bump it thereafter).

## Roadmap (Phase 2)

- Split render (CronJob, `--live` quotes) from serve (Caddy) for auto-refresh +
  history → Grafana, per the portfolio toolkit README.
- Add `cashflow.html` once `cashflow.py` reads its inputs from the Secret rather
  than hardcoded defaults (so comp is never baked into the image).
