---
status: draft
last_modified: 2026-05-09
---

# Monitoring Coverage & Signal Routing

## Overview

Close two specific gaps in the existing kube-prometheus-stack deployment:

1. **ServiceMonitor coverage** — most workload apps that could expose `/metrics` aren't scraped. One existing SM (authelia) is also missing the release label that makes Prometheus discover it.
2. **Critical-alert routing to Signal** — Alertmanager currently routes every alert to a `null` receiver. Wire `severity=critical` alerts through the existing signal-cli bridge.

This plan **does not** add new dashboards or a custom healthcheck exporter — see [Out of scope](#out-of-scope-deliberately) for why.

## Relationship to prior plans

- `docs/plans/2026-02-21-app-health-dashboards-plan.md` — `status: complete`. Generic Application Health dashboard is deployed.
- `docs/plans/2026-02-21-cluster-health-dashboards-plan.md` — `status: complete`. Cluster Overview, Node Details, Storage & CSI, Networking & Gateway, and Control Plane dashboards are deployed.

This plan does not supersede or extend either. Dashboard work is intentionally out of scope.

## Ground truth (verified against `origin/master` at 2026-05-09)

### Apps in `apps/production/kustomization.yaml` (25 entries)

- **Workload apps (23):** `adguard`, `audiobookshelf`, `authelia`, `cloudflare-tunnel`, `excalidraw`, `golinks`, `hermes`, `hermes-callee`, `homeassistant`, `homepage`, `immich`, `jellyfin`, `linkding`, `mealie`, `memos`, `navidrome`, `openwebui`, `overture`, `signal-cli`, `snapcast`, `synology-iscsi-monitor`, `truenas-iscsi-monitor`, `vitals`.
- **Kustomize aggregator entries excluded from SM scope (2):** `certificates`, `external-services`. These are infra resource bundles, not workloads.

### Existing ServiceMonitors on `origin/master` (4)

| File | Has `release: kube-prometheus-stack` label? | Notes |
|------|---------------------------------------------|-------|
| `apps/base/authelia/servicemonitor.yaml` | **No** — broken discovery | Likely not scraped today; see §1.2 |
| `apps/base/signal-cli/servicemonitor.yaml` | (verify) | |
| `apps/base/synology-iscsi-monitor/servicemonitor.yaml` | Yes | 60s interval, namespaceSelector set |
| `apps/base/truenas-iscsi-monitor/servicemonitor.yaml` | Yes | |

### SM discovery mechanic

`infra/controllers/kube-prometheus-stack/values.yaml:4146` sets `serviceMonitorSelectorNilUsesHelmValues: true` and the explicit `serviceMonitorSelector: {}` is empty. Combined, this means Prometheus selects ServiceMonitors whose `release` label matches the Helm release name — **`release: kube-prometheus-stack` is required**. SMs without it are silently ignored.

### Alertmanager config

Already in Git at `infra/controllers/kube-prometheus-stack/values.yaml:508–547`. Current routing: every alert → receiver `null`; one explicit route for `Watchdog` → `null`. No webhook receivers configured. There is no out-of-Git ConfigMap to discover.

### kube-state-metrics

Deployed (`infra/controllers/kube-prometheus-stack/values.yaml`: `kubeStateMetrics: true`). Emits `kube_pod_status_ready`, `kube_node_status_condition`, `kubelet_volume_stats_used_bytes`/`_capacity_bytes`, `kube_deployment_status_replicas_available`, etc. Anything we'd want from a hand-rolled "healthcheck exporter" for pod/node/PVC state is already there.

## Phase 1 — ServiceMonitor coverage

### 1.1 Audit (must precede §1.3)

For each of the 23 workload apps, determine whether the running image exposes a Prometheus `/metrics` endpoint and on what port. Record findings in the PR description as a table:

| app | exposes-metrics | port name | port number | sample metric | source |
|-----|-----------------|-----------|-------------|---------------|--------|

The rule: **only create an SM for an app that actually exposes `/metrics`.** Pod readiness, restart counts, and uptime for apps without `/metrics` are already covered by kube-state-metrics. Adding an SM that scrapes a 404 produces permanent `up=0` noise and false alerts.

Best-effort triage (must verify image-by-image — do not skip):

- **Likely yes:** `hermes`, `hermes-callee`, `golinks` (verify), `authelia` (existing SM), `signal-cli` (existing SM), `synology-iscsi-monitor` (existing), `truenas-iscsi-monitor` (existing), `overture` (verify), `vitals` (small custom exporter — verify).
- **Conditional / requires upstream config flag or sidecar:** `homeassistant` (Prometheus integration must be enabled in `configuration.yaml`), `jellyfin` (community plugin only), `immich` (no native endpoint).
- **Likely no:** `navidrome`, `audiobookshelf`, `snapcast`, `mealie`, `linkding`, `memos`, `homepage`, `excalidraw`, `adguard`, `openwebui` (verify), `cloudflare-tunnel`.

The audit determines the actual count of new SMs. Do not commit to "N apps need SMs" until §1.1 is done.

### 1.2 Fix existing SMs

- `apps/base/authelia/servicemonitor.yaml`: add `release: kube-prometheus-stack` to `metadata.labels`. Verify discovery before/after with `kubectl get servicemonitor -A -l release=kube-prometheus-stack` and the Prometheus targets page.
- `apps/base/signal-cli/servicemonitor.yaml`: confirm the label is present; add if missing.

### 1.3 Create new SMs (count from §1.1 audit)

Canonical template. The `release` label is **required**:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: <app>
  namespace: <namespace>
  labels:
    app: <app>
    release: kube-prometheus-stack
spec:
  selector:
    matchLabels:
      app: <app>
  endpoints:
    - port: <metrics-port-name>
      path: /metrics
      interval: 30s
      scrapeTimeout: 10s
```

Use a uniform `interval: 30s` until a measured reason exists to vary. The existing synology-iscsi-monitor SM at 60s stays as-is — it's tuned for that exporter's scrape cost. The `scrape-priority` label introduced in earlier drafts is dropped: there is no Grafana variable that consumes it, and label proliferation without a consumer is a maintenance liability. Reintroduce only with a concrete dashboard query that uses it.

### 1.4 Wiring

For each new SM, add `- servicemonitor.yaml` to `apps/base/<app>/kustomization.yaml`. Then `kustomize build apps/production/<app>` must pass before pushing.

### 1.5 Verify

- `kubectl get servicemonitor -A -l release=kube-prometheus-stack` lists the expected count.
- Prometheus targets page (Grafana → Explore → Prometheus datasource → `up{}`) shows each SM's target as UP.
- No `up=0` for any new SM. A persistent `up=0` means the audit in §1.1 was wrong for that app — remove the SM rather than chase the 404.

## Phase 2 — Critical-alert routing to Signal

### 2.1 Pin down the bridge API

Inspect the running signal bridge container to confirm:

- The image is `bbernhard/signal-cli-rest-api` or a custom hermes bridge.
- The exact endpoint shape (`bbernhard` exposes `/v2/send` with JSON `{number, recipients[], message}`).
- The in-cluster Service DNS name (likely `signal-cli-rest-api.signal-cli.svc.cluster.local`).
- Whether auth is required (in-cluster traffic is typically open).

Source: read `apps/base/signal-cli/deployment.yaml` and the upstream image docs. Hermes already calls this endpoint — its source is the authoritative reference.

### 2.2 Pick routing strategy

**Option A — direct Alertmanager webhook to signal-cli-rest-api.** Tempting but does not work cleanly: Alertmanager's webhook payload is `{alerts: [{labels, annotations, status, …}, …]}`, while signal-cli-rest-api expects `{number, recipients, message}`. Alertmanager `webhook_configs` has no body-templating field — only the URL. A direct POST will fail or render an unreadable payload.

**Option B (chosen) — small relay.** A ~50-line Go or Python service receiving Alertmanager webhooks, formatting one Signal message per firing alert (grouped by `alertname` + `namespace`), and POSTing to signal-cli-rest-api. Deploy alongside signal-cli in the same namespace, reusing its credentials secret.

Files to create:

```
apps/base/alertmanager-signal-relay/
├── kustomization.yaml
├── deployment.yaml         # single container, /alert POST endpoint, /metrics endpoint
├── service.yaml            # ClusterIP :8080
├── servicemonitor.yaml     # so the relay's own delivery counters are scraped
└── README.md
apps/production/alertmanager-signal-relay/
└── kustomization.yaml
```

The relay exposes `alertmanager_signal_relay_messages_sent_total` and `_failed_total` counters. We alert on `_failed_total > 0` to avoid silent swallowing of critical alerts (see §2.5).

### 2.3 Update values.yaml

Replace the `config:` block at `infra/controllers/kube-prometheus-stack/values.yaml:508`:

```yaml
config:
  global:
    resolve_timeout: 5m
  inhibit_rules:
    # …existing inhibit rules unchanged…
  route:
    group_by: ['namespace', 'alertname']
    group_wait: 30s
    group_interval: 5m
    repeat_interval: 12h
    receiver: 'null'
    routes:
      - receiver: 'null'
        matchers:
          - alertname = "Watchdog"
      - receiver: 'signal-critical'
        matchers:
          - severity = "critical"
        continue: false
  receivers:
    - name: 'null'
    - name: 'signal-critical'
      webhook_configs:
        - url: http://alertmanager-signal-relay.signal-cli.svc.cluster.local:8080/alert
          send_resolved: true
  templates:
    - '/etc/alertmanager/config/*.tmpl'
```

### 2.4 Test

1. Lower the threshold on an existing alert rule to force a critical fire (or pick one already firing — query `ALERTS{severity="critical",alertstate="firing"}`).
2. Confirm the Signal message arrives within 5 minutes.
3. Restore the threshold; confirm the resolved message arrives.

### 2.5 Self-monitoring (mandatory)

Add to `infra/configs/alerts/prometheus-rules.yaml`:

```yaml
- alert: AlertmanagerSignalRelayFailing
  expr: increase(alertmanager_signal_relay_messages_failed_total[5m]) > 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Signal alert relay failing — critical alerts may be silently dropped"
```

Without this, a relay outage swallows every critical alert and we'd never know.

## Phase 3 — PrometheusRules for Flux reconciliation

Pre-flight: confirm `gotk_reconcile_condition` is being scraped. The Flux controllers expose Prometheus metrics on port 8080. If `up{job=~".*flux.*"}` returns nothing, add a ServiceMonitor for the Flux controllers under `infra/controllers/flux-system/` first; otherwise the rules below evaluate to `vector()` and never fire.

Add to `infra/configs/alerts/prometheus-rules.yaml`:

```yaml
- alert: FluxKustomizationNotReady
  expr: max by (name, namespace) (gotk_reconcile_condition{type="Ready",status="False",kind="Kustomization"}) == 1
  for: 15m
  labels:
    severity: critical
  annotations:
    summary: "Flux Kustomization {{ $labels.namespace }}/{{ $labels.name }} not ready"

- alert: FluxHelmReleaseNotReady
  expr: max by (name, namespace) (gotk_reconcile_condition{type="Ready",status="False",kind="HelmRelease"}) == 1
  for: 15m
  labels:
    severity: critical
  annotations:
    summary: "Flux HelmRelease {{ $labels.namespace }}/{{ $labels.name }} not ready"

- alert: FluxGitRepositoryNotReady
  expr: max by (name, namespace) (gotk_reconcile_condition{type="Ready",status="False",kind="GitRepository"}) == 1
  for: 15m
  labels:
    severity: critical
  annotations:
    summary: "Flux GitRepository {{ $labels.namespace }}/{{ $labels.name }} not ready"
```

The 15m `for` window suppresses noise from transient reconciliation failures during the very rollouts that touch these rules.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Adding `release` label to authelia SM begins firing alerts that were silently suppressed before | Medium | Low | Add the SM fix in its own commit; observe one hour before adding any new rules. |
| Relay outage silently drops critical alerts | Medium | High | `AlertmanagerSignalRelayFailing` rule (§2.5). |
| Flux rules fire during this PR's own rollout | Medium | Medium | `for: 15m` window already mitigates; merge rules after relay is verified. |
| `up=0` from an SM whose target lacks `/metrics` | Medium | Low | §1.5 explicitly checks; remove SM rather than chase. |

## Rollback

Each phase is independently revertable. Default Kustomization interval is **10 minutes** (`clusters/melodic-muse/apps-production.yaml`, `clusters/melodic-muse/infra.yaml`). To force immediately:

- §1 SMs: `git revert <commit>`; `flux reconcile kustomization apps-production -n flux-system`.
- §2 values.yaml + relay: `git revert`; `flux reconcile helmrelease kube-prometheus-stack -n monitoring` and `flux reconcile kustomization apps-production -n flux-system`.
- §3 rules: `git revert`; Prometheus reloads via the operator within ~1 minute, no manual flux reconcile required.

## Success criteria

- Each app the §1.1 audit identifies as exposing `/metrics` has a discovered SM; `up{job=~"<app>.*"} == 1` on the Prometheus targets page.
- `kubectl get servicemonitor -A -l release=kube-prometheus-stack` returns the expected count (4 existing + N from audit).
- A test `severity=critical` alert delivers a Signal message within 5 minutes; `_resolved` follows when the threshold is restored.
- `alertmanager_signal_relay_messages_failed_total` stays at 0 over a 24h window after the rollout.
- `flux get kustomizations -A` is green for every Kustomization touched.

## Out of scope (deliberately)

- **New Grafana dashboards.** Already covered and complete in `2026-02-21-app-health-dashboards-plan.md` and `2026-02-21-cluster-health-dashboards-plan.md`.
- **Custom healthcheck exporter.** kube-state-metrics already emits pod readiness, node status, and PVC capacity. Black-box HTTP probing of app endpoints, if later wanted, is `prometheus-blackbox-exporter` (a kube-prometheus-stack subchart) — separate, smaller plan.
- **Loki-based log alerting.** Separate initiative.
- **Multi-cluster federation, Thanos, pushgateway.** Premature for a 6-node single-cluster homelab.
