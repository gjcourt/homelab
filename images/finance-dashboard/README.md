# finance-dashboard

Internal-only net-worth + IPS-allocation dashboard. Renders `positions.yaml`
(mounted from a SOPS-encrypted Secret at `/data/positions.yaml`) to a static
HTML report at startup, then serves it on `:8080` via stdlib `http.server`.

**Code-only image** — it carries no financial data. The numbers arrive at
runtime via the mounted Secret, so the image is safe to publish to ghcr.io.
Source scripts mirror `~/src/utility/portfolio/{portfolio.py,report_html.py}`.

- Served internally only (ClusterIP + `kubectl port-forward`); no public ingress.
- Refresh = `kubectl -n finance-dashboard rollout restart deploy/finance-dashboard`
  (re-renders on pod start). A CronJob + `--live` quotes is the Phase-2 upgrade.

## Local test
```bash
docker build -t finance-dashboard images/finance-dashboard
docker run --rm -p 8080:8080 \
  -v /path/to/positions.yaml:/data/positions.yaml \
  finance-dashboard
# open http://localhost:8080
```

## Roadmap (Phase 2)
- Split render (CronJob, `--live`) from serve (Caddy) once auto-refresh is wanted.
- Add a `cashflow.html` once `cashflow.py` reads its inputs from the mounted
  Secret instead of hardcoded defaults (avoids baking comp into the image).
