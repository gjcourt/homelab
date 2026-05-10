# Flashcards

## 1. Overview

Flashcards is a multi-deck spaced-repetition study app using FSRS-4.5 scheduling. It's a static React/TypeScript SPA — entirely client-side, no backend, no database. All scheduling state lives in the user's `localStorage`.

Source: https://github.com/gjcourt/flashcards · Plan: brainstorm/04-009.

## 2. Architecture

Static `dist/` from Vite is baked into a multi-stage Docker image (`Dockerfile` in the source repo) and served by `nginxinc/nginx-unprivileged`. The image contains two builds:

- **Multi-deck** at `/` — full app with deck picker, collections, /manage, /all
- **NATO-locked** at `/nato/` — focused build (`VITE_LOCKED_DECK=nato`) that boots straight into the NATO deck and gates out the multi-deck routes

Single Kubernetes `Deployment` (replica 1) in the `flashcards-prod` (and `flashcards-stage`) namespace; nginx listens on port 8080.

- **Database**: None. The app is local-first — every card's FSRS state is stored in the browser.
- **Storage**: None. `readOnlyRootFilesystem: true`; emptyDir volumes for `/tmp` and `/var/cache/nginx`.
- **Networking**: Cilium `HTTPRoute` on `flashcards.burntbytes.com` → port 8080.
- **Egress**: DNS only (CiliumNetworkPolicy denies everything else — the static-content server has no reason to make outbound calls).

## 3. URLs

- **Staging**: https://flashcards.stage.burntbytes.com
- **Production**: https://flashcards.burntbytes.com (and `/nato/` for the locked variant)

## 4. Configuration

- **Environment variables**: None at runtime. Build-time vars (`BASE_PATH`, `VITE_LOCKED_DECK`) are baked into the image by the source repo's `image.yml` workflow.
- **ConfigMaps/Secrets**: Only `ghcr-secret` (image pull credentials).

## 5. Usage instructions

- Open the URL in a browser.
- Pick a deck from the home page (or a saved collection); review cards with **Space** to flip and **1/2/3/4** to rate Again/Hard/Good/Easy.
- Use `/manage` to create custom collections of decks (e.g. "Interview prep" = NATO + latency + acronyms).
- See the source repo's README for the FSRS explainer and deck JSON format.

## 6. Testing

```bash
# Pod up and ready
kubectl get pods -n flashcards-prod

# Direct in-cluster smoke
kubectl -n flashcards-prod port-forward svc/flashcards 8080:8080
curl http://localhost:8080/healthz             # → "ok"
curl -I http://localhost:8080/                 # 200, multi-deck index
curl -I http://localhost:8080/nato/            # 200, locked index
curl -I http://localhost:8080/decks/financial  # 200, SPA fallback
```

## 7. Monitoring & alerting

- **Metrics**: nginx-unprivileged doesn't expose `/metrics`; no ServiceMonitor.
- **Logs**: `kubectl logs -n flashcards-prod deploy/flashcards`. Static-content server with low log volume.
- **Health probe**: `/healthz` (HTTP) for readiness; TCP-8080 for liveness.

## 8. Disaster recovery

- **Backup strategy**: None. The app is stateless; every user's progress lives in their own browser. Users wanting persistence across browsers should export their `localStorage` manually (no in-app export yet — phase 5 stretch).
- **Restore procedure**: Re-deploy the manifests; users' data is unaffected (it never lived in the cluster).

## 9. Troubleshooting

- **Page doesn't load externally**: check the `HTTPRoute` is bound to `flashcards.burntbytes.com` and Cloudflare Access has an application configured for the hostname.
- **Page doesn't load internally (LAN)**: AdGuard/Pihole wildcard `*.burntbytes.com` should resolve to the cluster gateway IP. Verify with `dig flashcards.burntbytes.com`.
- **`/healthz` 200 but `/` returns 500**: nginx config issue (e.g. missing `root` directive). Check `kubectl logs` for "rewrite or internal redirection cycle".
- **Image pull failure**: `ghcr-secret` is per-namespace and SOPS-encrypted. Re-encrypted by `flux-system-sops` at sync time; if missing, copy from another `gjcourt/*` app's base manifest.

## 10. Image bumps

CI (`.github/workflows/image.yml` in `gjcourt/flashcards`) tags as `YYYY-MM-DD` and `latest` on push to `main`. Renovate updates the digest in `apps/base/flashcards/deployment.yaml` automatically.
