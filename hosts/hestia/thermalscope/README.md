# thermalscope

Hestia thermal + power telemetry agent. Reads `/sys/class/{hwmon,thermal}` and exposes Prometheus metrics over host networking. Already running on hestia as a TrueNAS Custom App — this directory just backfills the canonical compose into git so `deploy-hestia.yml` can manage updates instead of the operator hand-editing the SCALE UI.

| Attribute | Value |
|---|---|
| Image | `ghcr.io/gjcourt/thermalscope` (built from [`gjcourt/thermalscope`](https://github.com/gjcourt/thermalscope), Go + distroless) |
| Tag scheme | git short SHA from the source repo (NOT the homelab YYYY-MM-DD convention — the image is owned by a different repo) |
| Network | `host` (Prometheus scrapes hestia directly) |
| Privileges | `privileged: true` — required to read kernel sysfs sensors |
| Bind mounts | `/sys/class/hwmon`, `/sys/class/thermal`, `/usr/bin/nvidia-smi` (all RO) |

## Provenance

This compose was extracted from the live SCALE Custom App `thermalscope` via:
```bash
ssh truenas_admin@10.42.2.10 'midclt call app.config thermalscope' \
  | jq '.services'
```

After this PR merges, `deploy-hestia.yml` auto-discovers the new directory and any future digest bumps in this compose flow through the workflow.

## Updating

When a new image is published from the [`gjcourt/thermalscope`](https://github.com/gjcourt/thermalscope) repo:

```bash
# Pick the new tag (git short SHA) and fetch its digest:
TAG=<new-short-sha>
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:gjcourt/thermalscope:pull" | jq -r .token)
curl -sI -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json,application/vnd.oci.image.manifest.v1+json" \
  "https://ghcr.io/v2/gjcourt/thermalscope/manifests/$TAG" \
  | grep -i 'docker-content-digest'
```

Then bump the `image:` line in `docker-compose.yml` to `:${TAG}@sha256:${digest}`. Merge the PR; `deploy-hestia.yml` will roll it out via `truenas-update-app.sh`.

## Notes

- `nvidia-smi` bind mount is legacy from the 4090 era. The binary is a no-op now (no GPUs on the box); agent code handles its absence gracefully. Leave the mount in place — removing it requires either the agent to drop the dependency or a coordinated change with [`gjcourt/thermalscope`](https://github.com/gjcourt/thermalscope).
