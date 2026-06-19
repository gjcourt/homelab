# Status

> **What this is.** A single-glance snapshot of the homelab: what's running,
> what's in flight, what's next, and what's broken. Detail lives elsewhere —
> this page links out. **Update this file in the same PR whenever** a plan's
> status changes, an incident postmortem lands, or hardware/topology changes.
>
> Last updated: 2026-06-19

## Cluster at a glance

| | |
|---|---|
| Cluster | `melodic-muse` |
| Nodes | 4 — 3 control-plane (`.20`/`.21`/`.23`) + 1 worker (`.25`); `.22`/`.24` physically out (see Known issues) |
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
| Synology / alcatraz (`10.42.2.11`) | Block + photo storage | iSCSI backing for CNPG PVCs; phone-photo upload target. Role narrowing — see the photos-SOT plan. |

## In flight

Active plans (see [docs/plans/](plans/README.md) for the full status-grouped index):

- [Alcatraz → hestia migration](plans/2026-05-20-alcatraz-to-hestia-migration.md) — non-photo data off alcatraz onto hestia ZFS; Phase 1 mostly done, Phase 2 backup pipeline live.
- [Hestia as photos source-of-truth](plans/2026-06-01-hestia-photos-sot.md) — Immich NFS PV repointed to hestia; soak/verification underway.
- [Snapcast / HifiBerry rollout](plans/2026-05-03-snapcast-hifiberry-rollout.md) — server + LB IP live; per-device client setup remaining.
- [Navidrome → Mopidy → Snapcast audio source](plans/2026-03-14-navidrome-snapcast-mopidy.md) — Mopidy sidecar in draft PR #426; not yet on master.
- [Hestia memory benchmark](plans/2026-05-15-hestia-memory-benchmark.md) — 6-DIMM baseline captured; 8-DIMM comparison pending a physical DIMM swap.

## Next up

Planned, not yet started:

- [Network resilience + BGP completion](plans/2026-05-06-network-resilience-and-bgp-completion.md) — the consolidated plan that supersedes the earlier BGP rollout.
- [democratic-csi least-privilege key](plans/2026-05-09-democratic-csi-least-privilege-key.md).
- [Immich pgvecto.rs → VectorChord](plans/2026-06-02-immich-vectorchord-migration.md).

## Recently completed (last ~60 days)

- [Monitoring enhancement](plans/2026-05-09-monitoring-enhancement.md) — **complete.** Phase 1 ServiceMonitor coverage (#935); Phase 2 (Signal routing) obsoleted by the signal-cli/hermes decommission; Phase 3 Flux reconciliation alerts (#937 PodMonitor + #940). GA Flux dropped `gotk_reconcile_condition`, so readiness alerts are sourced from kube-state-metrics `gotk_resource_info`. ⚠️ The 3 critical Flux alerts evaluate correctly but route to the `null` receiver — no delivery channel until a replacement for Signal is chosen.
- burntbytes.com self-hosted (off GitHub Pages → cluster via Cloudflare tunnel) · Guest VLAN DNS + HifiBerry access · AdGuard HA + DNS rollout · Authelia SMTP notifier · hestia GHA auto-deploy runner · critique remediation phases 2–5.

## Known issues / blocked

- **No on-prem LLM inference.** The 2× RTX 4090 were sold 2026-05-16. The `llms/` and GPU-`monitoring/` Custom Apps on hestia are archived. Restoring inference needs new GPU hardware or a hosted-API backend.
- **`hermes` / `hermes-callee` (Signal bots) and `signal-cli` decommissioned 2026-06-17.** Removed from `apps/{base,production,staging}/` and garbage-collected by Flux (`prune: true`). They had no model backend after the GPUs were sold and were already scaled to 0. Signal-based critical-alert routing is dead as a result — see the obsoleted Phase 2 in [plans/2026-05-09-monitoring-enhancement.md](plans/2026-05-09-monitoring-enhancement.md).
- **`.22` (talos-v2l-hng) decommissioned 2026-06-19** — bad DIMM (reads 30.6 GiB vs ~62), did not return after the 2026-06-18 maintenance. Removed from etcd and the cluster; **`.23` was promoted to control-plane in its place**, restoring fault-tolerant 3-member etcd (now a 4-node cluster). See the [control-plane promotion runbook](operations/2026-06-19-talos-controlplane-promotion.md) and [plan/execution record](plans/2026-06-19-promote-talos-25-to-controlplane.md). `.22` hardware verdict (DIMM/board/RMA) and `.24` re-entry still pending. **Topology (verified 2026-06-19):** all 4 nodes share one **Rack Switch (USWED42)** and one **USP PDU Pro** (separate outlets) — so the switch + PDU are **accepted single points of failure** (a 3-member etcd survives a node/outlet loss, not a whole-switch/PDU loss). Highest-value mitigation: put the Rack Switch + PDU on a UPS.
- **CNPG WAL archiving** broken on several staging clusters (stale system-ID in S3) — base backups succeed, but no PITR until fixed.
- **LB pool / Lab-VLAN `/24` sharing** is the root cause of the 2026-05-05 wired-device incident; the dedicated LB subnet migration is tracked in the network-resilience plan (Phase D).
