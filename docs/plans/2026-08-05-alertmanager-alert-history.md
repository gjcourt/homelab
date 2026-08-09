---
status: planned
last_modified: 2026-08-05
summary: "Fired-alert history: Grafana state-timeline over Prometheus ALERTS vs. a notification-log webhook receiver"
---

# Plan: fired-alert history

## Context

Two problems have been filed under one bullet in [STATUS.md](../STATUS.md), and
conflating them is why the wrong fix keeps looking attractive:

1. **Alertmanager state durability** — silences and the notification log (dedup /
   inhibition memory) lived on `emptyDir`, so every restart dropped them. **Fixed
   separately** by running 2 gossip replicas with hard anti-affinity, NOT by a
   volume (`infra/controllers/kube-prometheus-stack/values.yaml`); a PVC here
   would couple alert delivery to hestia. Residual gap and a deadman proposal:
   [2026-08-08-alertmanager-local-durability.md](2026-08-08-alertmanager-local-durability.md).
2. **Fired-alert history** — "what fired last week, and for how long?" **This
   plan.** Persistence does *not* address it and never could.

**Alertmanager stores no alert history, by design, regardless of storage.** Its
API exposes only currently-active alerts; `/api/v2/alerts` is a snapshot of what
Prometheus is asserting right now. The on-disk state a PVC preserves is the
silences file plus `nflog`, and `nflog` is a *dedup ledger* — entries keyed by
receiver + alert-group fingerprint, pruned at `alertmanagerSpec.retention`
(120h), holding the last-notified timestamp and nothing about what came before.
It is not a queryable audit log and the UI never surfaces it. No amount of
storage would change the empty history page.

So the history has to come from somewhere else. Two different questions, with
two different sources:

| Question | Who knows the answer today |
|---|---|
| **What *fired*?** (rule evaluation) | Prometheus — it already writes `ALERTS` / `ALERTS_FOR_STATE` for every pending/firing alert |
| **What was I actually *told* about?** (delivery, post-silence/inhibit/route) | Nobody. The only record is the `gjcourt+alerts@` / `gjcourt+critical@` mailbox |

Almost every real use ("did the cert alert ever fire?", "how noisy is
`KubePersistentVolumeFillingUp`?", "when did `.20` start flapping?") is the first
question. That matters for the recommendation.

### Constraints as built (verified 2026-08-05)

| Component | Setting | Consequence for this plan |
|---|---|---|
| Prometheus | `retention: 10d`, `retentionSize: 15GiB`, `replicas: 2`, no `remoteWrite`, no Thanos | `ALERTS` history is capped at ~10 days; series are per-replica and must be deduped in the query |
| Loki | filesystem storage, `retention_period: 7d`, 40Gi PVC | Any log-based approach gets a *shorter* window than Prometheus |
| Grafana | 12.2.0, dashboards provisioned by the kiwigrid sidecar from ConfigMaps labelled `grafana_dashboard: "1"` in `infra/configs/dashboards/` | A dashboard is a single ConfigMap + one line in a kustomization — the cheapest unit of work in this repo |
| Alertmanager | `replicas: 1`, `retention: 120h`, default route → `null` receiver | Alerts hitting the default route are dropped with no trace anywhere except `ALERTS` |

## Options

### A. Grafana state-timeline over `ALERTS` / `ALERTS_FOR_STATE`

Add one dashboard ConfigMap. `ALERTS{alertname, alertstate, severity, ...}` is
emitted by Prometheus for the lifetime of every pending/firing alert;
`ALERTS_FOR_STATE` carries the wall-clock timestamp the alert became active
(written so Prometheus can restore `for:` state across restarts, but a perfectly
ordinary series to query). A state-timeline panel over the first and a table over
the second reconstructs "what fired, when, and for how long".

- **Effort:** an afternoon. No new runtime components, no new image, no PVC, no
  build workflow, nothing new to alert on.
- **Window:** 10 days (Prometheus retention). Extendable — see B.
- **Honest limits:** it answers "what *fired*", not "what was *delivered*".
  A silenced, inhibited, or `null`-routed alert is indistinguishable from a
  paged one. `ALERTS` also disappears the instant a rule is deleted or renamed,
  and the 2 Prometheus replicas each write their own copy (dedupe with
  `max by (...)`, not `sum`).

### B. Widen the Prometheus retention window

Not an alternative to A — a knob on top of it. Vanilla Prometheus has one global
retention; there is no way to keep `ALERTS` longer than everything else without
a second store. Going 10d → 30d means growing the 20Gi PVCs and raising
`retentionSize`, ×2 replicas. Note the `storageClassName` / `volumeClaimTemplate`
immutability tax on StatefulSets (see `docs/operations/2026-05-02-flux-debugging.md`)
— though as of #1019 iSCSI PVCs do expand in place, so a size bump is now cheap.

- **Effort:** minutes of config, ~40Gi more on the pool.
- **Verdict:** do it *if* 10 days proves too short in practice. Don't pre-buy it.

### C. Webhook receiver persisting notifications

Point an Alertmanager `webhook_configs` receiver at a small service that records
every notification it is handed. This is the only option that answers the second
question — it sees exactly what was delivered, to which receiver, after silences
and inhibition were applied.

- **C-full:** own service + image + `build-*.yml` workflow + deployment + probes
  + a CNPG database or a 1Gi sqlite PVC + a UI or a Grafana datasource + an
  alert for when the recorder itself dies. A day or more, and permanent
  maintenance surface, for a question that is asked far less often than "what
  fired".
- **C-lite:** an off-the-shelf webhook→stdout logger (e.g.
  `alertmanager-webhook-logger`), scraped by the existing promtail → Loki →
  Grafana Logs panel. No PVC, no database, no custom image. ~half a day. Capped
  at Loki's 7d retention, which is *worse* than Prometheus's 10d — so it is a
  complement for delivery evidence, never a replacement for A.
- **Side benefit worth noting:** if C-lite lands, the `null` default route can be
  repointed at the logger, so unmatched alerts stop vanishing silently (the
  #1080-class gap) without turning into email noise.

### D. Migrate to Grafana-managed alert rules for Grafana's alert state history

Grafana 12 keeps state history for rules it manages. The homelab's rules are
`PrometheusRule` CRs evaluated by Prometheus — Grafana can *display* those rules
but does not own their state, so no history. Getting history would mean rewriting
the rule corpus into Grafana-managed rules, abandoning the GitOps-native
`PrometheusRule` pattern and the upstream kube-prometheus-stack rule set.
**Reject** — enormous change, worse fit, and it would still not record delivery.

### E. `remoteWrite` to a long-term store (VictoriaMetrics / Mimir / Thanos)

Solves metric retention generally, of which alert history is a rounding error.
A new stateful system to run and back up. **Reject for this purpose** — revisit
only if long-term *metrics* become a goal in their own right.

## Recommendation

**Do A. Consider B if 10 days bites. Add C-lite only if "was I actually
notified?" keeps coming up in practice. Skip D and E.**

A costs one ConfigMap, uses a pattern this repo already runs 15 times over,
introduces no new failure domain, and answers the question that is actually
being asked. Its limits are real but they are *stateable* — and stating them in
the panel description is cheaper than building C to make them go away. Building
a notification recorder first would be paying a day of work plus permanent
operational surface to answer the rarer of the two questions, while still
needing A for the common one.

## Implementation sketch (not done here)

**Phase 1 — the dashboard.** `infra/configs/dashboards/alert-history-cm.yaml`,
labelled `grafana_dashboard: "1"`, added to that directory's
`kustomization.yaml`. Panels:

- *State timeline* — `max by (alertname, severity, namespace) (ALERTS{alertstate="firing"})`,
  one row per alert, over the dashboard time range.
- *Recent firings table* — `max by (alertname, namespace) (ALERTS_FOR_STATE)`,
  value rendered as a timestamp ("started at"), sorted descending.
- *Noisiest alerts* — firing minutes per alert over the range, e.g.
  `sort_desc(count_over_time(ALERTS{alertstate="firing"}[$__range]))`, to drive
  alert-tuning decisions.
- *Pending vs firing* — same series filtered on `alertstate="pending"`, to spot
  rules whose `for:` is mistuned.
- Template variables for `severity` and `namespace`; a text panel stating the
  10-day window and the fired-≠-notified caveat, so the dashboard is not
  mistaken for a delivery audit.

**Phase 2 (conditional) — retention.** Only if Phase 1 shows 10 days is too
short: bump `retention` and `retentionSize`, expand the PVCs in place.

**Phase 3 (conditional) — C-lite.** Webhook logger + Loki panel, and repoint the
`null` default route at it.

## Verification

- `kustomize build infra/configs` passes.
- Sidecar picks the ConfigMap up:
  `kubectl -n monitoring logs deploy/kube-prometheus-stack-grafana -c grafana-sc-dashboard | grep alert-history`.
- Cross-check against known history: `Watchdog` must show as continuously firing
  for the whole range (it always fires by design), and the cert-manager alerts
  added in #1275 must line up with the 2026-08-05 wildcard-expiry incident.
- Confirm the 2-replica dedup works — each alert renders one row, not two.

## Out of scope

- **Alertmanager state persistence** — separate change, already made.
- **Alert *delivery*** — the `null` default receiver, the Gmail skip-inbox
  filter on `+alerts@`, and the intentional `null` mutes are routing problems,
  not history problems. Tracked in [STATUS.md](../STATUS.md) and
  [2026-06-17-alertmanager-smtp-alerting.md](2026-06-17-alertmanager-smtp-alerting.md).
- **Long-term metrics storage** (option E).
- **Alert rule tuning** — the noisiest-alerts panel exists to *inform* it; the
  tuning itself is separate work.

## Open questions

- Is a 10-day window enough for how this is actually used? Cheapest way to find
  out is to ship Phase 1 and notice when the range runs out.
- Is the "what was I notified about?" question real, or is the mailbox already a
  sufficient record of delivery? C stays unbuilt until that answer is yes.
