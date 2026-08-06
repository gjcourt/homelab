# Status

> **What this is.** A single-glance snapshot of the homelab: what's running,
> what's in flight, what's next, and what's broken. Detail lives elsewhere —
> this page links out. **Update this file in the same PR whenever** a plan's
> status changes, an incident postmortem lands, or hardware/topology changes.
>
> Last updated: 2026-08-05

## Cluster at a glance

| | |
|---|---|
| Cluster | `melodic-muse` |
| Nodes | 4 — 3 control-plane (`.20`/`.21`/`.23`) + 1 worker (`.25`); **`.20` hung since 2026-06-21** (etcd at 2/3) and `.22`/`.24` physically out (see Known issues) |
| Platform | Talos v1.12.4 · Kubernetes v1.35.0 |
| CNI / ingress | Cilium 1.19 (VXLAN) + Gateway API |
| GitOps | Flux CD, reconciling from `master` |
| Apps | ~14 self-hosted (see [apps/README.md](../apps/README.md)) |
| LB advertisement | L2 announcements + BGP to the UCGF (AS 65010 ↔ 65100) |

Full picture: [AGENTS.md](../AGENTS.md) · architecture in [docs/architecture/](architecture/README.md).

## Hosts at a glance

| Host | Role | Notes |
|---|---|---|
| 4× Talos nodes | Kubernetes cluster | `.20`/`.21`/`.23` (control-plane) + `.25` (worker) on the Lab VLAN, `10.42.2.x`. `.22` (bad DIMM) + `.24` physically out. |
| hestia (`10.42.2.10`) | TrueNAS storage + compute | No GPUs since 2026-05-16. Runs the GHA deploy runner, Immich photo-backup rsync, qBittorrent, thermalscope telemetry, IPMI exporter. See [hosts/hestia/](../hosts/hestia/README.md). |
| Synology / alcatraz (`10.42.2.11`) | Block + photo storage | iSCSI backing for CNPG PVCs; phone-photo upload target. Role narrowing — see the photos-SOT plan. GitOps deploy runner + `immich-photos-pull` compose workload built (operator bootstrap pending — see the alcatraz GitOps plan). |

## In flight

Active plans (see [docs/plans/](plans/README.md) for the full status-grouped index):

- [Alcatraz → hestia migration](plans/2026-05-20-alcatraz-to-hestia-migration.md) — non-photo data off alcatraz onto hestia ZFS; Phase 1 mostly done, Phase 2 backup pipeline live.
- [Hestia as photos source-of-truth](plans/2026-06-01-hestia-photos-sot.md) — Immich NFS PV repointed to hestia; soak/verification underway.
- [Alcatraz GitOps Docker workflow](plans/2026-06-26-alcatraz-gitops-docker.md) — self-hosted runner + `docker compose` deploy mirroring hestia; runner/workflow/guard + first workload (`immich-photos-pull` as a compose service) built, operator bootstrap (P1–P7) + arch confirmation pending.
- [Alcatraz pulls photos from hestia](plans/2026-07-04-alcatraz-photos-pull.md) — impossible hestia→alcatraz rsync push-back (Synology setuid-root inbound-uid check) retired; backfill now runs from alcatraz as a DSM Task Scheduler pull job. Repo artifacts landed; DSM operator steps + DSM-Photos-indexing validation pending.
- [Snapcast / HifiBerry rollout](plans/2026-05-03-snapcast-hifiberry-rollout.md) — server + LB IP live; per-device client setup remaining.
- [Navidrome → Mopidy → Snapcast audio source](plans/2026-03-14-navidrome-snapcast-mopidy.md) — Mopidy sidecar in draft PR #426; not yet on master.
- [Hestia memory benchmark](plans/2026-05-15-hestia-memory-benchmark.md) — 6-DIMM baseline captured; 8-DIMM comparison pending a physical DIMM swap.
- [homelabscope — scheduled-job monitoring](plans/2026-07-04-homelabscope.md) — unified `homelabscope_job_*` metric family + hestia node-exporter textfile scraper (fixes the orphaned/unscraped immich-backup metric) + cronjob recording rules + templated staleness/absence alerts + Grafana table. Repo artifacts landed; operator steps remain (deploy node-exporter, build+enable the heartbeat Custom App, bump the immich image digest).

## Next up

Planned, not yet started:

- [Network resilience + BGP completion](plans/2026-05-06-network-resilience-and-bgp-completion.md) — the consolidated plan that supersedes the earlier BGP rollout.
- [democratic-csi least-privilege key](plans/2026-05-09-democratic-csi-least-privilege-key.md).
- [Immich pgvecto.rs → VectorChord](plans/2026-06-02-immich-vectorchord-migration.md) — **upgrade staged** in [operations/2026-07-02-immich-v3-upgrade.md](operations/2026-07-02-immich-v3-upgrade.md) (two PRs, not applied). Immich v3.0.0 removed pgvecto.rs, so the v2.7.5→v3.0.1 bump is gated on this DB migration. Rehearsed on `immich-stage` 2026-07-03 → split into **PR-B** (DB → dual-extension migration image `corentingiraud/cnpg-pgvector-vectorchord:16-migration`, ready) and **PR-C** (app → v3.0.1 + DB cleanup, draft; blocked on PR-B + manual superuser `CREATE EXTENSION vchord CASCADE` + v2.7.5 data-migration + verify). Dual-extension image resolves the plan's Constraint A / Open Question 1 → in-place swap (Option 3B). Operator-gated, one-way DB migration.

## Recently completed (last ~60 days)

- [Monitoring enhancement](plans/2026-05-09-monitoring-enhancement.md) — **complete.** Phase 1 ServiceMonitor coverage (#935); Phase 2 (Signal routing) obsoleted by the signal-cli/hermes decommission; Phase 3 Flux reconciliation alerts (#937 PodMonitor + #940). GA Flux dropped `gotk_reconcile_condition`, so readiness alerts are sourced from kube-state-metrics `gotk_resource_info`. ⚠️ The 3 critical Flux alerts evaluate correctly but route to the `null` receiver — no delivery channel until a replacement for Signal is chosen.
- **iSCSI PVC expansion fixed** (2026-07-03, [#1019](https://github.com/gjcourt/homelab/pull/1019)) — democratic-csi **v1.9.0 → v1.9.5** removed the `simple-file-writer` SCST-reload helper (missing on TrueNAS SCALE, upstream #390) that had broken every PVC expand. Online growth now works end-to-end (verified: jellyfin-cache grew 5→20 Gi live, no recreate); the previously-feared node-side `resize2fs` EPERM was a v1.9.0 artifact. Keep `truenas_admin` passwordless sudo ON for the reload. Write-up: [incidents/2026-07-02-jellyfin-cache-pvc-expand-simple-file-writer.md](operations/incidents/2026-07-02-jellyfin-cache-pvc-expand-simple-file-writer.md).
- **Vibrato (ito + leva! espresso controller) shipped** — a clean-room monitor/control SPA for the espresso machine, deployed to `vibrato-prod` / `vibrato-stage`. Staging runs `LEVA_TRANSPORT=sim` so it can't starve prod's single ito telemetry stream. Features: live amber-OLED display mirror + keyboard/touch nav, a direct-command Control surface (`MCc` commands with a server-side deny-list), profile planner, shot history, and an MQTT→Home-Assistant bridge. Runbook: [operations/apps/vibrato.md](operations/apps/vibrato.md). Open gap: live numeric telemetry (pressure/flow/temp/weight) is undecoded — the rich-frame offsets need an on-machine sensored capture, and `LEVA_HOST` still points at the sensorless bench ito.
- burntbytes.com self-hosted (off GitHub Pages → cluster via Cloudflare tunnel) · Guest VLAN DNS + HifiBerry access · AdGuard HA + DNS rollout · Authelia SMTP notifier · hestia GHA auto-deploy runner · critique remediation phases 2–5.

## Known issues / blocked

- **`*.burntbytes.com` wildcard expired 2026-08-05 — resolved same day, but it was silently broken for 30 days.** cert-manager's DNS-01 propagation self-check queried cluster DNS (CoreDNS → AdGuard), which serves a split-horizon view and returns `ANSWER: 0` for `_acme-challenge.burntbytes.com`, so the ACME Order sat `pending` from 2026-07-06 until the old cert lapsed. Cloudflare had published the records correctly the whole time. Fixed with `--dns01-recursive-nameservers` ([#1274](https://github.com/gjcourt/homelab/pull/1274)); cert-manager metrics are now scraped and alerted on ([#1275](https://github.com/gjcourt/homelab/pull/1275)) — there was previously **no ServiceMonitor**, so no expiry alert was possible at all. The Certificate reported `Ready=True` throughout, which was true and useless. Full postmortem: [incidents/2026-08-05-wildcard-cert-expiry-dns01-split-horizon.md](operations/incidents/2026-08-05-wildcard-cert-expiry-dns01-split-horizon.md). **Open follow-up:** audit which other Helm charts default `serviceMonitor.enabled: false` and are therefore unmonitored.
- **No on-prem LLM inference.** The 2× RTX 4090 were sold 2026-05-16. The `llms/` and GPU-`monitoring/` Custom Apps on hestia are archived. Restoring inference needs new GPU hardware or a hosted-API backend.
- **`hermes` / `hermes-callee` (Signal bots) and `signal-cli` decommissioned 2026-06-17.** Removed from `apps/{base,production,staging}/` and garbage-collected by Flux (`prune: true`). They had no model backend after the GPUs were sold and were already scaled to 0. Signal-based critical-alert routing is dead as a result — see the obsoleted Phase 2 in [plans/2026-05-09-monitoring-enhancement.md](plans/2026-05-09-monitoring-enhancement.md).
- **`.22` (talos-v2l-hng) decommissioned 2026-06-19** — bad DIMM (reads 30.6 GiB vs ~62), did not return after the 2026-06-18 maintenance. Removed from etcd and the cluster; **`.23` was promoted to control-plane in its place**, restoring fault-tolerant 3-member etcd (now a 4-node cluster). See the [control-plane promotion runbook](operations/2026-06-19-talos-controlplane-promotion.md) and [plan/execution record](plans/2026-06-19-promote-talos-25-to-controlplane.md). `.22` hardware verdict (DIMM/board/RMA) and `.24` re-entry still pending. **Topology (verified 2026-06-19):** all 4 nodes share one **Rack Switch (USWED42)** and one **USP PDU Pro** (separate outlets) — so the switch + PDU are **accepted single points of failure** (a 3-member etcd survives a node/outlet loss, not a whole-switch/PDU loss). Highest-value mitigation: put the Rack Switch + PDU on a UPS.
- **`.20` (talos-ykb-uir) hung 2026-06-21 21:16 UTC → cluster-wide DNS outage via a Cilium `k8sServiceHost` SPOF** (diagnosed/mitigated 2026-06-24). The node pings but `apid`/`kubelet` are stuck (no TLS handshake), so `talosctl` can't reach it — recovery needs a **physical power-cycle** (no BMC; mind the `.20/.21` shared switch/PDU). Cilium had `k8sServiceHost: 10.42.2.20` pinned, so losing that node killed cilium-operator → CoreDNS → every DNS-dependent service. **Mitigated live:** Cilium HelmRelease **suspended** and operator/agent `KUBERNETES_SERVICE_HOST` repointed to `.21`. Permanent fix (Talos KubePrism, `localhost:7445`) is in an open PR — **do not resume the suspended Cilium HelmRelease until it merges**, or Flux will re-apply the SPOF. Full postmortem: [incidents/2026-06-24-cp-node-hang-cilium-k8sservicehost-spof.md](operations/incidents/2026-06-24-cp-node-hang-cilium-k8sservicehost-spof.md).
- **CNPG WAL archiving** broken on several staging clusters (stale system-ID in S3) — base backups succeed, but no PITR until fixed.
- **LB pool / Lab-VLAN `/24` sharing** is the root cause of the 2026-05-05 wired-device incident; the dedicated LB subnet migration is tracked in the network-resilience plan (Phase D).
- **Alertmanager state was non-durable — fixed 2026-08-05 ([#1278](https://github.com/gjcourt/homelab/pull/1278)).** `alertmanagerSpec.storage` was empty, so the pod ran on `emptyDir`: every restart (node drain, chart upgrade, OOM) dropped `/alertmanager`, taking active **silences** (one set during maintenance evaporated mid-window and the alerts it muted re-fired) and the **notification log** (dedup/inhibition memory, so already-notified alerts re-notified) with it. Now a 1Gi `truenas-iscsi` PVC via `storage.volumeClaimTemplate`, with `persistentVolumeClaimRetentionPolicy: Retain`. This buys silence + dedup durability **and nothing else** — see the next bullet.
- **No fired-alert history anywhere — by design, and persistence does not fix it.** Alertmanager keeps **no record of alerts that fired and resolved**, regardless of storage: its API only exposes currently-active alerts, and the on-disk `nflog` is a dedup ledger pruned at `retention: 120h`, not a queryable audit log. "What fired last week" has to come from Prometheus `ALERTS` / `ALERTS_FOR_STATE` rendered in Grafana. Plan (options, recommendation, effort): [plans/2026-08-05-alertmanager-alert-history.md](plans/2026-08-05-alertmanager-alert-history.md) — recommendation is a Grafana state-timeline dashboard over `ALERTS`, capped at the 10d Prometheus retention. Two *routing* facts compound the blindness (`infra/controllers/kube-prometheus-stack/values.yaml`): the **default route receiver is `null`**, so any alert lacking a `severity` label — or otherwise unmatched — is silently dropped (the #1080-class gap); and warnings deliver to `gjcourt+alerts@` behind a Gmail skip-inbox filter (opt-in label), so even a routed warning can go unseen. (Three alerts are *intentionally* muted to `null`: Watchdog, `overture-prod` KubePdb, `*-stage` warnings.) Not a bug: the Status page's `Cluster Status: disabled` is expected for a single-replica AM (`alertmanagerSpec.replicas: 1`, no gossip cluster).
