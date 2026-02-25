# Application Health Dashboards Plan

This document outlines a plan for creating a centralized, filterable Grafana dashboard to monitor application health for all apps across staging and production environments.

## Objectives

1.  **Centralization**: Consolidate application monitoring into a single, comprehensive dashboard.
2.  **Dynamic Filtering**: Allow operators to filter metrics by Namespace, Application (Pod), and Environment.
3.  **Standardization**: Apply a uniform set of "Golden Signal" metrics to all applications.

## Single Dashboard Strategy

Instead of maintaining individual dashboards for each application, we will deploy a single **"Application Health"** dashboard.

### Templating Variables
- **Datasource**: Select the Prometheus datasource.
- **Namespace**: Dropdown list of namespaces (e.g., `immich-prod`, `authelia-stage`).
- **Pod**: Regex-enabled dropdown to select one or multiple pods within the selected namespace.

## Dashboard Layout

### Row 1: Resource Usage (Universal)
*   **CPU Usage**: Per-pod CPU usage (rate).
*   **Memory Usage**: Per-pod Memory working set.
*   **Network I/O**: Per-pod Receive/Transmit bandwidth.

### Row 2: Performance & Reliability (Future)
*   These will require standardizing on Ingress/Gateway API metrics or Service Meshes.
*   **Request Rate**: HTTP Request/s.
*   **Error Rate**: HTTP 5xx %.
*   **Latency**: Request duration P95.

### Row 3: Application Specifics (Future)
*   Potential for conditional rows that only show up if specific metrics (e.g., `pg_stat_activity`) are present.

## Execution Plan

### Phase 1: Core Resource Dashboard (Complete)
- [x] Create generic `Application Health` dashboard with Namespace and Pod variables.
- [x] Deploy via `infra/configs/dashboards` ConfigMap.
- [x] Remove legacy individual dashboards from `apps/base`.

### Phase 2: Enhanced Metrics
- [ ] Integrate Cilium/Envoy metrics for Request/Error/Latency rows.
- [ ] Add basic storage (PVC) usage metrics.

### Phase 3: Alerting
- [ ] Define universal alerts for High CPU, OOM Kills, and CrashLoopBackOff.

### Phase 2: Core Application Dashboards

Create dashboards for the most critical applications:

*   [ ] **Authelia**: Monitor authentication requests, failures, and SSO performance.
*   [ ] **AdGuard Home**: Monitor DNS query volume, blocked requests, and upstream latency.
*   [ ] **Homepage**: Monitor page load times and widget API request success rates.

### Phase 3: Media & Data Application Dashboards

Create dashboards for resource-intensive applications:

*   [ ] **Immich**: Monitor machine learning job queues, transcoding performance, and database vector search latency.
*   [ ] **Jellyfin**: Monitor active streams, transcoding CPU/GPU usage, and library scan progress.
*   [ ] **Navidrome**: Monitor active streams and library scan progress.
*   [ ] **Audiobookshelf**: Monitor active streams and library scan progress.
*   [ ] **Snapcast**: Monitor active clients, stream latency, and buffer underruns.

### Phase 4: Utility Application Dashboards

Create dashboards for the remaining applications:

*   [ ] **Memos**: Monitor API request rates and database performance.
*   [ ] **Linkding**: Monitor API request rates and database performance.
*   [ ] **Mealie**: Monitor API request rates and database performance.
*   [ ] **GoLinks**: Monitor redirect latency and database performance.
*   [ ] **Excalidraw**: Monitor active sessions and WebSocket connections.
*   [ ] **Vitals**: Monitor API request rates and database performance.

## Implementation Details

*   **Dashboard as Code**: All dashboards will be defined as JSON files and managed via GitOps (Flux).
*   **Variables**: Use Grafana variables to allow switching between environments (`staging` vs. `production`) and specific pods/instances.
*   **Alerting**: Integrate key metrics (e.g., high error rate, low availability) with Alertmanager to trigger notifications.