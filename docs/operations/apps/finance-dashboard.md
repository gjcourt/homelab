---
title: finance-dashboard
status: Stable
created: 2026-06-17
updated: 2026-06-18
updated_by: gjcourt
tags: [operations, apps, internal]
---

# finance-dashboard

Internal-only personal-finance **site** — four static pages rendered from
encrypted-YAML data and served on the LAN. **No public ingress.**

| Page | URL | Renderer ← data |
|---|---|---|
| Balance sheet | `/` (index.html) | `report_html.py` ← `positions.yaml` |
| Cash flow | `/cashflow.html` | `cashflow.py` ← `cashflow.yaml` |
| Real estate (STR) | `/realestate.html` | `realestate.py` ← `str.yaml` + `candidates.yaml` |
| Runway | `/runway.html` | `runway.py` ← `runway.yaml` |

## Overview

| | |
|---|---|
| Image | `ghcr.io/gjcourt/finance-dashboard` (built from `images/finance-dashboard/`, **code-only**) |
| Namespace | `finance-dashboard` (production-only; plain name) |
| Data | one SOPS Secret `finance-dashboard-data` (5 YAML keys) mounted at `/data` |
| Exposure | **Port-forward only** while HOMELAB-90 is open (the HTTPRoute is not deployed). Was LAN-only via the gateway at `https://finance.burntbytes.com` |
| Interactivity | Real-estate + runway pages use client-side JS (sliders, Monte Carlo, Chart.js vendored locally) — no backend |

## Architecture

The image carries only the renderers + shared `webcommon.py`/`style.css` + a
vendored `chart.min.js` (no financial data). On pod start, `entrypoint.sh`
renders all four pages from `/data/*.yaml` → `/srv/html/` (an emptyDir), copies
the static assets, and serves with stdlib `http.server` on `:8080`. The numbers
live only in the SOPS-encrypted Secret and the running pod. All charts/sliders
run in the **browser** — the pod makes no external calls (NetworkPolicy egress is
DNS-only).

## Access

**Port-forward only, for now.** The site has no login of its own, and the gateway's
Authelia check is not enforced (HOMELAB-90; audited 2026-11-03, the route served the
whole balance sheet to anyone on the LAN). The HTTPRoute stays out of
`kustomization.yaml` until that is fixed, then comes back at
`https://finance.burntbytes.com`.
```bash
kubectl -n finance-dashboard port-forward svc/finance-dashboard 8080:8080  # → localhost:8080
```

## Update the data (no image rebuild)

Edit the source YAMLs in `~/src/private/portfolio/` (`positions.yaml`,
`cashflow.yaml`, `str.yaml`, `runway.yaml`; for candidates run
`redfin_filter.py --emit-yaml candidates.yaml` from a Redfin export), then:
```bash
cd ~/src/homelab           # (or a worktree)
scripts/update-finance-data.sh      # rebuilds + SOPS-encrypts secret-finance-data.yaml
git add apps/base/finance-dashboard/secret-finance-data.yaml && git commit -m "chore: update finance data"
# open a PR; after merge:
kubectl -n finance-dashboard rollout restart deploy/finance-dashboard   # re-render
```

## Image builds

Push to `master` touching `images/finance-dashboard/**` → `build-finance-dashboard.yml`
→ `ghcr.io/gjcourt/finance-dashboard:YYYY-MM-DD-<sha7>`. Pin that tag in
`apps/base/finance-dashboard/deployment.yaml` (Renovate bumps it thereafter).
Only **code/layout** changes need a rebuild; data changes don't (data is mounted).
