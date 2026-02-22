# Application Health Dashboards Plan

This document outlines a comprehensive plan for adding detailed Grafana dashboards to monitor application health for all apps in both staging and production overlays.

## Objectives

1.  **Visibility**: Provide a clear, real-time view of the health and performance of every application deployed in the homelab.
2.  **Proactive Monitoring**: Enable early detection of issues before they impact users.
3.  **Standardization**: Create a consistent dashboard layout and metric set across all applications.
4.  **Environment Separation**: Clearly distinguish between staging and production metrics.

## Standard Dashboard Layout

Every application dashboard should follow a standard layout, organized into rows:

### Row 1: Overview & Golden Signals

*   **Uptime/Availability**: Percentage of time the application is reachable (via Blackbox Exporter or ingress metrics).
*   **Request Rate**: Total HTTP requests per second (via Cilium/Gateway API or ingress metrics).
*   **Error Rate**: Percentage of HTTP 5xx errors.
*   **Latency (P95/P99)**: Response time percentiles.
*   **Active Connections/Sessions**: If applicable to the application.

### Row 2: Pod & Container Resources

*   **CPU Usage**: CPU cores used vs. requested/limited.
*   **Memory Usage**: Memory bytes used vs. requested/limited.
*   **Network I/O**: Bytes received/transmitted per second.
*   **Restarts**: Number of container restarts over time.
*   **Pod Status**: Current state of the pods (Running, Pending, CrashLoopBackOff).

### Row 3: Storage & Database (If Applicable)

*   **PVC Usage**: Persistent Volume Claim space used vs. capacity.
*   **Database Connections**: Active connections to the Postgres/Redis backend.
*   **Database Query Latency**: Average query execution time.
*   **Database Cache Hit Ratio**: Percentage of queries served from cache.

### Row 4: Application-Specific Metrics

*   Custom metrics exposed by the application itself (e.g., Immich background jobs, Navidrome active streams, Authelia authentication failures).

## Execution Plan

### Phase 1: Infrastructure Preparation

1.  **Verify Metrics Collection**: Ensure `kube-prometheus-stack` is correctly scraping metrics from all namespaces.
2.  **Standardize Labels**: Ensure all applications have consistent labels (e.g., `app.kubernetes.io/name`, `env`) to allow for easy filtering in Grafana.
3.  **Dashboard Provisioning**: Configure Grafana to automatically load dashboards from a specific directory or ConfigMap (e.g., using Grafana sidecar).

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