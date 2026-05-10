---
name: "Homelab Monitoring & Healthcheck Enhancement"
date: "2026-05-09"
status: "draft"
author: "George Courtsunis + Hermes"
---

# Homelab Monitoring & Healthcheck Enhancement

## Overview

Extend the existing kube-prometheus-stack monitoring to provide full observability across all 21 production apps, add a healthcheck Deployment for cluster-level synthetic metrics, and configure Alertmanager-to-Signal routing for on-call notifications. The current stack is well-architected but has two gaps: 18 apps lack ServiceMonitors (invisible to Prometheus), and there are no cluster healthcheck metrics or Signal alert routing beyond what Hermes uses.

This plan covers ServiceMonitors for all apps, a healthcheck Deployment, Alertmanager Signal routing, and new Grafana dashboards — all delivered via Flux GitOps.

**Why now:** The homelab has grown to 21 production apps. Without ServiceMonitors, most apps are invisible to Prometheus — CrashLoopBackOff on a non-monitored app would only be caught by the generic K8s alert (which fires on any namespace). The healthcheck metrics will give a single-pane view of cluster synthetic health.

## Requirements

### Functional
- All 21 production apps are scraped by Prometheus via ServiceMonitors
- Healthcheck metrics are exposed and visible in Grafana
- Critical alerts (node NotReady, CrashLoopBackOff, PVC > 90%, Flux reconciliation failure, BGP session down) route to Signal
- Warning alerts (CPU > 90%, OOMKilled, DaemonSet not ready, CNPG degraded) log to Loki and show in Grafana
- Informational alerts (weekly health digest) are emailed

### Non-functional
- All changes via Flux GitOps — no manual `kubectl`
- High-priority apps scraped at 15s, normal apps at 30s
- Healthcheck Deployment runs lightweight Python script, exposes `/metrics` on port 9100
- Signal integration reuses existing signal-bridge HTTP API (port 8080)
- New ServiceMonitors follow the existing pattern (`apps/base/<app>/servicemonitor.yaml`)
- New Grafana dashboards follow the existing pattern (`infra/configs/dashboards/<name>-cm.yaml`)
- PrometheusRules follow the existing pattern (`infra/configs/alerts/prometheus-rules.yaml`)

### Priority tiers for scrape intervals
- **High (15s):** openwebui, homeassistant, hermes, signal-cli, golinks, immich, jellyfin
- **Normal (30s):** adguard, audiobookshelf, authelia, excalidraw, hermes-callee, homepage, linkding, mealie, memos, navidrome, overture, snapcast, vitals, synology-iscsi-monitor

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Flux Kustomization                         │
│  infra/ → controllers/kube-prometheus-stack/ (HelmRelease)   │
│  infra/ → configs/servicemonitors/ (new: SM CRs)             │
│  infra/ → configs/dashboards/ (new dashboard ConfigMaps)     │
│  apps/production/ → base/*/servicemonitor.yaml (new)         │
└──────────────────┬───────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────┐
│                      Cluster                                 │
│                                                              │
│  ┌─────────────┐   ┌─────────────────┐   ┌───────────────┐  │
│  │ Healthcheck  │   │  kube-prometheus│   │  App Pods     │  │
│  │ Deployment   │──▶│  -stack         │   │  (21 apps)    │  │
│  │              │   │  Prometheus     │   │               │  │
│  │ /metrics     │   │  Grafana        │   │ ServiceMonitor│  │
│  │ :9100        │   │  Alertmanager   │   │  (15s/30s)    │  │
│  └─────────────┘   │                 │   └───────┬───────┘  │
│                    └────────┬────────┘           │          │
│                             │                    │          │
│                    ┌────────▼────────┐   ┌───────▼───────┐  │
│                    │  Loki           │   │  signal-bridge│  │
│                    │  Promtail       │   │  :8080        │  │
│                    └─────────────────┘   │  /v1/send     │  │
│                                          └───────┬───────┘  │
│                                                  │          │
└──────────────────────────────────────────────────┼──────────┘
                                                   │
                                          ┌──────▼───────┐
                                          │  signal-cli   │
                                          │  (JSON-RPC)   │
                                          │  :7583        │
                                          └──────────────┘
```

### Data flow
1. **Scrape:** Prometheus scrapes all 21 apps via ServiceMonitors + healthcheck Deployment + built-in exporters (node-exporter, kube-state-metrics, cAdvisor)
2. **Rules:** PrometheusRule alerts fire on thresholds (existing custom rules + new healthcheck rules)
3. **Alertmanager:** Routes alerts to receivers based on severity (Signal for critical, Loki for warning, null for info)
4. **Signal bridge:** Alertmanager HTTP webhook → signal-bridge → signal-cli → Signal message
5. **Dashboards:** Grafana visualizes metrics from Prometheus + healthcheck metrics

## Implementation Plan

### Phase 1: ServiceMonitors for all 18 apps without them

**Goal:** Create ServiceMonitors for all 21 production apps. Three already exist (authelia, signal-cli, synology-iscsi-monitor). 18 need to be created.

**Pattern:** Follow existing convention — each `apps/base/<app>/servicemonitor.yaml` is referenced by the production Kustomization via `apps/base/<app>/kustomization.yaml`.

**Tasks:**

1. **Audit each app's service to determine metrics endpoint**
   - Inspect each app's `service.yaml` and `deployment.yaml` in `apps/base/<app>/` to find the port name and targetPort that exposes metrics
   - Many apps don't expose metrics (immich, jellyfin, navidrome, audiobookshelf, snapcast) — create ServiceMonitors anyway for readiness/liveness probe visibility, note that they won't produce application-level metrics
   - Document findings in PR description

2. **Create ServiceMonitors in `apps/base/<app>/servicemonitor.yaml`**
   - For high-priority apps: `interval: 15s`
   - For normal-priority apps: `interval: 30s`
   - Label with `scrape-priority: high` or `scrape-priority: normal` for dashboard filtering
   - 14 high-priority ServiceMonitors, 4 normal-priority ServiceMonitors

**Files to create (18 ServiceMonitors):**
```
apps/base/adguard/servicemonitor.yaml                    (normal)
apps/base/audiobookshelf/servicemonitor.yaml              (normal)
apps/base/excalidraw/servicemonitor.yaml                  (normal)
apps/base/golinks/servicemonitor.yaml                     (high)
apps/base/hermes/servicemonitor.yaml                      (high)
apps/base/hermes-callee/servicemonitor.yaml               (high)
apps/base/homeassistant/servicemonitor.yaml               (high)
apps/base/homepage/servicemonitor.yaml                    (high)
apps/base/immich/servicemonitor.yaml                      (high)
apps/base/jellyfin/servicemonitor.yaml                    (high)
apps/base/linkding/servicemonitor.yaml                    (normal)
apps/base/mealie/servicemonitor.yaml                      (normal)
apps/base/memos/servicemonitor.yaml                       (high)
apps/base/navidrome/servicemonitor.yaml                   (high)
apps/base/openwebui/servicemonitor.yaml                   (high)
apps/base/overture/servicemonitor.yaml                    (high)
apps/base/snapcast/servicemonitor.yaml                    (normal)
apps/base/vitals/servicemonitor.yaml                      (high)
```

**Template:**
```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: <app-name>
  namespace: <namespace>
  labels:
    app: <app-name>
    release: kube-prometheus-stack
    scrape-priority: <high|normal>
spec:
  selector:
    matchLabels:
      app: <app-name>
  endpoints:
    - port: <metrics-port-name>
      path: /metrics
      interval: <15s|30s>
      scrapeTimeout: 10s
```

3. **Verify Prometheus discovers new ServiceMonitors**
   - After Flux applies, check Prometheus target discovery (`/targets` in Grafana)
   - Confirm all 21 apps appear as targets

### Phase 2: Healthcheck Deployment

**Goal:** A lightweight Deployment that runs periodic cluster health checks and exposes synthetic metrics to Prometheus.

**Rationale for Deployment over CronJob:** CronJobs are ephemeral and can't be scraped by Prometheus. A Deployment runs continuously, exposes `/metrics` on port 9100, and can be scraped at the same intervals as other apps.

**Architecture:**
- Single Python container (or Go if preferred) with two processes:
  1. Healthcheck loop: runs every 60s, checks pod readiness, PVC usage, node status
  2. Metrics server: exposes `/metrics` on port 9100, updated by the healthcheck loop
- ServiceMonitors scrape the `/metrics` endpoint
- Prometheus records the metrics as time-series

**Healthcheck checks:**
1. **Pod readiness** — `healthcheck_pod_ready{namespace, pod} = 0|1`
2. **PVC usage** — `healthcheck_pvc_usage_percent{namespace, pvc} = float`
3. **Node status** — `healthcheck_node_ready{node} = 0|1`
4. **Flux reconciliation** — `healthcheck_flux_reconciliation{kind, name, status} = 0|1`
5. **App uptime** — `healthcheck_app_uptime_seconds{namespace, app} = float`

**Tasks:**

1. **Create deployment manifest** — `infra/controllers/healthcheck/deployment.yaml`
   - Single container with Python image (e.g., `python:3.12-slim`)
   - Resource limits: 50m CPU, 128Mi memory
   - ServiceAccount with read-only access to pods, nodes, pvcs (namespaced)
   - ConfigMap with healthcheck configuration (check intervals, namespaces to monitor)

2. **Create healthcheck Python script** — `infra/controllers/healthcheck/healthcheck.py`
   - Uses `kubernetes` Python client to read pod/node/PVC status
   - Exposes `/metrics` via `prometheus_client` library
   - Runs checks every 60s

3. **Create Service and ServiceMonitor** — `infra/controllers/healthcheck/service.yaml`, `infra/controllers/healthcheck/servicemonitor.yaml`
   - Service on port 9100 (http)
   - ServiceMonitor scraping `/metrics` at 30s interval

4. **Create namespace** — `infra/controllers/healthcheck/namespace.yaml` (new `healthcheck` namespace)

5. **Create HelmRelease for Flux** — `infra/controllers/healthcheck/release.yaml`
   - Or use kustomize if the healthcheck is simpler than a Helm chart

6. **Verify healthcheck metrics are visible in Prometheus**
   - Check `/metrics` endpoint in Grafana
   - Verify `healthcheck_pod_ready`, `healthcheck_pvc_usage_percent` etc. are recorded

### Phase 3: Alertmanager Signal Routing

**Goal:** Configure Alertmanager to route critical alerts to Signal via the existing signal-bridge.

**Current state:** Signal is already wired — Hermes agents communicate via signal-bridge (port 8080, JSON-RPC on 7583). The `/v1/send` endpoint (or similar) exists and is used by Hermes.

**Tasks:**

1. **Determine signal-bridge alert endpoint**
   - Confirm the exact HTTP endpoint that signal-bridge uses for sending messages
   - This is used by Hermes already — check the Hermes deployment or source code for the API format
   - Expected format: `POST http://signal-bridge.signal-cli.svc.cluster.local:8080/v1/send` with JSON body containing `phone` and `message`
   - **DEPENDENCY:** Need Hermes signal-bridge API format (ask user or check hermes deployment)

2. **Create Alertmanager receiver config**
   - Add a `signal` receiver to the Alertmanager config
   - Use a `webhook` receiver that POSTs to signal-bridge
   - Configure the message template for Signal (compact format suitable for chat)

3. **Create webhook relay (if needed)**
   - If signal-bridge doesn't have a direct Alertmanager-compatible webhook endpoint, create a small relay
   - Options:
     a. Small Go/Python service in-cluster that converts Alertmanager webhook to signal-bridge API call
     b. Use Alertmanager's `url` field to POST directly to signal-bridge if it accepts the format
   - Deploy via `infra/controllers/` (or add as sidecar to signal-bridge if feasible)

4. **Update Alertmanager routing**
   - Critical alerts → Signal receiver
   - Warning alerts → Loki (already via default route)
   - Info alerts → null (email digest handled separately)

5. **Test end-to-end alert flow**
   - Trigger a test alert (e.g., set a Deployment to CrashLoopBackOff)
   - Verify Signal message is received
   - Verify warning alerts appear in Grafana

### Phase 4: Grafana Dashboards

**Goal:** Add new Grafana dashboards for healthcheck metrics and app-level overview.

**Tasks:**

1. **Cluster health dashboard** — `infra/configs/dashboards/cluster-health-cm.yaml`
   - Panels for: pod readiness summary, PVC usage heatmap, node status, Flux reconciliation status
   - Uses healthcheck metrics as primary data source
   - Follows existing ConfigMap pattern with `grafana_dashboard: "1"` label

2. **App health dashboard** — `infra/configs/dashboards/app-health-cm.yaml`
   - Enhanced version of existing `application-health-cm.yaml`
   - Add scrape-priority-based filtering
   - Show all 21 apps in a single view

3. **Alert summary dashboard** — `infra/configs/dashboards/alerts-cm.yaml`
   - Active alerts panel (from Alertmanager API)
   - Alert history (from Prometheus)
   - Signal delivery status (if relay is used)

### Phase 5: New Prometheus Rules

**Goal:** Add healthcheck-specific alerting rules and Flux reconciliation failure rules.

**Tasks:**

1. **Healthcheck alerting rules** — extend `infra/configs/alerts/prometheus-rules.yaml`
   - `HealthcheckPodNotReady` — fires when healthcheck reports pod not ready for > 5m
   - `HealthcheckPVCHighUsage` — fires when PVC usage > 90%
   - `HealthcheckFluxReconciliationFailed` — fires when Flux reconciliation has failed for > 15m

2. **Flux reconciliation rules** — add Flux-specific alerting
   - `FluxKustomizationFailed` — Kustomization status is NotReady
   - `FluxHelmReleaseFailed` — HelmRelease status is NotReady
   - `FluxSyncFailed` — Git repository sync has failed

3. **Recorded metrics for healthcheck** — add recording rules for common queries
   - `healthcheck:pod_ready_ratio` — ratio of ready pods to total pods per namespace
   - `healthcheck:pvc_usage_max` — max PVC usage percentage across all PVCs

## Risk Analysis

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| ServiceMonitor targets produce too many low-value metrics (noise) | Medium | Low | Add `scrape-priority` labels, filter in Grafana. Review metrics after 1 week, disable low-value ones. |
| Healthcheck Python script has resource issues | Low | Low | Set CPU/memory limits (50m/128Mi). Test with k6 or similar before deploying. |
| Signal webhook relay introduces latency | Medium | Medium | Use direct signal-bridge API if possible. Add timeout (5s). Log delivery failures. |
| Alertmanager config change breaks existing alerts | Low | High | Keep existing config intact, only add new receivers/routes. Test in staging first. |
| Flux reconciliation fails due to new resources | Low | Medium | New resources are in separate directories. Flux should handle them independently. Monitor Flux status after deploy. |
| Grafana dashboard ConfigMaps conflict with existing | Low | Low | Follow existing naming convention (`<name>-cm.yaml`). Use distinct panel IDs. |

## Success Criteria

All criteria must be verifiable via the cluster (no subjective assessments):

1. **All 21 apps appear in Prometheus targets** — `up{job=~"kubernetes-pods.*"}` returns 21+ results (including healthcheck)
2. **Healthcheck metrics are visible** — `healthcheck_pod_ready` metric is present in Prometheus with 21+ series
3. **Critical alerts route to Signal** — trigger a CrashLoopBackOff test, receive a Signal message within 10 minutes
4. **Grafana cluster health dashboard loads** — open the new dashboard, see pod readiness, PVC usage, and node status panels
5. **No existing alerts break** — confirm existing alerts (CNPG, node filesystem, BGP) still fire correctly
6. **Flux reconciles all new resources** — `flux get kustomizations` shows all green after apply

## Dependencies

1. **Signal-bridge API format** — Need to confirm the exact HTTP endpoint and JSON format that signal-bridge uses for sending messages (HERMES_ALLOWED_ACCOUNTS env var suggests Hermes knows this)
2. **Hermes source code** — Check how Hermes sends messages via signal-bridge to understand the API
3. **App metrics endpoints** — Need to inspect each app's deployment to find the correct metrics port (some apps may not expose metrics at all)
4. **Existing ConfigMap** — The Alertmanager config lives in `kube-prometheus-stack-helm-values` ConfigMap in the cluster, not in Git. Need to either move it to Git or create it as a managed resource.

## Rollback Plan

1. **ServiceMonitors rollback** — Delete the ServiceMonitor YAML files from `apps/base/<app>/`. Flux will delete the ServiceMonitor CRs automatically. No impact on apps.
2. **Healthcheck rollback** — Delete `infra/controllers/healthcheck/` directory. Flux will delete the Deployment, Service, and ServiceMonitor. No impact on other resources.
3. **Alertmanager rollback** — Revert the Alertmanager config change (remove the signal receiver and route). Alertmanager will reload config automatically.
4. **Dashboard rollback** — Delete the dashboard ConfigMaps from `infra/configs/dashboards/`. Grafana will remove them.
5. **PrometheusRules rollback** — Revert changes to `infra/configs/alerts/prometheus-rules.yaml`. Prometheus will reload rules automatically.

**Full rollback:** Delete the PR branch and revert all commits. Flux will reconcile back to the previous state within 120 minutes (HelmRelease interval).

## Future Considerations

1. **Prometheus pushgateway** — If we need CronJobs to push metrics in the future (e.g., for batch jobs), consider deploying pushgateway. For now, the Deployment pattern is simpler.
2. **Thanos** — If long-term metrics retention is needed (current PVC is ephemeral-ish), consider adding Thanos for object store-backed retention.
3. **Multi-cluster** — If homelab expands to multiple clusters, consider a federated Prometheus setup or VictoriaMetrics.
4. **Log-based alerting** — Currently alerts are metric-driven. Consider adding log-based alerts from Loki (e.g., error rate spikes in app logs).
5. **Runbook automation** — Add runbook URLs to alert annotations that trigger self-healing actions (e.g., restart a stuck pod).
