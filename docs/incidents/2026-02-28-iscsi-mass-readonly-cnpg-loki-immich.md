# Incident: Mass iSCSI Read-Only + CNPG Replica Failures + Loki WAL + Immich ML Probe Loop

**Date:** 2026-02-28  
**Severity:** High — multiple production and staging databases unavailable; observability pipeline degraded  
**Environments affected:** Production + Staging  
**Duration:** ~8–12 days (silent accumulation from ~2026-02-16 onwards)  
**Resolved:** 2026-02-28

---

## Summary

A series of Synology iSCSI volume instability events caused widespread, silent failures across the cluster. The failures accumulated over ~12 days without generating page-worthy alerts, manifesting as pod CrashLoops, read-only file systems, and degraded CloudNative-PG (CNPG) database clusters. A single root cause — iSCSI block device going read-only — cascaded across multiple namespaces and services.

Concurrently, the `immich-machine-learning` deployment was in a continuous `OOMKilled`/liveness-probe-killed loop due to probe thresholds that were too aggressive for ROCm/ONNX GPU model loading.

---

## Affected Services

| Service | Environment | Impact | Duration |
|---|---|---|---|
| `linkding-db` (CNPG) | Production | Replica 2 CrashLoopBackOff — DB cluster degraded | ~12 days |
| `linkding-db` (CNPG) | Staging | Replicas 2 & 3 CrashLoopBackOff — DB cluster degraded | ~12 days |
| `memos-db` (CNPG) | Staging | Replicas 2 & 3 CrashLoopBackOff — DB cluster degraded | ~11 days |
| `vitals-db` (CNPG) | Production | **All 3 instances** read-only — cluster fully degraded | ~8 days |
| `vitals-db` (CNPG) | Staging | **All 3 instances** read-only — cluster fully degraded | ~8 days |
| `loki-0` | Monitoring | WAL write failures → HTTP 500 to promtail | ~3 days |
| `promtail` | Monitoring | Readiness probe failing — log ingestion degraded | ~8 days (cascade) |
| `immich-machine-learning` | Production | `0/1` Running — ML features unavailable | ~3+ days |
| `immich-machine-learning` | Staging | `0/1` Running + stuck Terminating pod | ~3+ days |

---

## Root Causes

### Root Cause 1: Synology iSCSI Volume Read-Only (primary)

The Synology NAS iSCSI targets periodically served block devices as read-only to the Kubernetes node (`talos-519-vmy`). This is a known instability in the `synology-iscsi` CSI driver / iSCSI session management — when a session is disrupted and reconnects, the block device may reattach in read-only mode.

Once a PVC-backed volume is read-only:
- **PostgreSQL (CNPG)**: The `pg_wal` directory and `pgdata` become unwritable. Replicas crash with `FATAL: could not open file "...": Read-only file system` or `read-only file system` on `postgresql.conf`. At startup, `pg_controldata` returns exit status 1 on an empty/uninitialised volume.
- **Loki**: The WAL write path fails (`write /var/loki/wal/00001449: read-only file system`), causing Loki to return HTTP 500 to all push requests.
- **The fix** in all cases was pod deletion (forced PVC detach) followed by re-scheduling — which caused the CSI driver to detach and reattach the iSCSI volume, restoring read-write access.

### Root Cause 2: CNPG Replica PVC Initialisation Failure (secondary)

For **linkding** and **memos** replicas, the PVCs were not merely read-only — they were empty/uninitialised due to the CSI failure occurring during initial provisioning or streaming. The `pg_controldata` binary found no valid cluster directory. For these, the fix required:
1. `kubectl delete pod <replica>` — CNPG autopilot immediately tries to re-stream but finds the PVC empty
2. `kubectl delete pvc <replica-pvc>` — forces CNPG to provision a new PVC
3. CNPG automatically re-streams from primary to the new PVC

### Root Cause 3: immich-machine-learning Liveness Probe (independent)

The `immich-machine-learning` container uses ROCm/ONNX to load the ViT-B-32 CLIP model to the AMD GPU. This load takes **>300 seconds**. The liveness probe was configured with:
- `initialDelaySeconds: 300` — first check fires exactly when the model is loading
- `failureThreshold: 6` — only 60 seconds of tolerance after the initial delay

The container's HTTP server is not responsive during model loading, so the liveness probe killed the container before it could become healthy, creating an infinite restart loop.

---

## Timeline

| Time (UTC) | Event |
|---|---|
| ~2026-02-16 | First iSCSI session disruption — `linkding-db` replicas begin CrashLoopBackOff |
| ~2026-02-17 | `memos-db-staging` replicas begin CrashLoopBackOff |
| ~2026-02-19 | `vitals-db` prod+stage: all instances go read-only; `Cluster.status` shows "Instance Status Extraction Error" |
| ~2026-02-19 | `promtail` readiness probe begins failing (cascade from earlier Loki issues; 2334+ failures accumulated) |
| ~2026-02-25 | `immich-machine-learning` liveness loop begins (GPU model load taking >360s) |
| ~2026-02-25 | `loki-0` WAL becomes read-only |
| 2026-02-28 | Full cluster audit initiated; all issues diagnosed |
| 2026-02-28T00:00Z | `linkding-db-production-cnpg-v1-2`: pod+PVC deleted; re-streamed from `*-v1-3`; cluster 3/3 |
| 2026-02-28T00:05Z | `linkding-db-staging-cnpg-v1-{2,3}`: both pod+PVCs deleted; re-streamed from `*-v1-1`; cluster 3/3 |
| 2026-02-28T00:10Z | `memos-db-staging-cnpg-v1-{2,3}`: both pod+PVCs deleted; re-streamed from `*-v1-1`; cluster 3/3 |
| 2026-02-28T00:15Z | All 6 `vitals-db` pods bounced (both envs 3×); iSCSI reattached RW; all clusters 3/3 |
| 2026-02-28T00:20Z | `immich-machine-learning` stuck Terminating pod force-deleted in staging |
| 2026-02-28T00:25Z | Immich ML liveness probe fix committed to `fix/pod-health-cnpg-immich-ml` |
| 2026-02-28T00:30Z | `loki-0` pod deleted; storage-loki-0 PVC reattached RW; Loki `2/2 Running` |
| 2026-02-28T00:42Z | `promtail` readiness probe passes; `1/1 Running` |
| 2026-02-28T00:45Z | PR #197 created for immich ML probe fix |
| 2026-02-28T00:50Z | Final cluster validation: all CNPG clusters 3/3 healthy; loki+promtail healthy |

---

## Impact

- **Linkding** (prod + stage): Database cluster was running in degraded mode (1 of 3 replicas missing in prod, 1 of 3 in staging) for ~12 days. The primary remained healthy throughout, so the service was functionally available but without HA protection.
- **Memos** (staging): Database cluster degraded for ~11 days. Staging service remained available via primary.
- **Vitals** (prod + stage): ALL instances were read-only — the primary included. PostgreSQL was likely rejecting all writes. Vitals monitoring was fully unavailable in both environments.
- **Loki** (monitoring): Log ingestion pipeline broken — all logs from the cluster were dropped for ~3 days. Observability gap during this window.
- **Promtail** (monitoring): Unhealthy for ~8 days (initially from a prior Loki disruption, exacerbated by the February 25th WAL failure). Log shipping failed silently.
- **Immich ML** (prod + stage): Smart search, face recognition, and ML-based features were unavailable for ~3+ days in both environments.

---

## Fixes Applied

### Imperatively applied (cluster state fixes)

These were operationally necessary but are **not tracked in git** — they are transient pod/PVC deletions to recover the CSI volumes.

| Action | Command |
|---|---|
| linkding-prod replica recover | `kubectl delete pod linkding-db-production-cnpg-v1-2 -n linkding-prod` + `kubectl delete pvc ...` |
| linkding-stage replicas recover | Same for `*-v1-2` and `*-v1-3` in `linkding-stage` |
| memos-stage replicas recover | Same for `memos-db-staging-cnpg-v1-{2,3}` in `memos-stage` |
| vitals-prod pod bounce | `kubectl delete pod vitals-db-production-cnpg-v1-{1,2,3} -n vitals-prod` |
| vitals-stage pod bounce | `kubectl delete pod vitals-db-staging-cnpg-v1-{1,2,3} -n vitals-stage` |
| loki-0 pod bounce | `kubectl delete pod loki-0 -n monitoring` |

### Code changes (tracked in git)

**PR #197** — `fix(immich): increase machine-learning liveness probe thresholds for ROCm`  
Branch: `fix/pod-health-cnpg-immich-ml`

Files changed:
- `apps/base/immich/deployment.yaml` — liveness probe `initialDelaySeconds` 300→600, `failureThreshold` 6→30
- `apps/production/immich/deployment-patch.yaml` — same changes in prod overlay

---

## Post-Incident State

After all fixes applied:

```
All CNPG clusters:        10/10 → "Cluster in healthy state" (3/3 instances each)
loki-0:                   2/2   Running
promtail-cqcqm:           1/1   Running  (Ready since 2026-02-28T00:41:45Z)
immich-machine-learning:  0/1   Running in prod+stage (pending PR #197 merge)
```

---

## Detection & Alerting Gap

This failure accumulated silently over 12 days. The cluster had no alerting that fired for:
- CNPG replica CrashLoopBackOff (no PagerDuty/alertmanager rule for `cluster.status.instances < cluster.spec.instances`)
- iSCSI read-only file systems (no node-level filesystem read-only alert)
- Loki ingest failures (no alert on Loki HTTP 500 rate or promtail backpressure)
- Promtail readiness probe failures (health was visible in `kubectl get pods` but not alerted)

---

## Action Items

| Priority | Item | Owner |
|---|---|---|
| HIGH | Add PrometheusRule: alert when CNPG `cluster_status_instances` < `cluster_spec_instances` for >10m | infra |
| HIGH | Add PrometheusRule: alert when any PVC-backed pod has read-only filesystem (node exporter `node_filesystem_readonly == 1`) | infra |
| HIGH | Add PrometheusRule: alert for Loki push error rate >0 for >5m | monitoring |
| MEDIUM | Investigate iSCSI session stability — Synology firmware / kernel iSCSI parameters | infra |
| MEDIUM | Consider moving volatile CNPG WAL/data to local node storage OR implement automated iSCSI session health checks | infra |
| MEDIUM | Add PrometheusRule: alert for DaemonSet pod not ready > 10m (catches promtail, node-exporter) | monitoring |
| LOW | Merge PR #197 to unblock immich-machine-learning in prod+stage | immich |
| LOW | Document iSCSI recovery runbook update with "force delete pod+PVC" procedure for CNPG replicas | docs |

---

## References

- [PR #197 — fix(immich): increase machine-learning liveness probe thresholds for ROCm](https://github.com/gjcourt/homelab/pull/197)
- [docs/guides/synology-iscsi-operations.md](../guides/synology-iscsi-operations.md)
- [docs/incidents/2026-02-27-homeassistant-staging-iscsi-io-error.md](2026-02-27-homeassistant-staging-iscsi-io-error.md)
- [docs/incidents/2026-02-20-immich-staging-wal-archive-failure.md](2026-02-20-immich-staging-wal-archive-failure.md)
- CloudNative-PG docs: [Replica recovery](https://cloudnative-pg.io/documentation/current/replica_cluster/)
