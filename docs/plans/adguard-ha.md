---
status: planned
last_modified: 2026-02-27
---

# TODO: AdGuard Home HA (DNS + UI-driven config)

This repo is structured so staging uses a `-stage` suffix and production has no suffix (desired end-state). Today, AdGuard overlays are:

- Staging: `adguard-stage`
- Production: `adguard-prod` (consider renaming to `adguard` later if you want strict consistency)

## Current state (April 2026)

- AdGuard is a StatefulSet with per-pod PVCs:
  - `config` PVC stores `AdGuardHome.yaml`
  - `work` PVC stores query logs/statistics
- UI traffic is pinned to the primary pod (`adguard-0`) via `adguard-admin`
- DNS is exposed via a single `LoadBalancer` service (`10.42.2.43` in prod)
- `adguard-sync` CronJob is enabled in both prod and staging and is intended to sync config from `adguard-0` to replicas

## Important prerequisite discovered during HA rollout

Before scaling replicas, `adguard-sync` credentials must match the live AdGuard admin user. The live username is `george` (not `admin`). If sync fails with `401 Unauthorized`, replicas will not receive the primary config.

Production also needed a larger `work` volume because query logs filled the original 1Gi PVC.

## Why “one-writer UI” matters

AdGuard Home has no built-in multi-master config reconciliation.
The safe pattern is:

- Only one UI endpoint (primary) is reachable for humans
- A sync job copies config from primary to replicas

## Next steps when you have 6 nodes + more IPs (UniFi)

### 1) Scale AdGuard safely (single DNS IP)

- Edit replicas in your overlay (staging first):
  - apps/staging/adguard/… (StatefulSet `spec.replicas`)
- Set `replicas: 2` (or `3`) and ensure pods land on different nodes:
  - Add `topologySpreadConstraints` or pod anti-affinity in the StatefulSet
  - Goal: `adguard-0`, `adguard-1`, … run on different nodes
- Confirm DNS continues working during a node reboot:
  - `kubectl -n adguard-stage get endpoints adguard` should show multiple endpoints

### 2) Enable configuration sync (CronJob)

A disabled-by-default CronJob manifest exists here:

- apps/base/adguard/cronjob-sync.yaml

To enable it later:

- Add the CronJob file to the overlay `resources:` (staging first)
- Create a secret named `adguard-sync-credentials` in the AdGuard namespace with keys:
  - `ORIGIN_USERNAME`, `ORIGIN_PASSWORD`
  - `REPLICA1_USERNAME`, `REPLICA1_PASSWORD`

Notes:
- The CronJob uses `ORIGIN_URL=http://adguard-admin:8080` so it always reads from the primary.
- DHCP sync is disabled by default (`FEATURES_DHCP_* = false`).

### 3) Move from single DNS IP → two DNS IPs (best client failover)

When you can allocate more LB IPs:

- Expand your Cilium LB IP pool (or add a second pool) so you have at least two addresses
- Deploy a second AdGuard service/IP (or a second AdGuard instance if you want strict isolation)
- Configure UniFi DHCP to hand out BOTH DNS servers (primary + secondary)

### 4) Hardening checklist

- Confirm health checks:
  - readiness probe OK (the Service should stop sending traffic to unhealthy pods)
- Add resource requests/limits if needed
- Consider NetworkPolicies:
  - Only allow sync job to reach replica admin endpoints
  - Only allow the UI route to reach `adguard-admin`

## Quick validation commands (when you resume)

- `kubectl -n adguard-stage get statefulset,svc,pdb`
- `kubectl -n adguard-stage get pods -o wide`
- `kubectl -n adguard-stage get endpoints adguard`
- `kubectl -n adguard-stage describe httproute adguard-https`
