---
title: Adding a new app
status: Stable
created: 2026-05-02
updated: 2026-05-02
updated_by: gjcourt
tags: [operations, kustomize, apps]
---

# Adding a new app

1. Create `apps/base/<app>/kustomization.yaml` and the base manifests.
2. Create environment overlays under `apps/staging/<app>/` and/or `apps/production/<app>/`.
3. Add the app to `apps/staging/kustomization.yaml` and/or `apps/production/kustomization.yaml`.
4. Update the auto-generated apps list:
   ```bash
   ./scripts/update-apps-readme.sh
   ```
5. Namespace convention: production uses the plain name (`<app>`); staging uses a `-stage` suffix (`<app>-stage`).
6. Validate before pushing:
   ```bash
   kustomize build apps/staging/<app>
   kustomize build apps/production/<app>
   ```
7. Open a PR against `master`. CI will build into the `staging` branch automatically; Flux will deploy to the `<app>-stage` namespace. Validate there before merging.

## Health probes

Every container needs a `readinessProbe`. A `livenessProbe` is added **only when it carries a distinct signal** from readiness — never share path + timing, because a single flap on a shared probe co-restarts the pod.

Probe-type selection (in order of preference):

1. **HTTP** — `httpGet` on a documented health endpoint (`/health`, `/healthz`, `/ready`, etc.).
2. **TCP** — `tcpSocket` on the service port when the app has no HTTP health endpoint (e.g. mosquitto MQTT on 1883).
3. **Exec** — `exec: command: [...]` for opaque daemons or sidecars without a network listener (e.g. `redis-cli ping`, `pgrep -x <process-name>`).

### Locked timings

```yaml
readinessProbe:
  <httpGet|tcpSocket|exec>: ...
  initialDelaySeconds: 5
  periodSeconds: 10
  timeoutSeconds: 3
  failureThreshold: 3

livenessProbe:
  <httpGet|tcpSocket|exec>: ...
  initialDelaySeconds: 30
  periodSeconds: 20
  timeoutSeconds: 5
  failureThreshold: 6
```

Readiness is tight (catches problems fast, gates traffic). Liveness is loose (only restarts on persistent failure — the cost of a wrong restart is high).

### Startup probe (optional)

Add a `startupProbe` only when the app's cold-start exceeds the liveness `initialDelaySeconds + periodSeconds * failureThreshold` budget — otherwise liveness can kill the pod mid-boot. Pattern:

```yaml
startupProbe:
  httpGet: { path: /health, port: http }
  failureThreshold: 30
  periodSeconds: 10        # ~5 min budget
```

Examples in repo: `apps/base/jellyfin/`, `apps/base/openwebui/`.

### When to skip a probe

Skip rather than add a meaningless probe. Recorded skips (and why):

- **homeassistant**: no anonymous health endpoint; `/api/*` requires a long-lived token (would expand secret blast radius).
- **cert-manager controller** (Helm): leader-elected — readiness on a non-leader replica is semantically meaningless. Upstream chart deliberately doesn't expose the override.
- **CSI sidecars / chart-internal sidecars** (e.g. `config-reloader`, Grafana sc-* sidecars): upstream Helm chart authors omit by design.

If you skip, add a one-line comment in the manifest (or HelmRelease values) explaining why.

### Don't co-restart

If liveness and readiness must hit the same path, distinguish them by timing per the locked values above (readiness tight, liveness loose) — but prefer different probe types (e.g. `httpGet` for readiness, `tcpSocket` for liveness on the same port). See `apps/production/cloudflare-tunnel/deployment.yaml` for the canonical example.

Background: the rationale and the apps that closed the initial probe gaps are tracked in `docs/plans/2026-05-04-phase2-5-completion.md` ("PR A — Health probes").

## Image naming

CI tags images as `YYYY-MM-DD` (first build of the day) then `YYYY-MM-DD-N` for subsequent builds. Image bumps in `apps/{staging,production}/<app>/deployment.yaml` must be strictly greater than the currently deployed tag.

To list published tags:

```bash
gh api /users/gjcourt/packages/container/<app>/versions --jq '.[0].metadata.container.tags[]'
```

## Per-app runbook

After the app is deployed, add an entry under `docs/operations/apps/<app>.md` with the standard runbook structure (overview, architecture, URLs, configuration, usage, monitoring, disaster recovery, troubleshooting). The template is documented in `docs/plans/2026-02-21-documentation-rewrite-plan.md`.
