---
title: finance-dashboard
status: Stable
created: 2026-06-17
updated: 2026-10-06
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
| Exposure | LAN-only via gateway — `https://finance.burntbytes.com` (wildcard cert); gateway holds a LAN IP, not tunneled |
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

On the LAN: **https://finance.burntbytes.com** (valid TLS off the `*.burntbytes.com`
wildcard cert). Not tunneled → unreachable off-network. Fallback:
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

## Verifying a deploy

**Fetch with a cache-busting query string, never the bare URL:**

```bash
curl -s "https://finance.burntbytes.com/cashflow.html?cb=$(date +%s)"
```

After a rollout the bare URL keeps returning stale HTML for a while even though the new pod is
already serving the new page (seen 2026-06-17: features showed as missing on bare-URL fetches while
the pod's `last-modified` was current). Append `?cb=<ts>` to every check, for HTML, `style.css` and JS
alike. Even then there is a ~1–2 minute settle window after `rollout status` completes in which
cache-busted fetches can still be stale — re-fetch before trusting a "feature missing" result.

**The authoritative check is the image digest**, not HTTP: compare the pod's
`kubectl -n finance-dashboard get pod -o jsonpath='{.items[0].status.containerStatuses[0].imageID}'`
with the digest the build exported (`gh run view <id> --log | grep 'exporting manifest list'`). If
they match and `git show origin/master:<file>` has the change, the deploy is correct and a stale
response is the cache. That cost about an hour of false "the build raced" diagnosis once.
