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
- **Prometheus Alerting Rules** (PromQL), all in `infra/configs/alerts/`:
  - `prometheus-rules.yaml` — CNPG, node filesystem, DaemonSet, Loki push errors, container health, Cilium BGP, Flux.
  - `cert-manager-rules.yaml` — certificate renewal, expiry, and a `CertManagerDown` meta-alert.
  - `loki-rules.yaml` — log-pipeline alerts: Loki WAL/chunk-flush failures, loki-canary end-to-end missing/late entries, promtail send failures and dropped entries, plus `LokiDown` / `LokiCanaryDown` / `PromtailDown` meta-alerts.
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
- **Metrics**: The stack monitors itself. Prometheus scrapes metrics from all its components. Loki (`loki:3100`), loki-canary (`loki-canary:3500`) and promtail (`promtail-metrics:3101`) are scraped via the charts' own ServiceMonitor options (`monitoring.serviceMonitor.enabled` in `infra/controllers/loki/values.yaml`, `serviceMonitor.enabled` in `infra/controllers/promtail/values.yaml`). A single Loki ServiceMonitor covers both `loki` and `loki-canary`; `loki-headless` is excluded by its `prometheus.io/service-monitor=false` label and `loki-memberlist` has no `http-metrics` port.
- **Container Logs**: Promtail collects logs from all containers in the cluster and sends them to Loki.
- **Kernel Logs**: Vector collects Talos kernel and service logs and sends them to Loki. See [Talos Kernel Log Shipping](kernel-log-shipping.md).
- **Metric Alerts**: Alertmanager receives alerts from Prometheus via `PrometheusRule` resources. Custom rules are in `infra/configs/alerts/` (`prometheus-rules.yaml`, `cert-manager-rules.yaml`, `loki-rules.yaml`).
- **Log Alerts**: Loki's built-in ruler evaluates LogQL rules from `infra/controllers/loki/alerting-rules.yaml` and forwards firing alerts to Alertmanager.
- **Alert delivery**: Alertmanager config lives in `infra/controllers/kube-prometheus-stack/values.yaml` (`alertmanager.config`). Four receivers:

| Receiver | Gets | Delivery |
|---|---|---|
| `email-critical` | `severity = "critical"` | `gjcourt+critical@gmail.com` (no skip-inbox filter), `send_resolved: true`, `repeat_interval: 4h` |
| `email-warning` | `severity = "warning"` | `gjcourt+alerts@gmail.com` behind a Gmail skip-inbox label, `send_resolved: false`, `repeat_interval: 24h` |
| `deadman` | `Watchdog` only | Webhook POST to an off-cluster healthchecks.io check every 5m — the **dead man's switch**; the check alerts on the ping's *absence*. Notifies `gjcourt+critical@gmail.com` (see below). See [plans/2026-08-09-local-deadman-mesh.md](../plans/2026-08-09-local-deadman-mesh.md) |
| `null` | `overture-prod` KubePdb, `*-stage` warnings, and the default route | Discarded |

Both email receivers use Gmail SMTP (`smtp.gmail.com:587`, STARTTLS, auth `gjcourt@gmail.com`). Secrets are SOPS-encrypted in the `monitoring` namespace and mounted via `alertmanagerSpec.secrets`: `alertmanager-smtp` → `/etc/alertmanager/secrets/alertmanager-smtp/password` (`smtp_auth_password_file`), and `alertmanager-deadman` → `/etc/alertmanager/secrets/alertmanager-deadman/ping-url` (`url_file`).

Note the default route receiver is `null`, so an alert carrying no `severity` label is silently dropped. Note also that the deadman proves the *webhook* leg only — a broken SMTP path would leave the check green while email alerts fail. See [plans/2026-06-17-alertmanager-smtp-alerting.md](../plans/2026-06-17-alertmanager-smtp-alerting.md).

### Dead man's switch — verified timings

The healthchecks.io check is configured **Simple schedule, Period 6m, Grace 15m
→ 21m worst-case detection**. Period is 6m and not 5m because the measured ping
cadence is **5m03–5m04s**: `group_interval` sets a nominal 5m and each ping
slips by the POST round-trip, so a 5m Period would park the check in amber
"late" for a few seconds of every cycle and destroy "late" as a signal.

Notifications go to `gjcourt+critical@gmail.com`, **not** `+alerts`. `+alerts`
is the skip-inbox tier documented above as unread, and total alerting failure
outranks any individual critical — routing the deadman there would put the one
alert that means "you are now blind" in the mailbox you do not watch. A
non-email channel (Pushover/ntfy/SMS) is worth adding for the same reason: 21m
detection becomes overnight detection if it only reaches an inbox.

Three behaviours that will otherwise read as faults:

- **Only one replica's counter moves.** In HA the peer that wins the dedup race
  is the one that POSTs; `alertmanager_notifications_total{integration="webhook"}`
  stays `0` on the other. Read both replicas before concluding the webhook is
  broken.
- **`amtool check-config` does not catch `repeat_interval < group_interval`.**
  It returns SUCCESS; only the running server logs the warning
  (`Notifications will not repeat until the next group_interval`). That warning
  is expected and benign — `repeat_interval` must stay strictly under
  `group_interval` or the cadence silently doubles.
- **Recovery after a silence expires is not instant** — the next flush waits a
  full `group_interval` (measured 4m37s).

Fail-closed test, **verified 2026-08-16** (procedure in the
`secret-alertmanager-deadman.yaml.example` header):

| | |
|---|---|
| Last ping → DOWN | 09:11:11Z → 09:32:11Z = **21m0s**, exactly Period + Grace |
| Silence expired → resumed | 09:36:34Z → 09:41:11Z = **4m37s** |
| Reported downtime | 8m59s |
| Ping accounting | healthchecks.io "Total Pings" matched the Alertmanager counter at both points — **zero loss** cluster → endpoint |

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
  - **Every `ServiceMonitor` must carry the label `release: kube-prometheus-stack`.** Prometheus runs with `serviceMonitorSelectorNilUsesHelmValues: true`, so its `serviceMonitorSelector` is `matchLabels: {release: kube-prometheus-stack}`. A `ServiceMonitor` without that label is created successfully and then silently ignored — the resource exists, `kubectl get servicemonitor` looks fine, and no metrics ever arrive. Confirm the selector with `kubectl get prometheus -A -o jsonpath='{.items[0].spec.serviceMonitorSelector}'`, and confirm a metric actually landed with `count(<metric_name>)` in the Prometheus UI rather than assuming.
- **Missing Container Logs**:
  - Check Promtail logs for errors reading container logs or sending to Loki.
  - Verify Loki is running and accepting connections.
- **Missing Kernel Logs**: See the [Talos Kernel Log Shipping](kernel-log-shipping.md) troubleshooting section.
- **Grafana Login Issues**: Check the Grafana admin credentials in the `kube-prometheus-stack` values or secrets.
