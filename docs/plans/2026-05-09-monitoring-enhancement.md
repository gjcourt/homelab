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
| `apps/base/signal-cli/servicemonitor.yaml` | **No** — broken discovery | Same fix as authelia; see §1.2 |
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
| authelia | yes | metrics | 9959 | `authelia_authentication_attempts_total` | https://www.authelia.com/reference/guides/metrics/ |
| immich | no | n/a | n/a | n/a | inspected `apps/base/immich/deployment.yaml` — no metrics port; upstream docs make no Prometheus claim |

The rule: **only create an SM for an app that actually exposes `/metrics`.** Pod readiness, restart counts, and uptime for apps without `/metrics` are already covered by kube-state-metrics. Adding an SM that scrapes a 404 produces permanent `up=0` noise and false alerts.

**Hints, not the audit result — verify each before drawing conclusions.** The grouping below is a starting point from documentation review; upstream behavior may have changed.

- _Hints toward yes:_ `hermes`, `hermes-callee`, `golinks`, `authelia` (SM exists), `signal-cli` (SM exists), `synology-iscsi-monitor` (SM exists), `truenas-iscsi-monitor` (SM exists), `overture`, `vitals`.
- _Hints toward conditional (upstream config flag or sidecar):_ `homeassistant` (Prometheus integration must be enabled in `configuration.yaml`), `jellyfin` (community plugin only), `immich` (no native endpoint).
- _Hints toward no:_ `navidrome`, `audiobookshelf`, `snapcast`, `mealie`, `linkding`, `memos`, `homepage`, `excalidraw`, `adguard`, `openwebui`, `cloudflare-tunnel`.

The audit determines the actual count of new SMs. Do not commit to "N apps need SMs" until §1.1 is done.

### 1.2 Fix existing SMs

- `apps/base/authelia/servicemonitor.yaml`: add `release: kube-prometheus-stack` to `metadata.labels`. Verify discovery before/after with `kubectl get servicemonitor -A -l release=kube-prometheus-stack` and the Prometheus targets page.
- `apps/base/signal-cli/servicemonitor.yaml`: same fix — `metadata.labels` currently has only `{app: signal-cli}`. Add `release: kube-prometheus-stack`.

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

**Order of operations:** §2.1 audit → §2.2 deploy relay and smoke-test → §2.3 enable webhook in Alertmanager → §2.4 end-to-end test → §2.5 self-monitoring rule. Flipping the webhook (§2.3) before the relay is up will cause Alertmanager delivery timeouts.

### 2.1 Pin down the bridge API

Verified against `apps/base/signal-cli/`:

- **Image:** `ghcr.io/gjcourt/signal-bridge:2026-05-03` (custom bridge built in this org, not `bbernhard/signal-cli-rest-api`). The API is whatever that image exposes — read the source to determine the endpoint shape; do not assume the bbernhard `/v2/send` contract applies.
- **Service:** `signal-cli-bridge.signal-cli.svc.cluster.local:8080` (per `apps/base/signal-cli/service.yaml`; the Service is named `signal-cli-bridge`).
- **Auth:** in-cluster traffic is open by default; confirm the bridge container does not enforce a bearer token before relying on this.

Action: read the gjcourt/signal-bridge image source (or its README) to record the actual `POST` path and JSON body shape. Hermes already calls this endpoint — its code is the authoritative reference for the contract.

### 2.2 Pick routing strategy

**Option A — direct Alertmanager webhook to signal-cli-bridge.** Doesn't work, regardless of bridge contract: Alertmanager `webhook_configs` posts a fixed `{alerts: [{labels, annotations, status, …}, …]}` JSON body to the configured URL, with **no body-templating field**. Whatever shape signal-cli-bridge accepts, Alertmanager won't produce it directly. A relay is required to translate the payload.

**Option B (chosen) — small relay.** A ~50-line Python service mirroring the *script structure* of `apps/base/synology-iscsi-monitor/script-cm.yaml` — ConfigMap-embedded script, `prometheus_client` for metrics, plain `http.server` or `aiohttp` for the webhook handler. Placement differs from synology-iscsi-monitor (relay is alerting infrastructure, not a workload). It receives Alertmanager webhooks, formats one Signal message per firing alert (grouped by `alertname` + `namespace`), and POSTs to signal-cli-bridge.

**Placement:** `infra/controllers/alertmanager-signal-relay/` in the existing `monitoring` namespace, colocated with kube-prometheus-stack/Alertmanager. Rationale: this is alerting plumbing, not a user-facing workload — it belongs with the monitoring stack, not under `apps/`. No HelmRelease (custom code, not an upstream chart); plain Kustomize.

**No staging overlay** — singleton relay, follows the synology-iscsi-monitor pattern. Production-only.

Files to create:

```
infra/controllers/alertmanager-signal-relay/
├── kustomization.yaml
├── deployment.yaml         # single container, /alert POST endpoint, /metrics endpoint
├── service.yaml            # ClusterIP :8080 in namespace monitoring
├── servicemonitor.yaml     # so the relay's own delivery counters are scraped
├── script-cm.yaml          # embedded Python relay script
└── README.md
```

Then add `- alertmanager-signal-relay` to `infra/controllers/kustomization.yaml`.

**Configuration** (env vars, no secrets — the bridge holds the Signal credentials, the relay only needs the bridge URL):

- `SIGNAL_BRIDGE_URL` — `http://signal-cli-bridge.signal-cli.svc.cluster.local:8080/<path>` where `<path>` is whatever endpoint the gjcourt/signal-bridge image exposes (resolved in §2.1).
- `SIGNAL_RECIPIENT_NUMBER` — destination phone number, sourced from a non-secret ConfigMap value or hardcoded in the manifest (it's a phone number, not a credential).
- `SIGNAL_FROM_NUMBER` — sender, same source.

**Webhook URL** for Alertmanager (used in §2.3): `http://alertmanager-signal-relay.monitoring.svc.cluster.local:8080/alert`.

The relay exposes `alertmanager_signal_relay_messages_sent_total` and `_failed_total` counters and is itself scraped via the SM above. §2.5 alerts on **both** `_failed_total > 0` (delivery errors while the relay is up) and `up == 0` (relay pod gone), so neither failure mode silently swallows critical alerts.

### 2.3 Update values.yaml

Edit `infra/controllers/kube-prometheus-stack/values.yaml` inside the `alertmanager.config:` block. The `inhibit_rules:` and `templates:` sections **must remain intact** — only modify `route:` and `receivers:`. Use anchoring text below, not line numbers, since both shift as the file is edited.

**Diff-style instruction (do not replace the whole `config:` block):**

1. **Update `config.route.group_by`** from `['namespace']` to `['namespace', 'alertname']`.

2. **Add a new route** to `config.route.routes:`, immediately after the existing Watchdog route (`- receiver: 'null'` with `matchers: alertname = "Watchdog"`):

   ```yaml
         - receiver: 'signal-critical'
           matchers:
             - severity = "critical"
           continue: false
   ```

3. **Add a new receiver** to `config.receivers:`, immediately after the existing `- name: 'null'` entry:

   ```yaml
       - name: 'signal-critical'
         webhook_configs:
           - url: http://alertmanager-signal-relay.monitoring.svc.cluster.local:8080/alert
             send_resolved: true
   ```

4. **Do not touch** the `inhibit_rules:` block or the `templates:` block. They stay as-is.

After editing, `git diff infra/controllers/kube-prometheus-stack/values.yaml` should show four small additions/edits and **no** changes to `inhibit_rules:` or `templates:`. If the diff shows any changes inside `inhibit_rules:` or `templates:`, revert and retry.

### 2.4 Test

1. Confirm the relay's metrics are exposed:
   ```bash
   kubectl -n monitoring port-forward svc/alertmanager-signal-relay 8080:8080 &
   PF=$!
   trap 'kill $PF 2>/dev/null' EXIT
   sleep 1
   curl -s localhost:8080/metrics | grep -E '^alertmanager_signal_relay_messages_(sent|failed)_total'
   kill $PF; trap - EXIT
   ```
   Both metric families must appear (even with value `0`). If either is missing, the relay was implemented incorrectly — the §2.5 self-monitoring rule depends on these names.
2. Lower the threshold on an existing alert rule to force a critical fire (or pick one already firing — query `ALERTS{severity="critical",alertstate="firing"}`).
3. Confirm the Signal message arrives within 5 minutes.
4. Restore the threshold; confirm the resolved message arrives.

### 2.5 Self-monitoring (mandatory)

Add **both** rules to `infra/configs/alerts/prometheus-rules.yaml` — together they catch delivery failures *and* full relay outages:

```yaml
- alert: AlertmanagerSignalRelayFailing
  expr: increase(alertmanager_signal_relay_messages_failed_total[5m]) > 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Signal alert relay failing — critical alerts may be silently dropped"

- alert: AlertmanagerSignalRelayDown
  expr: up{job="alertmanager-signal-relay"} == 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Signal alert relay scrape target is down — no critical alerts can be delivered"
```

The `Failing` rule covers the case where the relay is up but POSTs to signal-cli-bridge are erroring (counter increments). The `Down` rule covers the case where the relay pod is gone entirely — no metric increments, but `up=0`. Without both, a hard relay outage produces total silence.

## Phase 3 — PrometheusRules for Flux reconciliation

### 3.1 Pre-flight: confirm Flux metrics are scraped

The rules below depend on `gotk_reconcile_condition`, emitted by the Flux controllers. Before writing the rules:

1. Query Prometheus: `up{job=~".*flux.*"}` and `count(gotk_reconcile_condition)`.
2. If both return data, proceed to §3.2.
3. If either is empty, add a ServiceMonitor for the Flux controllers (under `infra/controllers/flux-system/` or wherever Flux is deployed in this repo). The Flux controllers conventionally expose metrics on port 8080, but verify against the actual Service spec before assuming. Without this scrape coverage, the rules below evaluate to `vector()` and never fire.

### 3.2 Add the rules

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
| Adding `release` label to authelia or signal-cli SM begins firing alerts that were silently suppressed before | Medium | Low | Land each SM label fix in its own commit; observe for one hour before adding any new rules. |
| Relay outage silently drops critical alerts | Medium | High | Both `AlertmanagerSignalRelayFailing` (delivery errors) and `AlertmanagerSignalRelayDown` (`up == 0`) rules in §2.5. |
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
- The relay's `/metrics` exposes both `alertmanager_signal_relay_messages_sent_total` and `_failed_total` (curl on `:8080/metrics` shows both metric families).
- `alertmanager_signal_relay_messages_failed_total` stays at 0 over a 24h window after the rollout.
- `up{job="alertmanager-signal-relay"} == 1` continuously over a 24h window.
- `flux get kustomizations -A` is green for every Kustomization touched.

## Out of scope (deliberately)

- **New Grafana dashboards.** Already covered and complete in `2026-02-21-app-health-dashboards-plan.md` and `2026-02-21-cluster-health-dashboards-plan.md`.
- **Custom healthcheck exporter.** kube-state-metrics already emits pod readiness, node status, and PVC capacity. Black-box HTTP probing of app endpoints, if later wanted, is `prometheus-blackbox-exporter` (a kube-prometheus-stack subchart) — separate, smaller plan.
- **Loki-based log alerting.** Separate initiative.
- **Multi-cluster federation, Thanos, pushgateway.** Premature for a 6-node single-cluster homelab.
