# monitoring — hestia thermals + GPU metrics

Thermal/GPU observability for hestia.

## Services

| File | Purpose |
|------|---------|
| `docker-compose-nvtop.yml` | nvtop — interactive GPU process viewer |
| `docker-compose.yml` | thermalscope — Prometheus thermal/GPU exporter on `:9102` |

## Deployment

Deploy as a TrueNAS Custom App. Paste the YAML into SCALE UI → Apps → Custom App. Subsequent updates to `docker-compose.yml` flow through `.github/workflows/deploy-hestia.yml`.
