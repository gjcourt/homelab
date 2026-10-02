# cadence-site

## 1. Overview

The Cadence marketing page: one static page served by nginx. Source and image live in
`gjcourt/cadence` under `site/` (see its `site/README.md`); this repo only deploys it.

**Status (2026-10-01): staging only.** Production waits until the page's `[CONTACT EMAIL]`
and `[COMPANY NAME]` placeholders are filled in upstream.

## 2. Architecture

- `ghcr.io/gjcourt/cadence-site:<YYYY-MM-DD>-<sha7>`, built by cadence's `site.yml` on push to
  `main`. nginx-unprivileged (uid 101) on :8080, `/healthz` for probes. Fonts are self-hosted,
  so the page makes no third-party requests; a strict same-origin CSP is set in the image's
  `nginx.conf`.
- Private ghcr package, pulled with the shared `ghcr-secret` (`secret-ghcr.yaml` in the base is a
  verbatim copy of the encrypted file the other gjcourt apps use; kustomize sets the namespace).
- Base in `apps/base/cadence-site/` (copied from `burntbytes`): Deployment, Service,
  ServiceAccount (no token), CiliumNetworkPolicy (gateway ingress on 8080, DNS egress only),
  PDB. One replica.
- No storage, no database; the only secret is the image pull secret.

## 3. URLs

- **Staging:** https://cadence.stage.burntbytes.com (LAN only — `*.stage` isn't in the
  Cloudflare tunnel).
- **Production (not yet):** https://cadence.burntbytes.com. The tunnel already has an entry for
  this hostname (`apps/production/cloudflare-tunnel/config.yaml`), left from the Cadence
  Shopify app work (#1262); point it at this site's service when production lands. When the
  Shopify app also deploys, its `/pay`, `/app`, `/auth` and `/webhooks` paths route to the app
  on the same host.

## 4. Configuration

None at runtime. Copy and layout change in `gjcourt/cadence` `site/`.

## 5. Updating the page

1. Merge the change to `site/` in `gjcourt/cadence`; `site.yml` publishes a new
   `ghcr.io/gjcourt/cadence-site:<date>-<sha7>`.
2. Bump the tag and digest in `apps/base/cadence-site/deployment.yaml` here (Renovate's
   date-sha rule, #1523, also proposes these).

## 6. Testing

```bash
kubectl -n cadence-site-stage get pods                       # 1/1 Running
kubectl -n cadence-site-stage exec deploy/cadence-site -- wget -qO- localhost:8080/healthz
curl -sI https://cadence.stage.burntbytes.com/ | grep -i content-security-policy
```

## 7. Monitoring & alerting

Covered by the cluster-wide pod and probe alerts; nothing app-specific.

## 8. Rollback

Revert the tag bump in `apps/base/cadence-site/deployment.yaml`.
