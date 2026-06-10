# burntbytes

## 1. Overview

`burntbytes` is George's personal blog (burntbytes.com) — a static Hugo + PaperMod
site, self-hosted on the cluster after migrating off GitHub Pages. No backend,
no database; nginx serves the pre-built HTML.

Source: https://github.com/gjcourt/burntbytes · Migration plan:
`docs/plans/2026-06-10-burntbytes-self-host.md`.

## 2. Architecture

Hugo builds the site into `public/` inside a multi-stage Docker image
(`Dockerfile` in the source repo: `hugomods/hugo` extended → `nginxinc/nginx-unprivileged`).
The image is published to `ghcr.io/gjcourt/burntbytes` by the source repo's
`.github/workflows/image.yml` on every push to `master`, tagged
`YYYY-MM-DD` / `YYYY-MM-DD-<sha>` / `latest`.

- Single `Deployment` (**2 replicas**, topology-spread across nodes) in the
  `burntbytes-prod` namespace; nginx listens on 8080.
- **Database / storage**: none. `readOnlyRootFilesystem: true`; emptyDir volumes
  for `/tmp` and `/var/cache/nginx`.
- **Networking**: served at the **apex** `burntbytes.com` via dedicated
  `http-apex` / `https-apex` listeners on `app-gateway-production` (the
  `*.burntbytes.com` wildcard listeners do not match the bare apex). A second
  hostname `burntbytes-origin.burntbytes.com` (covered by the wildcard
  listeners) is kept as a permanent smoke-test endpoint. Both terminate TLS
  with the `burntbytes-prod-wildcard-tls` cert, which SANs both the wildcard
  and the apex.
- **Egress**: DNS only (CiliumNetworkPolicy denies the rest — the static server
  makes no outbound calls).
- **Public path**: Cloudflare proxy → `cloudflared` tunnel → gateway →
  HTTPRoute → Service → nginx. The apex Cloudflare DNS record is a proxied
  CNAME to `<tunnel-uuid>.cfargotunnel.com`.

## 3. URLs

- **Production**: https://burntbytes.com
- **Origin smoke-test**: https://burntbytes-origin.burntbytes.com (same content;
  reachable on-LAN via the wildcard, and off-LAN only if a Cloudflare DNS
  record is added for it)

## 4. Configuration

- **Environment variables**: none at runtime. `baseURL` (`https://burntbytes.com/`)
  is baked into the build from the source repo's `config.yaml`.
- **ConfigMaps/Secrets**: only `ghcr-secret` (image pull credentials, the same
  cluster-age-encrypted secret used by other `gjcourt/*` apps).

## 5. Updating content

1. Edit / add posts under `content/posts/` in the source repo and push to
   `master` (via PR).
2. `image.yml` builds + pushes a new `ghcr.io/gjcourt/burntbytes:YYYY-MM-DD…`
   image.
3. Renovate opens a PR in this repo bumping the image tag in
   `apps/base/burntbytes/deployment.yaml`. Merge it; Flux rolls the deployment.
4. To deploy immediately instead of waiting for Renovate, bump the tag by hand
   in a PR (tags must be strictly increasing — see AGENTS.md).

## 6. Testing

```bash
# Pods up and ready (2/2)
kubectl get pods -n burntbytes-prod

# In-cluster smoke
kubectl -n burntbytes-prod port-forward svc/burntbytes 18080:8080
curl http://localhost:18080/healthz            # → "ok"
curl -I http://localhost:18080/                # 200, blog index

# Through the gateway from the LAN (forces SNI + the gateway IP)
curl -fsSI --resolve burntbytes.com:443:10.42.2.40 https://burntbytes.com/
curl -fsSI --resolve burntbytes-origin.burntbytes.com:443:10.42.2.40 \
  https://burntbytes-origin.burntbytes.com/

# Public, end-to-end (off-LAN)
curl -fsSI https://burntbytes.com/             # server: cloudflare, nginx ETag
curl -s -o /dev/null -w '%{http_code}\n' https://burntbytes.com/intentionally-missing  # 404 (Hugo 404.html)
```

## 7. Monitoring & alerting

- **Metrics**: nginx-unprivileged exposes no `/metrics`; no ServiceMonitor.
- **Logs**: `kubectl logs -n burntbytes-prod deploy/burntbytes`. Low volume.
- **Health probe**: `/healthz` (HTTP) for readiness; TCP-8080 for liveness.

## 8. DNS / TLS notes

- The apex (`burntbytes.com`) needs its **own** gateway listeners and its own
  Cloudflare DNS record — the `*.burntbytes.com` wildcard covers subdomains
  only, not the bare apex.
- LAN clients: an AdGuard rewrite `burntbytes.com → 10.42.2.40` keeps apex
  traffic on-LAN instead of hairpinning out through Cloudflare (optional;
  mirrors the wildcard rewrite used for subdomains).
- TLS is the shared `burntbytes-prod-wildcard-tls` cert
  (`apps/production/certificates/`), which already lists both `*.burntbytes.com`
  and `burntbytes.com` in its `dnsNames`.

## 9. Rollback to GitHub Pages

The legacy `gjcourt/gjcourt.github.io` Pages site is retained as a rollback
parachute. To revert: in the Cloudflare dashboard, restore the apex record to
its pre-cutover value (A/CNAME to GitHub Pages) and purge the cache. Apex TTL is
300s, so propagation is ≤5 min.
