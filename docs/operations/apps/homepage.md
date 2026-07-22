# Homepage

## 1. Overview
Homepage is a modern, fully static, fast, secure, fully proxied, highly customizable application dashboard with integrations for over 100 services and translations into multiple languages. It serves as the primary landing page for the homelab, providing quick access to all deployed applications and infrastructure components.

## 2. Architecture
Homepage is deployed as a stateless Kubernetes `Deployment` with a single replica.
- **Storage**: It does not require persistent storage. All configuration is provided via a Kubernetes `ConfigMap`.
- **Service Account**: It uses a dedicated `ServiceAccount` (`homepage`) with a `ClusterRole` to query the Kubernetes API for cluster metrics and node status (used by the Kubernetes widget).
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://home.stage.burntbytes.com
- **Production**: https://home.burntbytes.com

## 4. Configuration
- **Environment Variables**: 
  - `HOMEPAGE_ALLOWED_HOSTS`: Set to `gethomepage.dev` (required by the application).
  - Additional variables can be injected via secrets for widget authentication (e.g., `HOMEPAGE_VAR_SYNOLOGY_USER`).
- **ConfigMaps/Secrets**:
  - `homepage` (ConfigMap): Contains all the YAML configuration files required by Homepage.
    - `settings.yaml`: Global settings, layout, theme, and background image.
    - `services.yaml`: Defines the application links and their associated widgets.
    - `bookmarks.yaml`: Defines static links (e.g., GitHub repos, documentation).
    - `widgets.yaml`: Defines global widgets (e.g., search bar, Kubernetes cluster status).
    - `kubernetes.yaml`, `docker.yaml`, `proxmox.yaml`: Empty files required by Homepage to prevent startup crashes.

### Updating the Dashboard
To add a new service or change the layout, edit the `configmap-patch.yaml` in the respective environment overlay (`apps/production/homepage/configmap-patch.yaml` or `apps/staging/homepage/configmap-patch.yaml`).
The `services.yaml` is often shared or patched depending on the environment.

## 5. Usage Instructions
Navigate to the Homepage URL. The dashboard is read-only. Clicking on a service icon will open that service in a new tab.

## 6. Testing
To verify Homepage is working:
1. Navigate to https://home.burntbytes.com.
2. Verify the page loads and the widgets (e.g., Kubernetes cluster status, Synology NAS status) are displaying data.
3. Verify the `homepage` pod is running: `kubectl get pods -n homepage`

## 7. Monitoring & Alerting
- **Metrics**: Homepage itself exposes no Prometheus metrics. Tile *click* usage is
  instrumented separately by the `homepage-clicks` beacon exporter (see §10), which exposes
  `homepage_tile_clicks_total{service,group}` and is scraped via a `ServiceMonitor`.
- **Logs**: Check the pod logs for configuration errors or widget connection issues:
  ```bash
  kubectl logs -n homepage-prod deploy/homepage
  ```

## 8. Disaster Recovery
- **Backup Strategy**: All configuration is stored declaratively in this Git repository. No data backup is required.
- **Restore Procedure**: Re-apply the Flux Kustomization. The dashboard will be recreated exactly as defined in Git.

## 9. Troubleshooting
- **Widgets Not Loading**: 
  - Check the pod logs for API connection errors.
  - Ensure the target service is reachable from the `homepage` pod.
  - Verify any required authentication credentials (e.g., API keys, usernames/passwords) are correctly injected via environment variables or secrets.
- **Kubernetes Widget Errors**: Ensure the `homepage` ServiceAccount has the correct RBAC permissions to read nodes and pods.
- **Configuration Changes Not Applying**: Homepage hot-reloads configuration changes. If changes don't appear, verify the ConfigMap was updated in the cluster (`kubectl describe cm homepage -n homepage`) and check the pod logs for YAML parsing errors.

## 10. Usage tracking & the measure→reorder loop

The dashboard layout is meant to be **evidence-driven, not guessed**. Which tiles actually get
used is measured, and the layout is re-ordered on that data on a repeating cadence. This closes the
loop set up by the homepage organization plan — Phase 1 tracking (PR #1170) and Phase 2 re-order
(PR #1171).

### How click tracking works (Phase 1)
1. **`custom.js`** (mounted at `/app/config/custom.js`, sourced from `apps/base/homepage/config/`)
   attaches a delegated click listener to tile `<a>` links. On click it fires
   `navigator.sendBeacon()` with `{service, group, href}` — non-blocking, so navigation is unaffected.
2. **`homepage-clicks`** — a small Go beacon exporter (in the `*scope` mold, `apps/base/homepage-clicks/`)
   receives the beacon, increments `homepage_tile_clicks_total{service,group}`, and serves `/metrics`.
3. A **`ServiceMonitor`** has kube-prometheus-stack scrape it; the **`homepage-clicks-dashboard`**
   Grafana ConfigMap renders clicks-over-time, top tiles, and never-clicked tiles.

Privacy: self-hosted, single household, no third party — only a `service`/`group` label + timestamp,
no PII. The dashboard is public, so the beacon accepts only simple same-origin payloads.

Verify the beacon:
```bash
# counter increments after clicking a tile
kubectl -n homepage-prod exec deploy/homepage-clicks -- wget -qO- localhost:8080/metrics | grep homepage_tile_clicks_total
```

### Layout: tabs + within-group order (Phase 2)
- `settings.yaml` assigns each group to a tab via `layout.<group>.tab`: **Home** (Media, Tools) and
  **Admin** (Infrastructure, Monitoring) — this halves first-paint clutter without removing access.
- Within each group, tiles in `services.yaml` are ordered by expected daily-use frequency.
- Rarely-used power-user deep-links (Prometheus, Alertmanager, raw Loki logs) live in the
  **Observability** bookmarks group, not as tiles.

### The loop (Phases 3–4, repeat every ~quarter)
1. **Instrument** — already live (above); new tiles are tracked automatically (generic handler).
2. **Collect** — let 2–4 weeks of real clicks accumulate before drawing conclusions.
3. **Read** — open the Grafana "tile usage" panel: top tiles, click distribution, never-clicked tiles.
4. **Re-order (a config-only PR)** — edit `apps/production/homepage/config/`:
   - promote high-click tiles toward top-left (F-pattern), demote low-click ones;
   - move never-clicked tiles (after a full quarter) to the Observability bookmarks or remove them;
   - retab if a whole group's cadence changed. Run `kustomize build apps/production/homepage` to validate.
5. **Annotate** — add a Grafana annotation at the merge so the next period's before/after is visible.
6. **Measure again** — each re-order is a hypothesis the next period's data confirms or refutes.

Never hand-order by intuition once data exists — let the panel drive it.
