# Monitoring & Logging

## 1. Overview
The homelab cluster uses a comprehensive monitoring and logging stack based on the Prometheus ecosystem. It provides real-time metrics, log aggregation, and alerting capabilities.

## 2. Architecture
The monitoring stack is deployed in the `monitoring` namespace via Flux using official Helm charts.
- **Kube-Prometheus-Stack**: A collection of Kubernetes manifests, Grafana dashboards, and Prometheus rules combined with documentation and scripts to provide easy to operate end-to-end Kubernetes cluster monitoring with Prometheus using the Prometheus Operator.
  - **Prometheus**: Scrapes and stores time-series metrics.
  - **Alertmanager**: Handles alerts sent by client applications such as the Prometheus server.
  - **Grafana**: Provides visualization dashboards for metrics and logs.
- **Loki**: A horizontally-scalable, highly-available, multi-tenant log aggregation system inspired by Prometheus. The built-in **ruler** component evaluates LogQL alerting rules and forwards alerts to Alertmanager.
- **Promtail**: An agent which ships container logs (CRI format) from every node to Loki.
- **Vector**: A DaemonSet that receives Talos kernel and service logs over TCP (Talos `json_lines` format) and forwards them to Loki. Required because Talos `machine.logging` sends newline-delimited JSON, not RFC 5424 syslog. See [Talos Kernel Log Shipping](kernel-log-shipping.md) for the full architecture and operational guide.

## 3. URLs
- **Grafana**: https://grafana.burntbytes.com
- **Prometheus**: (Internal port-forward only)
- **Alertmanager**: (Internal port-forward only)

## 4. Configuration
- **Helm Values**:
  - `infra/controllers/kube-prometheus-stack/values.yaml`
  - `infra/controllers/loki/values.yaml`
  - `infra/controllers/promtail/values.yaml`
  - `infra/controllers/vector/values.yaml`
- **Loki Alerting Rules** (LogQL, evaluated by Loki ruler): `infra/controllers/loki/alerting-rules.yaml`
- **Prometheus Alerting Rules** (PromQL): `infra/configs/alerts/prometheus-rules.yaml`
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
- **Container Logs**: Promtail collects logs from all containers in the cluster and sends them to Loki.
- **Kernel Logs**: Vector collects Talos kernel and service logs and sends them to Loki. See [Talos Kernel Log Shipping](kernel-log-shipping.md).
- **Metric Alerts**: Alertmanager receives alerts from Prometheus via `PrometheusRule` resources. Custom rules are in `infra/configs/alerts/prometheus-rules.yaml`.
- **Log Alerts**: Loki's built-in ruler evaluates LogQL rules from `infra/controllers/loki/alerting-rules.yaml` and forwards firing alerts to Alertmanager.

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
- **Missing Container Logs**:
  - Check Promtail logs for errors reading container logs or sending to Loki.
  - Verify Loki is running and accepting connections.
- **Missing Kernel Logs**: See the [Talos Kernel Log Shipping](kernel-log-shipping.md) troubleshooting section.
- **Grafana Login Issues**: Check the Grafana admin credentials in the `kube-prometheus-stack` values or secrets.
