---
status: in-progress
last_modified: 2026-05-03
---

# AdGuard Home — high availability

Make the homelab DNS resolver tolerant to a single-node loss without breaking client lookups.

## Current state (2026-05-03)

The HA primitives are deployed in production. Two AdGuard pods are scheduled on different nodes, a CronJob keeps their config in sync, and a second LoadBalancer DNS IP is allocated for client failover.

| Piece | State | Notes |
|-------|-------|-------|
| `apps/base/adguard/` StatefulSet, replicas | base=1, prod-overlay patches to 2 ✓ | per-pod PVCs (`config-adguard-0/1`, `work-adguard-0/1`) |
| Pod spread | `podAntiAffinity (preferred)` + `topologySpreadConstraints (ScheduleAnyway)` ✓ | both running on distinct workers (`talos-2mz-rfj`, `talos-kot-7x7`) |
| `adguard-sync` CronJob | enabled in base, schedule `0 */6 * * *` ✓ | last run 2026-05-03 06:00 UTC, status `Sync done` |
| Sync credentials | `adguard-sync-credentials` Secret in prod overlay ✓ | username `george`, not `admin` (gotcha caught earlier) |
| Primary DNS LB IP | `10.42.2.43` ✓ | `service/adguard` |
| Secondary DNS LB IP | `10.42.2.45` ✓ | `service/adguard-dns-secondary` (added 2026-05-02) |
| UI is one-writer | `service/adguard-admin` (ClusterIP, pinned to `adguard-0`) ✓ | sync job reads from this; replicas never receive direct writes |

Functionally, DNS will survive losing either AdGuard pod or the node it's on as long as both LB IPs are wired into UniFi DHCP scope options.

## Remaining work

| # | Item | Priority | Notes |
|---|------|----------|-------|
| 1 | **Verify UniFi hands out both DNS servers** | high | Operator-side router config. UniFi DHCP scope option 6 should list `10.42.2.43, 10.42.2.45`. Check at UniFi → Networks → LAN → DHCP → Network options. |
| 2 | **Failover validation runbook** | high | Document a repeatable drill: drain `talos-X`, confirm DNS still resolves through `dig @10.42.2.45 example.com`, restore. See [`docs/operations/2026-05-03-adguard-failover-validation.md`](../operations/2026-05-03-adguard-failover-validation.md) (added in the same PR as this refresh). |
| 3 | **Resize `work-adguard-1` from 1Gi → 5Gi** | medium | Mismatch with `work-adguard-0` (5Gi). Query log history on replica is capped early. StatefulSet `volumeClaimTemplate` is immutable, so this is a manual `kubectl patch pvc` + `kubectl rollout restart` cycle (Synology iSCSI supports volume expansion). |
| 4 | **NetworkPolicy hardening** | medium | Restrict ingress to `adguard-admin` to the sync job + Gateway pods only. Restrict ingress to `adguard-headless` (replica admin port 80) to the sync job only. Add `network-policies: enforced` label on `adguard-prod` so the cluster-wide default-deny applies. |
| 5 | **Cilium BGP advertisement of LB IPs** | low | Tracked in [`2026-03-08-bgp-rollout.md`](2026-03-08-bgp-rollout.md). Until BGP is in place, both DNS IPs are advertised via L2 announcements which is fine for the LAN but limits failover to nodes in the same broadcast domain. |
| 6 | **Consider strict naming consistency** | defer | Earlier draft said "rename `adguard-prod` → `adguard` later if you want strict consistency." That suggestion contradicts the cluster's actual `<app>-prod` convention (see `AGENTS.md`). Keep `adguard-prod`. |

## Why "one-writer UI" matters

AdGuard Home has no built-in multi-master config reconciliation. The pattern is:

- Only one UI endpoint (the primary, `adguard-0` via `adguard-admin` Service) is reachable for humans.
- The `adguard-sync` CronJob (`ghcr.io/bakito/adguardhome-sync`) copies config from primary to replicas every 6 hours.
- DHCP sync is intentionally disabled (`FEATURES_DHCP_*=false`) — UniFi handles DHCP.

Editing config on a replica directly will be silently overwritten on the next sync cycle. Always edit through `adguard-admin`.

## Validation

When you resume after a change, the smoke check:

```bash
# Both pods Ready?
kubectl -n adguard-prod get statefulset,pods -o wide

# Both LBs have endpoints?
kubectl -n adguard-prod get svc,endpoints | grep -E 'adguard\b|dns-secondary'

# Sync ran cleanly recently?
kubectl -n adguard-prod logs -l job-name=$(kubectl -n adguard-prod get jobs -o name | tail -1 | cut -d/ -f2) --tail=20

# DNS resolves through both LBs?
dig @10.42.2.43 +short example.com
dig @10.42.2.45 +short example.com
```

Full failover drill is in the operations runbook (see Remaining Work item 2).

---

## Survey 2026-05-03

**Current state:** HA primitives are deployed in production — 2 StatefulSet replicas on distinct workers, the config-sync CronJob ran at 06:00 UTC on 2026-05-03, and both DNS LoadBalancers (`10.42.2.43` primary + `10.42.2.45` secondary) are operational. The failover validation runbook lives at [`docs/operations/2026-05-03-adguard-failover-validation.md`](../operations/2026-05-03-adguard-failover-validation.md). The remaining checklist items in the plan are operator-side actions, not IaC.

**Outstanding next steps (operator):**

1. Confirm UniFi DHCP scope option 6 advertises both `10.42.2.43` and `10.42.2.45` to LAN clients.
2. Run the failover validation drill end-to-end (drain a node, watch the secondary LB take traffic, restore).
3. Resize the `work-adguard-1` PVC from 1Gi → 5Gi (PVC patch + rollout restart) — current sizing is pinch-point under filter-list growth.
4. Decide whether the deferred NetworkPolicy hardening (PRs #412 / #413) lands as v1 follow-up or punts to v2.
