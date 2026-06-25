# Plans

This directory contains planning documents for features, migrations, and operational improvements in the homelab.

## File Naming

Plan filenames follow the format:

```
YYYY-MM-DD-<slug>.md
```

The date prefix is the **filing date** — when the plan was first written, not the latest edit. It does not change when the plan is updated; that's what `last_modified` in the front-matter is for. The prefix exists so the filesystem listing and the index below sort chronologically without ambiguity.

The slug is kebab-case and describes the work in 2–6 words.

## Front-Matter Convention

Every plan document **must** include YAML front-matter at the top of the file with the following fields:

```yaml
---
status: <value>
last_modified: YYYY-MM-DD
summary: "One line (<120 chars) describing the work — feeds the generated index below"
---
```

### `status` values

| Value | Description |
| :--- | :--- |
| `planned` | Work has not yet started. The plan exists for future reference. |
| `in-progress` | Actively being worked on; some steps may be complete. |
| `complete` | All steps are done and the feature/change is live in production. |
| `superseded` | This plan was replaced or made obsolete by a different approach. |
| `abandoned` | Decided not to pursue; kept for historical reference. |

### `last_modified`

Use `YYYY-MM-DD` format. Update this field whenever the document is meaningfully changed.

### Optional fields

| Field | Use |
| :--- | :--- |
| `blocked_on: "<reason>"` | An `in-progress` plan that cannot advance — say what unblocks it. |
| `superseded_by: docs/plans/<file>` | Required on `superseded` plans; points at the replacement. |

When a plan's status changes, update `docs/STATUS.md` in the same PR.
(The dashboard lands with Phase B of
[2026-06-10-docs-reorg-status-dashboard.md](2026-06-10-docs-reorg-status-dashboard.md);
until then this step is a no-op.)

## Document Index

Grouped by status, newest filing date first. **Generated — do not edit by
hand.** Edit plan frontmatter and run `make plans-index` (CI fails on drift;
see `scripts/plans-index/`).

<!-- BEGIN PLANS INDEX -->

### In progress (9)

| File | Last modified | Summary |
| :--- | :--- | :--- |
| [2026-06-17-alertmanager-smtp-alerting.md](2026-06-17-alertmanager-smtp-alerting.md) | 2026-06-17 | Route critical Alertmanager alerts to email via Gmail SMTP (replaces dead Signal channel) |
| [2026-06-16-thermalscope-power-and-headroom.md](2026-06-16-thermalscope-power-and-headroom.md) | 2026-06-17 | thermalscope: add power/energy/cost (RAPL), thermal headroom, and throttle/degradation signals — phases 1–3 live; phase 4 pending |
| [2026-06-01-hestia-photos-sot.md](2026-06-01-hestia-photos-sot.md) | 2026-06-10 | Make hestia the source of truth for family/ + homes/; repoint Immich NFS PV; alcatraz narrows to upload target |
| [2026-05-20-alcatraz-to-hestia-migration.md](2026-05-20-alcatraz-to-hestia-migration.md) | 2026-05-21 | Migrate non-photo data (~870 GiB iSCSI + 3 TiB NFS media) off alcatraz onto hestia ZFS |
| [2026-05-15-hestia-memory-benchmark.md](2026-05-15-hestia-memory-benchmark.md) | 2026-05-15 | STREAM + Intel MLC bandwidth benchmark: 6-DIMM baseline vs 8-DIMM comparison |
| [2026-05-03-snapcast-hifiberry-rollout.md](2026-05-03-snapcast-hifiberry-rollout.md) | 2026-06-10 | Wire kitchen + living-room HifiBerries as snapclients of the in-cluster snapserver |
| [2026-05-02-hermes-bot-k8s.md](2026-05-02-hermes-bot-k8s.md) | 2026-06-10 | Hermes agent (Signal mode) on melodic-muse so the bot is laptop-independent — **blocked:** LLM backend gone (RTX 4090s sold 2026-05-16); deployment scaled to 0 |
| [2026-05-02-critique-remediation.md](2026-05-02-critique-remediation.md) | 2026-05-04 | IaC hardening — close the 22 findings from the 2026-05-02 critique |
| [2026-03-14-navidrome-snapcast-mopidy.md](2026-03-14-navidrome-snapcast-mopidy.md) | 2026-06-10 | Navidrome → Mopidy → Snapcast → HifiBerry audio pipeline — draft PR #426 open, not yet on master |

### Planned (7)

| File | Last modified | Summary |
| :--- | :--- | :--- |
| [2026-06-21-bluetooth-presence-system.md](2026-06-21-bluetooth-presence-system.md) | 2026-06-21 | BLE beacon presence/occupancy system (ESPresense → HA → Grafana) for who/how-many is home, per-room |
| [2026-06-16-burntbytes-mailserver.md](2026-06-16-burntbytes-mailserver.md) | 2026-06-16 | Self-hosted mail for burntbytes.com (<10 accounts): Mailu on the cluster + VPS SMTP gateway + SES smarthost |
| [2026-06-02-immich-vectorchord-migration.md](2026-06-02-immich-vectorchord-migration.md) | 2026-06-10 | Migrate Immich CNPG from pgvecto.rs to VectorChord |
| [2026-05-09-democratic-csi-least-privilege-key.md](2026-05-09-democratic-csi-least-privilege-key.md) | 2026-05-09 | Migrate democratic-csi to a least-privilege TrueNAS API key |
| [2026-05-06-network-resilience-and-bgp-completion.md](2026-05-06-network-resilience-and-bgp-completion.md) | 2026-05-06 | Unified network resilience + BGP completion plan, phases A-F with GO gates |
| [2026-03-08-drawer-inserts.md](2026-03-08-drawer-inserts.md) | 2026-05-03 | Cardboard drawer insert design (75×32×12 cm) — physical project, no repo artifacts |
| [2026-02-21-linkding-db-restore-plan.md](2026-02-21-linkding-db-restore-plan.md) | 2026-05-03 | Live DR drill: destroy and restore Linkding staging DB (never executed) |

### Complete (19)

| File | Last modified | Summary |
| :--- | :--- | :--- |
| [2026-06-24-control-plane-vip-stable-endpoint.md](2026-06-24-control-plane-vip-stable-endpoint.md) | 2026-06-24 | EXECUTED 2026-06-24 — Talos layer-2 control-plane VIP 10.42.2.26 live on all 3 CP nodes (etcd-elected); apiserver cert regenerated to include all node IPs + the VIP; kubeconfig and talosconfig cut over to the VIP. Nodes were DHCP (not static as drafted); applied per-node in try-mode with no reboots, etcd 3/3 throughout. |
| [2026-06-19-promote-talos-25-to-controlplane.md](2026-06-19-promote-talos-25-to-controlplane.md) | 2026-06-19 | EXECUTED 2026-06-19 — promoted .23 (not .25) to control-plane and removed dead .22, restoring 3-member etcd; cluster now 4 nodes (3 CP + 1 worker) |
| [2026-06-18-finance-dashboard-multipage.md](2026-06-18-finance-dashboard-multipage.md) | 2026-06-18 | finance.burntbytes.com expanded from one balance-sheet page to a 4-page static site (balance sheet, cash flow, STR model, retirement runway) with encrypted-YAML data + interactive client-side charts |
| [2026-06-10-docs-reorg-status-dashboard.md](2026-06-10-docs-reorg-status-dashboard.md) | 2026-06-10 | Docs status legibility: plan frontmatter cleanup, generated index, STATUS.md dashboard, HOMELAB.md migration |
| [2026-06-10-burntbytes-self-host.md](2026-06-10-burntbytes-self-host.md) | 2026-06-10 | burntbytes.com self-hosted on the cluster via Cloudflare tunnel; apex cutover live, GitHub Pages retired |
| [2026-05-09-monitoring-enhancement.md](2026-05-09-monitoring-enhancement.md) | 2026-06-17 | ServiceMonitor coverage audit, critical-alert Signal routing, Flux reconciliation alerts |
| [2026-05-07-vllm-frontier-model-experiments.md](2026-05-07-vllm-frontier-model-experiments.md) | 2026-05-09 | Stability-first vLLM experiments on 2× 4090 — winner Qwen3.6-35B-A3B-AWQ TP=2 |
| [2026-05-07-guest-vlan-dns-and-hifiberry-access.md](2026-05-07-guest-vlan-dns-and-hifiberry-access.md) | 2026-06-10 | Guest VLAN DNS + HifiBerry speaker access (firewall rules + mDNS reflector) |
| [2026-05-04-phase2-5-completion.md](2026-05-04-phase2-5-completion.md) | 2026-06-10 | Close critique phases 2-5: probe coverage and liveness/readiness gaps (PRs A-D) |
| [2026-05-02-hestia-gha-runner.md](2026-05-02-hestia-gha-runner.md) | 2026-06-10 | Self-hosted GHA runner on hestia for auto-deploy of Custom App compose changes |
| [2026-03-08-adguard-dns-rollout.md](2026-03-08-adguard-dns-rollout.md) | 2026-06-10 | Roll AdGuard Home out as the primary LAN DNS resolver |
| [2026-02-28-network-migration-192-to-10-42-2.md](2026-02-28-network-migration-192-to-10-42-2.md) | 2026-03-06 | Migrate the LAN from 192.168.5.0/24 to 10.42.2.0/24 |
| [2026-02-21-documentation-rewrite-plan.md](2026-02-21-documentation-rewrite-plan.md) | 2026-05-02 | Rewrite all app and infra documentation |
| [2026-02-21-cnpg-backup-upgrade.md](2026-02-21-cnpg-backup-upgrade.md) | 2026-02-27 | Migrate CNPG backups to Barman Cloud Plugin |
| [2026-02-21-cluster-health-dashboards-plan.md](2026-02-21-cluster-health-dashboards-plan.md) | 2026-05-03 | Grafana cluster health dashboard suite |
| [2026-02-21-app-health-dashboards-plan.md](2026-02-21-app-health-dashboards-plan.md) | 2026-05-03 | Grafana application health dashboards |
| [2026-02-17-authelia-smtp-notifier.md](2026-02-17-authelia-smtp-notifier.md) | 2026-06-10 | Replace Authelia filesystem notifier with real SMTP (Gmail app password) |
| [2026-02-15-adguard-ha.md](2026-02-15-adguard-ha.md) | 2026-06-10 | AdGuard Home high-availability: 2 replicas on distinct workers + config sync |
| [2026-02-11-authelia-sso-rollout.md](2026-02-11-authelia-sso-rollout.md) | 2026-05-03 | SSO rollout across all homelab apps |

### Superseded / abandoned (6)

| File | Last modified | Summary |
| :--- | :--- | :--- |
| [2026-05-09-vllm-vision-sidecar.md](2026-05-09-vllm-vision-sidecar.md) | 2026-05-09 | vLLM vision sidecar — reverted on quality (47% vs 70% target on real eBay photos) |
| [2026-05-07-hestia-p2p-enablement.md](2026-05-07-hestia-p2p-enablement.md) | 2026-05-07 | Enable GPU P2P on hestia — blocked by 3-slot 4090 chassis constraints |
| [2026-05-05-bgp-phase4-revision.md](2026-05-05-bgp-phase4-revision.md) | 2026-05-06 | Revised BGP phase 4 (safe L2 removal gate) after wired-client ARP regression — superseded by [2026-05-06-network-resilience-and-bgp-completion.md](2026-05-06-network-resilience-and-bgp-completion.md) |
| [2026-05-04-llama-cpp-benchmarking.md](2026-05-04-llama-cpp-benchmarking.md) | 2026-06-10 | Systematic llama.cpp benchmarking on hestia 4090s — moot after GPU sale |
| [2026-05-02-signal-cli-hermes-rollout.md](2026-05-02-signal-cli-hermes-rollout.md) | 2026-05-03 | Signal-cli + signal-bridge as TrueNAS Custom App; went k8s-native instead — superseded by [2026-05-02-hermes-bot-k8s.md](2026-05-02-hermes-bot-k8s.md) |
| [2026-03-08-bgp-rollout.md](2026-03-08-bgp-rollout.md) | 2026-06-10 | L2 → BGP LoadBalancer advertisement with the UCGF; phases 1-3 live, phase 4 reverted — superseded by [2026-05-06-network-resilience-and-bgp-completion.md](2026-05-06-network-resilience-and-bgp-completion.md) |

<!-- END PLANS INDEX -->
