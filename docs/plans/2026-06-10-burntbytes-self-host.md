---
status: in-progress
last_modified: 2026-06-10
summary: "Self-host the burntbytes.com blog; hidden-origin live, apex + Cloudflare swing pending"
---

# Self-host burntbytes.com on the Talos homelab

## Context

`burntbytes.com` is a Hugo + PaperMod blog (`gjcourt/burntbytes`, 4 published
posts) previously published to `gjcourt/gjcourt.github.io` via a manual
`hugo && git push` and served by GitHub Pages behind Cloudflare proxy. We're
moving the origin into the `melodic-muse` cluster to retire the manual deploy
and route apex traffic through the same path as every `*.burntbytes.com`
subdomain. Pattern cloned: `apps/base/flashcards/` (static site → nginx-unprivileged
image → ghcr → Renovate-bumped tag → Flux). Exposure path: Cloudflare proxy →
`cloudflared` tunnel → `cilium-gateway-app-gateway-production` → HTTPRoute →
`burntbytes` Service → nginx pod.

## Sequencing (refined from the original draft for lower blast radius)

The original draft bundled the apex gateway listeners into P2. During execution
we found Cloudflare uses **per-subdomain** DNS records (no `*.burntbytes.com`
CNAME to the tunnel), and that the hidden hostname `burntbytes-origin.burntbytes.com`
is already covered by the existing wildcard cert + wildcard gateway listeners +
AdGuard wildcard rewrite. So:

- **P2 touches zero shared infra** — app + an HTTPRoute on the hidden hostname,
  validated on-LAN via `curl --resolve` to the gateway IP. The tunnel hop is
  app-agnostic and already proven for every other subdomain.
- **P3 isolates the one risky change** — apex gateway listeners + apex hostname
  + apex tunnel entry + operator AdGuard apex rewrite. A bad apex listener can
  only be a single isolated revert (it cannot ride along with the app deploy).

```
P0 (operator)   Lower apex Cloudflare TTL; capture tunnel UUID + original apex records
P1 (source)     ✅ DONE — Dockerfile + nginx.conf + image.yml → ghcr image
P2 (homelab)    App + hidden-hostname HTTPRoute + hidden tunnel entry (no gateway change)
P3 (homelab)    Apex gateway listeners + apex hostname + apex tunnel entry (dark)
P4 (Cloudflare) Apex DNS swing A → CNAME cfargotunnel (operator)
P5 (cleanup)    Decommission Pages publish; runbook; plan → complete
```

## P1 — source repo (DONE)

`gjcourt/burntbytes` PR #1 (merged). Multi-stage `hugomods/hugo:0.163.0` →
`nginxinc/nginx-unprivileged:1.27-alpine` on :8080; `nginx.conf` with gzip +
fingerprinted-asset immutable cache + short HTML revalidate + Hugo 404 wiring;
`image.yml` builds to `ghcr.io/gjcourt/burntbytes:{date, date-sha, latest}` and
inits only the theme submodule (the `public/` submodule is an SSH Pages URL).

Two latent build fixes surfaced on modern Hugo (0.163):

- `config.yaml`: restored pre-0.158 `security.allowContent` so the raw-HTML
  `labs/beerbuilder` lab builds.
- `network-streamers.md`: `{{< fig >}}` → `{{< figure >}}` (the `fig` shortcode
  never existed; this published post had never built).

Published image: `ghcr.io/gjcourt/burntbytes:2026-06-10-29cc91a`
(`sha256:d78b6f5be213a9f5816ef0fe691203f473214186346a3fe036652f92c08fc45f`).

## P2 — homelab app on the hidden hostname (this PR)

New: `apps/base/burntbytes/` (clone of flashcards — 2 replicas, PDB
`maxUnavailable: 1`, topology spread, read-only rootfs, `/healthz` readiness,
TCP liveness; portable SOPS ghcr-secret copied verbatim) and
`apps/production/burntbytes/` (HTTPRoute on `burntbytes-origin.burntbytes.com`).
Edits: wire `burntbytes` into `apps/production/kustomization.yaml`; add the
hidden-host tunnel ingress entry. **No gateway / AdGuard / Cloudflare change.**

Verification (post-merge):
```bash
flux reconcile kustomization apps-production -n flux-system
kubectl -n burntbytes-prod get pods            # 2/2 Ready
kubectl -n burntbytes-prod port-forward svc/burntbytes 18080:8080 &
curl -fsS http://localhost:18080/healthz       # ok
# Full path via the gateway (LAN), using the wildcard cert + listener:
curl -fsSI --resolve burntbytes-origin.burntbytes.com:443:10.42.2.40 \
  https://burntbytes-origin.burntbytes.com/
curl -fsS  --resolve burntbytes-origin.burntbytes.com:443:10.42.2.40 \
  https://burntbytes-origin.burntbytes.com/ | grep -i 'burnt\|papermod'
```

Rollback: revert PR; Flux removes the workload. Apex still on Pages.

## P3 — apex activation (dark)

- `infra/configs/gateway/gateway-production.yaml`: add `http-apex` + `https-apex`
  listeners (hostname `burntbytes.com`, reusing `burntbytes-prod-wildcard-tls`
  which already SANs the apex).
- `apps/production/burntbytes/httproute.yaml`: add `burntbytes.com` to hostnames.
- tunnel configmap: add `burntbytes.com` ingress entry.
- **Operator**: AdGuard rewrite `burntbytes.com → 10.42.2.40` (wildcard doesn't
  cover the bare apex).

Verify on-LAN (`curl --resolve burntbytes.com:443:10.42.2.40`); off-LAN still
serves Pages (DNS unchanged). If a new apex listener disrupts `*.burntbytes.com`,
revert immediately (known-unknown: Cilium per-hostname listener coexistence).

## P4 — Cloudflare DNS swing (operator)

Lower apex TTL ahead of time (P0). Replace apex A/AAAA with a proxied CNAME `@`
→ `<tunnel-uuid>.cfargotunnel.com`; purge cache. Verify off-LAN serves the
self-hosted build (nginx ETag, not Pages) and `/404` renders Hugo's 404.
Rollback: restore original apex A records + purge (≤5 min, TTL lowered).

## P5 — cleanup

Source PR: remove the Pages publish flow + README. Homelab PR: add
`docs/operations/apps/burntbytes.md`; flip this plan to `status: complete`.
Leave `gjcourt.github.io` intact 30 days as a rollback parachute.

## Out of scope

Analytics, comments, RSS validation, image pipeline, search, staging overlay,
self-hosted CI runner, cluster-edge CDN, sitemap submission.
