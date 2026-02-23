# Monitoring & Logging

## 1. Overview
The homelab cluster uses a comprehensive monitoring and logging stack based on the Prometheus ecosystem. It provides real-time metrics, log aggregation, and alerting capabilities.

## 2. Architecture
The monitoring stack is deployed in the `monitoring` namespace via Flux using official Helm charts.
- **Kube-Prometheus-Stack**: A collection of Kubernetes manifests, Grafana dashboards, and Prometheus rules combined with documentation and scripts to provide easy to operate end-to-end Kubernetes cluster monitoring with Prometheus using the Prometheus Operator.
  - **Prometheus**: Scrapes and stores time-series metrics.
  - **Alertmanager**: Handles alerts sent by client applications such as the Prometheus server.
  - **Grafana**: Provides visualization dashboards for metrics and logs.
- **Loki**: A horizontally-scalable, highly-available, multi-tenant log aggregation system inspired by Prometheus.
- **Promtail**: An agent which ships the contents of local logs to a private Loki instance.

## 3. URLs
- **Grafana**: https://grafana.burntbytes.com
- **Prometheus**: (Internal port-forward only)
- **Alertmanager**: (Internal port-forward only)

## 4. Configuration
- **Helm Values**:
  - `infra/controllers/kube-prometheus-stack/values.yaml`
  - `infra/controllers/loki/values.yaml`
  - `infra/controllers/promtail/values.yaml`
- **Grafana Dashboards**: Pre-configured dashboards are included in the `kube-prometheus-stack` chart. Additional custom dashboards can be added via ConfigMaps.
- **Loki Data Source**: Grafana is configured to use Loki as a data source for log querying.

## 5. Usage Instructions
- **Grafana**: Access the Grafana UI to view dashboards and query metrics/logs.
- **Prometheus**: Use the Prometheus UI (via port-forward) to execute PromQL queries and check target status.
- **Loki**: Use the Explore tab in Grafana to query logs using LogQL.

## 6. Testing
To verify the monitoring stack is working:
```bash
kubectl get pods -n monitoring
```
All pods (Prometheus, Alertmanager, Grafana, Loki, Promtail) should be in a `Running` state.
Access Grafana and verify that data is populating in the default dashboards.

## 7. Monitoring & Alerting
- **Metrics**: The stack monitors itself. Prometheus scrapes metrics from all its components.
- **Logs**: Promtail collects logs from all containers in the cluster and sends them to Loki.
- **Alerts**: Alertmanager is configured with default rules (e.g., `KubePodCrashLooping`, `TargetDown`). Custom rules can be added via `PrometheusRule` resources.

## 8. Disaster Recovery
- **Backup Strategy**: 
  - Prometheus metrics are ephemeral and not backed up.
  - Loki logs are stored on persistent volumes (if configured) or ephemeral storage.
  - Grafana dashboards and data sources are provisioned declaratively via GitOps.
- **Restore Procedure**: Re-apply the Flux Kustomizations. The stack will recreate itself and begin collecting new data.

## 9. Troubleshooting
- **Missing Metrics**: 
  - Check the Prometheus UI `Targets` page to ensure endpoints are being scraped successfully.
  - Verify `ServiceMonitor` or `PodMonitor` resources are correctly configured and labeled.
- **Missing Logs**: 
  - Check Promtail logs for errors reading container logs or sending to Loki.
  - Verify Loki is running and accepting connections.
- **Grafana Login Issues**: Check the Grafana admin credentials in the `kube-prometheus-stack` values or secrets.
