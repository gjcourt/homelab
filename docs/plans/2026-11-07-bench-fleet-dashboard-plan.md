---
status: planned
last_modified: 2026-11-07
summary: "Grafana dashboard for the bench-cloud agent fleet (Plane HOMELAB, George-only — outside the bench gate): Phase 1 throughput + utilization-vs-cap from kube-state-metrics Job labels, Phase 2 cost/token economics via a new bench-metrics-exporter over the per-run result.json; sliced by playbook/project/model/outcome/time"
---

# agent-bench fleet throughput & utilization dashboard

A Grafana dashboard for the bench-cloud agent fleet: how much work the fleet is
doing, how hard it is being driven (utilization against the caps), how it is
succeeding or failing, and what it costs — sliced by **playbook, project, model,
outcome and time**. Scope chosen by George, 2026-11-07: **Phases 1 + 2** (Phase 3,
per-run Loki drill-down, is deferred — see "Deferred").

## Goal

Answer three questions at a glance:

1. **Throughput** — how many agent Jobs start/finish per hour and per day, by
   playbook and project; are they succeeding?
2. **Utilization over time** — how full is the fleet? Concurrent running Jobs per
   playbook against the configured caps, as a time series, so idle capacity and
   saturation are both visible.
3. **Economics** — what is the cloud bench costing, in dollars and tokens, by
   project / playbook / model, and what do failures cost?

## What already exists (verified 2026-11-07)

- **Prometheus + Grafana** via `infra/controllers/kube-prometheus-stack`
  (kube-state-metrics subchart enabled). Grafana auto-loads any ConfigMap in the
  `monitoring` namespace labelled `grafana_dashboard: "1"`; ~17 dashboards live in
  `infra/configs/dashboards/*-cm.yaml`. **A new dashboard is one new ConfigMap.**
- **bench-cloud Jobs** (namespace `bench-cloud`, `app=bench-task`) already carry
  the labels we need: `agent-desk/playbook` (feature | design | docs-fix | review),
  `agent-desk/ticket`, `bench-cloud/task`. The Job name encodes the project:
  `ad-<project>-<seq>-<yyyymmdd>-<hash>` (e.g. `ad-domestique-15-20261107-d3165e`).
- **Per-run `result.json`** persists on hestia NFS at
  `/mnt/main/agent-inbox/runs/<task>/result.json`. Fields present today:
  `total_cost_usd`; `usage.{input_tokens, output_tokens, cache_creation_input_tokens,
  cache_read_input_tokens, output_tokens_details.thinking_tokens}`;
  `modelUsage.<model>.{inputTokens, outputTokens, cacheReadInputTokens,
  cacheCreationInputTokens, costUSD}`; `duration_ms`, `duration_api_ms`;
  `num_turns`; `subagent_stats.{spawned, completed, failed}`; `is_error`;
  `terminal_reason`; `stop_reason`; `result` (the summary text); `session_id`.
- **Caps and models** (from `apps/production/agent-desk/config.yaml`): feature 8 /
  Opus / 3h, docs-fix 6 / Haiku / 90m, design 2 / Opus / 2h, review 1 / Opus / 90m.
- **kube-state-metrics** exposes `kube_job_*` (active / succeeded / failed /
  complete / start_time / completion_time / created). It already uses
  `customResourceState` for Flux, so the config surface is familiar.

## The gap

Two pieces of instrumentation are missing before panels can be drawn:

1. kube-state-metrics does **not** expose the custom Job labels by default, so
   `kube_job_labels` carries no `agent-desk/playbook`. → **label allowlist** (Phase 1).
2. None of the cost / token / outcome detail in `result.json` reaches Prometheus
   at all. → a small **`bench-metrics-exporter`** (Phase 2).

Everything below is `infra/**` plus a new `apps/**` service that touches
bench-cloud internals — **outside the agent bench gate's allowed paths**
(`scripts/bench-gate/rules.yaml`). Bench agents cannot build any of it; it is
George-driven. The Plane tickets are tracked on George's list and never get `ready`.

---

## Phase 1 — Throughput & utilization (kube-state-metrics only, no new service)

### 1a. Expose Job labels

Add to `infra/controllers/kube-prometheus-stack/values.yaml`, under the
`kube-state-metrics:` subchart block:

```yaml
kube-state-metrics:
  metricLabelsAllowlist:
    - jobs=[agent-desk/playbook,bench-cloud/task,agent-desk/ticket]
```

This makes `kube_job_labels{job_name, label_agent_desk_playbook,
label_bench_cloud_task, label_agent_desk_ticket}` available to join against the
`kube_job_status_*` series. (Label keys are sanitised to `label_<key_with_underscores>`.)

### 1b. Recording rules for project + duration

Add `infra/configs/alerts/` (or a new recording-rules file) rules that:

- Derive **project** from the Job name: `label_replace(kube_job_labels, "project",
  "$1", "job_name", "ad-([a-z0-9]+)-.*")`.
- Precompute **job duration**:
  `kube_job_status_completion_time - kube_job_status_start_time`.

Recording rules keep the dashboard queries cheap and make "by project" a first-class
label even though it is encoded in the name rather than a Kubernetes label.

### 1c. Dashboard `infra/configs/dashboards/bench-fleet-cm.yaml`

ConfigMap `bench-fleet-dashboard`, namespace `monitoring`, label
`grafana_dashboard: "1"`, uid `bench-fleet`, tags `["homelab","bench-cloud","agents"]`,
templated variables for `$playbook`, `$project`, `$outcome` (multi-select, "All").

Rows / panels:

- **Fleet overview** — stat tiles: active Jobs now, Jobs completed last 24h, success
  rate last 24h, $ last 24h (Phase 2 fills the last). Timeseries: Jobs started vs
  completed vs failed per hour.
- **Utilization over time** — concurrent running Jobs
  (`sum by (playbook) (kube_job_status_active * on(job_name) group_left(playbook)
  kube_job_labels)`) as a stacked area **against cap threshold lines** (feature 8,
  docs-fix 6, design 2, review 1); a companion "% of slots used" gauge per playbook.
  This is the panel that answers "utilization over time."
- **Outcome** — success vs failed vs backoff over time; success-rate table by
  playbook and by project. Flag `BackoffLimitExceeded` runs (the subscription-limit
  stalls) distinctly — they explain throughput dips.
- **Duration** — p50/p95 Job runtime by playbook (from the recording rule), plus a
  duration heatmap; overlay each playbook's deadline (90m / 2h / 3h) as a reference.
- **Throughput by project** — completed Jobs/day per repo (bar), and a table of
  Jobs-per-project over the dashboard window.

**Deliverable:** one values.yaml edit, one recording-rules edit, one dashboard
ConfigMap. No new workloads. Historical depth is bounded by Prometheus retention.

---

## Phase 2 — Cost & token economics (`bench-metrics-exporter`)

### 2a. The exporter

A small service that turns `result.json` files into Prometheus metrics. New app
under `apps/base/bench-metrics-exporter/` + `apps/production/bench-metrics-exporter/`
(and a `images/bench-metrics-exporter/` build), in or beside the `bench-cloud`
namespace.

Design:

- **Input:** read-only mount of the agent-inbox runs directory
  (`/mnt/main/agent-inbox/runs/`, the same NFS the Jobs write).
- **Stateless rescan with an mtime cache:** on each scrape, walk the run dirs,
  parse each `result.json` once (cache by path+mtime), and **sum into aggregated
  series**. Because run dirs persist on NFS, rebuilt sums are effectively monotonic;
  no cursor/state file needed. Cardinality is bounded (project × playbook × model ×
  outcome ≈ low hundreds worst case).
- **The one missing dimension is `playbook`** — it is not in the task name and not
  in `result.json`. Resolve it one of two ways:
  - **Preferred:** have bench-cloud write a one-line `meta.json`
    (`{project, playbook, ticket, model, task}`) into the run dir when it launches
    the Job. Decouples the exporter from Job TTL entirely (pure filesystem, no K8s
    API, full history survives Job GC).
  - **Fallback (no bench-cloud change):** the exporter lists Jobs via the K8s API
    (`app=bench-task`) to map task → playbook; loses playbook for runs whose Job has
    already been TTL-deleted. Acceptable if the meta.json change is deferred.

Metrics exposed:

```
bench_runs_total{project,playbook,model,outcome}                 # counter
bench_run_cost_usd_total{project,playbook,model}                 # counter
bench_run_tokens_total{project,playbook,model,kind}              # kind: input|output|cache_read|cache_creation|thinking
bench_run_duration_seconds_bucket{project,playbook,le}           # histogram
bench_run_turns_bucket{project,playbook,le}                      # histogram
bench_run_subagents_spawned_total{project,playbook}             # counter
```

Scraped via a `ServiceMonitor` (Phase-1 Prometheus already selects them).

### 2b. Dashboard rows added to `bench-fleet-cm.yaml`

- **Spend** — $/day timeseries; $ by project, by playbook, by model (stacked); the
  24h-$ stat tile from Phase 1 now populated.
- **Cost drivers** — token volume by kind, and the **cache-read ratio**
  (`cache_read / (input+cache_read+cache_creation)`) — cache efficiency is what
  actually moves the bill (a single observed Opus run was $3.38 on 4.9M cache-read
  tokens). Cost-per-1k-output-tokens by model.
- **Cost per outcome** — $ spent on `is_error` runs vs successful runs (what retries
  and failures cost), and a rough **cost-per-completed-Job** by playbook.
- **Effort shape** — turns p50/p95 and subagent fan-out by playbook, to see which
  playbooks are expensive in iterations, not just dollars.

**Deliverable:** new exporter image + base/prod kustomize + ServiceMonitor; the
optional one-line bench-cloud `meta.json` write; dashboard rows appended.

---

## Issue breakdown for Plane (project HOMELAB)

One parent + five children. All George-only (no `ready`). "Depends on:" lines drive
`desk.py deps` ordering.

- **Parent / epic** — "Bench fleet throughput & utilization dashboard" — links this
  plan; checklist of the five below.
- **BENCH-DASH-1 (Phase 1a)** — kube-state-metrics Job-label allowlist. `infra`.
- **BENCH-DASH-2 (Phase 1b)** — recording rules: project-from-name + job duration.
  Depends on: BENCH-DASH-1.
- **BENCH-DASH-3 (Phase 1c)** — `bench-fleet-cm.yaml` dashboard: throughput,
  utilization-vs-cap, outcome, duration, by-project rows. Depends on: BENCH-DASH-2.
- **BENCH-DASH-4 (Phase 2a)** — `bench-metrics-exporter` (image + base/prod kustomize
  + ServiceMonitor) and the optional bench-cloud `meta.json` write. Depends on:
  BENCH-DASH-1.
- **BENCH-DASH-5 (Phase 2b)** — append spend / cost-driver / cost-per-outcome /
  effort rows to the dashboard. Depends on: BENCH-DASH-3, BENCH-DASH-4.

## Deferred

- **Phase 3 — per-run Loki drill-down.** Ship each `result.json` to Loki labelled
  {project, playbook, model, outcome}; a Grafana table panel clicks into any single
  run (cost, tokens, turns, the `result` summary). Most build for least daily value;
  revisit once Phases 1–2 are in use.

## Open questions for George

1. **`meta.json` write in bench-cloud, or K8s-API fallback for playbook?** The
   write gives full historical playbook attribution that survives Job GC, at the
   cost of a one-line change in the bench-cloud launcher. Recommendation: do the
   write — it is small and the fallback permanently loses playbook on old runs.
2. **Alerting?** This plan is dashboards only. Worth a follow-up: alert on fleet
   success-rate drop, a cost/day threshold, or sustained backoff-limit saturation?

## Orchestrator notes (not George's decisions)

- Project-from-name regex assumes the `ad-<project>-…` naming holds; if a project
  slug ever contains a hyphen the regex needs widening.
- Exporter cardinality is safe at current project/playbook/model counts; if
  per-run series are ever wanted, that is Loki (Phase 3), not a new Prometheus label.
- The dashboard ConfigMap can exceed the 1 MiB ConfigMap limit if over-built; keep
  panels lean or split Phase 2 rows into a second ConfigMap if needed.
