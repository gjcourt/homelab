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
| `deadman` | `IngestionWatchdog` only | Webhook POST to an off-cluster healthchecks.io check every 5m — the **dead man's switch**; the check alerts on the ping's *absence*. Notifies `gjcourt+critical@gmail.com` (see below). See [Mute Prometheus](#mute-prometheus--why-the-deadman-is-gated-on-ingestion) and [plans/2026-08-09-local-deadman-mesh.md](../plans/2026-08-09-local-deadman-mesh.md) |
| `null` | the chart's `Watchdog`, `overture-prod` KubePdb, `*-stage` warnings, and the default route | Discarded |

Both email receivers use Gmail SMTP (`smtp.gmail.com:587`, STARTTLS, auth `gjcourt@gmail.com`). Secrets are SOPS-encrypted in the `monitoring` namespace and mounted via `alertmanagerSpec.secrets`: `alertmanager-smtp` → `/etc/alertmanager/secrets/alertmanager-smtp/password` (`smtp_auth_password_file`), and `alertmanager-deadman` → `/etc/alertmanager/secrets/alertmanager-deadman/ping-url` (`url_file`).

Alertmanager's deadman is not the only off-cluster check: a second healthchecks.io check is pinged hourly by the hestia GitHub Actions runner itself — see [Runner canary](#runner-canary--off-cluster-proof-the-hestia-deploy-path-is-alive) below.

Note the default route receiver is `null`, so an alert carrying no `severity` label is silently dropped. Note also that the deadman proves the *webhook* leg only — a broken SMTP path would leave the check green while email alerts fail. See [plans/2026-06-17-alertmanager-smtp-alerting.md](../plans/2026-06-17-alertmanager-smtp-alerting.md).

### Mute Prometheus — why the deadman is gated on ingestion

The deadman's heartbeat is **`IngestionWatchdog`**, defined in
`infra/configs/alerts/prometheus-ingestion-rules.yaml`. The chart's `Watchdog`
still evaluates — it is upstream's rule and is left alone — but it is routed to
`null` and no longer pings anything.

`Watchdog`'s expression is `vector(1)`. It is computed without reading a single
stored sample, so it is true on a Prometheus that has ingested nothing for
twelve hours. That is not hypothetical: on
[2026-08-13](../operations/incidents/2026-08-13-iscsi-readonly-remount-monitoring-blind.md)
Prometheus' PVC remounted read-only, `prometheus-0` stayed `Running` with 0
restarts, kept evaluating rules against frozen data, and appended its last
sample at 10:05:10 UTC. `Watchdog` flowed the whole time, the check stayed
green, and monitoring was blind for 12h32m with nothing paging.

`IngestionWatchdog` adds the missing conjunct:

```promql
vector(1)
and on()
(sum(rate(prometheus_tsdb_head_samples_appended_total{job="kube-prometheus-stack-prometheus", namespace="monitoring"}[5m])) > 0)
```

Prometheus scrapes itself, so when ingestion stops the counter goes stale, the
`rate` returns empty after one lookback delta, the `and` yields nothing, the
alert **resolves**, the Alertmanager group empties, the pings stop, and
healthchecks.io alerts on their absence. `and on()` matches on the empty label
set, so one healthy replica is enough to keep the heartbeat — which is correct:
alerting is not blind while any replica has fresh data. A single mute replica is
covered instead by `PrometheusIngestionStalled` below.

It **strictly dominates** `Watchdog`: a dead Prometheus, a dead Alertmanager or
a broken webhook silence both. Nothing was given up by re-pointing the route.
Leaving `Watchdog` *also* pinging the same check would have restored the exact
blind spot, since the ungated heartbeat alone keeps the check green through a
total ingestion outage.

Replayed against the retained incident data (all four expressions, 9.7 days at
120s resolution):

| Expression | Result |
|---|---|
| `IngestionWatchdog` | true at **6603 / 6983** steps; exactly one gap, **08-13 10:03 → 22:47 UTC** (764m) — the blackout, and nothing else |
| `PrometheusIngestionAbsent` | true at **377** steps, one contiguous run **08-13 10:11 → 22:43 UTC** (752m); zero truthy steps otherwise |
| `PrometheusIngestionStalled` | zero truthy steps in 9.7 days |
| `PrometheusWALWriteFailures` | zero truthy steps in 9.7 days |

The heartbeat re-armed **4 minutes** after both replicas were deleted during the
2026-08-13 recovery, so a routine restart costs far less than the 21m budget.

#### The in-cluster half, and the staleness trap that shapes it

| Alert | Vantage | Fires when | `for:` |
|---|---|---|---|
| `PrometheusIngestionStalled` | peer-observed | one replica is scrapable but its append counter is flat | 5m |
| `PrometheusIngestionAbsent` | self-observed | **no** replica has appended a sample in 10m — monitoring is blind | 5m |
| `PrometheusWALWriteFailures` | peer-observed | WAL writes are failing (names the cause: read-only volume) | 5m |

All three are **`severity: critical`** and reach `gjcourt+critical@gmail.com`.
They live inside the thing they watch, so they are a faster tier, never a
substitute for the off-cluster deadman.

Two traps worth keeping in mind before editing these expressions:

- **A mute Prometheus cannot observe its own muteness with `rate`.** It stops
  appending its own metrics too, so within one lookback delta (5m)
  `prometheus_tsdb_head_samples_appended_total` disappears from its own view and
  `rate(...) == 0` returns *empty*, not `0`. It can never satisfy a `for:`
  longer than that narrow band. This is why the chart's own
  `PrometheusNotIngestingSamples` (severity `warning`, `for: 10m`) did not fire
  on 2026-08-13 despite being the textbook expression, and why
  `PrometheusIngestionAbsent` uses `absent_over_time`, which is true precisely
  when there are no samples.
- **`prometheus_tsdb_head_samples_appended_total` carries a `type` label.** The
  `type="histogram"` series sits at a constant `0` on a healthy Prometheus, so
  the bare `rate(...) == 0` form matches every replica at all times — verified
  live, it returned 2 series on a healthy cluster. `sum without (type)` is
  load-bearing, not tidying. Upstream does the same for the same reason.

`prometheus_tsdb_wal_writes_failed_total` is also exported by Loki
(`job="monitoring/loki"`), so that alert is scoped by `job`. It is the one arm
that could **not** be validated against the incident: the retained data has the
counter at `0` on both replicas right up to the last sample, because a
Prometheus that cannot write its WAL cannot record the counter proving its WAL
writes failed. It is kept as a cause-naming supplement, peer-observed only.

#### Testing it for real

The replay above is evidence, not proof — it shows the expressions would have
behaved correctly on recorded data, not that the whole chain fires. To exercise
the chain, take ingestion away from **both** replicas and watch two independent
things happen:

```bash
# Freeze ingestion without destroying data: scale the StatefulSet to 0.
# Flux will revert this, so suspend it first.
flux suspend helmrelease kube-prometheus-stack -n monitoring
kubectl -n monitoring scale statefulset prometheus-kube-prometheus-stack-prometheus --replicas=0
```

Expected, in order:

1. **~15m** — `PrometheusIngestionAbsent` cannot fire, because there is no
   Prometheus left to evaluate it. This is the point of the second half, and
   the reason scaling to 0 is a *weaker* test than a read-only volume.
2. **~21m** — the healthchecks.io check goes red and emails
   `gjcourt+critical@gmail.com`. This is the signal that matters.

For the failure this was actually built for — live but mute — make the volume
read-only instead of scaling down, so rule evaluation keeps running:

```bash
kubectl -n monitoring exec prometheus-kube-prometheus-stack-prometheus-0 -c prometheus \
  -- sh -c 'mount -o remount,ro /prometheus'   # requires a privileged context
```

Then expect `PrometheusIngestionAbsent` (critical, email) at ~15m **and** the
deadman at ~21m — two independent paths, which is the whole design. Restore
with `flux resume helmrelease kube-prometheus-stack -n monitoring` and a pod
delete per replica.

Distinguishing the two failure classes from the outside:

| What you see | What it means |
|---|---|
| Deadman red, no email | Alertmanager or the notification path died — the classic case |
| Deadman red **and** a `PrometheusIngestionAbsent` email | Prometheus went mute; alerting still works but every other alert is computed on frozen data |
| `PrometheusIngestionStalled` email, deadman green | One replica is mute, the other is healthy. Not urgent-blind, but fix it before the second one goes |

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

### Runner canary — off-cluster proof the hestia deploy path is alive

`.github/workflows/runner-canary.yml` runs hourly (`cron: 17 * * * *`) on
`runs-on: [self-hosted, hestia]` and `curl`s a **second, separate**
healthchecks.io check. It is the only monitoring in this repo that observes
hestia's GitHub Actions runner, and the only one that observes anything from
outside the cluster.

**Why it is not a Prometheus rule.** Everything else covering this blind spot is
in-cluster: a container writes a textfile, node-exporter serves it, Prometheus
scrapes it, a rule evaluates it. On
[2026-08-13](../operations/incidents/2026-08-13-iscsi-readonly-remount-monitoring-blind.md)
Prometheus' PVC remounted read-only and it kept evaluating rules while ingesting
nothing — every in-cluster alert sat silently "healthy" on frozen data. This
canary shares no failure domain with that: GitHub schedules it, hestia executes
it, healthchecks.io judges it. Reimplementing it as a PromQL rule would delete
the only property that makes it worth having.

**What a green check proves** — strictly more than "the runner container is
up": GitHub could dispatch a run, the runner was online and picked the job up,
and the runner executed a step. That is the entire path that was dead during the
2026-08-18 incident (design: `docs/plans/2026-08-18-hestia-deploy-monitoring-gap.md`, landing separately), where
a `deploy-hestia.yml` run sat queued indefinitely against a crash-looping
runner. GitHub emails on workflow *failure*; a run nobody picks up never fails,
so GitHub is structurally silent about exactly that state.

| | |
|---|---|
| Check config | Simple schedule, **Period 1h, Grace 1h** → ≤2h worst-case detection |
| Ping URL | repo secret `HEALTHCHECKS_RUNNER_CANARY_URL` (Settings → Secrets and variables → Actions) — **not** SOPS; a GitHub-hosted schedule cannot read a cluster secret |
| Notifications | `gjcourt+critical@gmail.com`, same reasoning as the deadman |

Two independent failure signals, and they cannot both go quiet:

- **No ping arrives** (runner offline, run stuck queued, GitHub cannot
  dispatch) → the check goes red and healthchecks.io emails. This is the case
  nothing else catches.
- **The run itself fails** (no egress from hestia, healthchecks.io down, secret
  missing or wrong) → the workflow exits non-zero and GitHub emails on failure.
  A failed ping is also an absent ping, so this eventually trips the first
  signal too.

Operational notes:

- **The runner is ephemeral**, so the canary costs **one runner container
  restart per hour**. The `myoung34/github-runner` entrypoint *presence-tests*
  `EPHEMERAL`, so the compose's `EPHEMERAL: "false"` still enables ephemeral
  mode — the value is irrelevant, only the variable being set matters. Read any
  hestia restart-rate alert with that hourly baseline in mind; the planned
  `increase(homelabscope_container_restarts_total[1h]) > 5` arm sits well above
  it.
- **`concurrency: cancel-in-progress: true`** means a backlog built up while the
  runner was down collapses to a single pending run, so recovery replays one
  canary rather than a dozen.
- **GitHub disables `schedule` triggers after 60 days of repository inactivity.**
  This repo is kept active by Renovate, but a long quiet period would stop the
  canary silently — the check going red is what surfaces it.
- **Two checks now share one healthchecks.io account.** A lapsed or closed
  account silently kills both, and nothing in-cluster notices, because a dead
  check cannot alert on itself.

Setup and troubleshooting: [`hosts/hestia/actions-runner/README.md`](../../hosts/hestia/actions-runner/README.md).

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
