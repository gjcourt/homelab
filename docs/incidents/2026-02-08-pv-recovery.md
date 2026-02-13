# Post-Mortem: Staging PV Release & CNPG Recovery (2026-02-08)

## Summary

An improper shutdown of the Synology NAS while the `melodic-muse` cluster was running caused iSCSI-backed PersistentVolumes to lose their attachment state. When the cluster reconciled, the Synology CSI driver could not reconnect the existing iSCSI targets and instead provisioned new empty volumes. The old PVs transitioned to `Released` status, and all staging app PVCs bound to the new empty PVs—resulting in complete data loss across staging.

## Timeline

| Time | Event |
|---|---|
| ~Jan 1 – Feb 7 | Staging environment running normally; all PVs bound and healthy |
| ~Feb 7 | Synology NAS improperly shut down while cluster pods were still running |
| Feb 7 18:38 | Cluster nodes detected iSCSI target loss; CSI driver began recovery |
| Feb 7 18:39+ | Synology CSI provisioned **new empty PVs** for each PVC (dynamic provisioner honored existing PVCs, but old iSCSI LUNs were stale) |
| Feb 7 | Old PVs transitioned to `Released` (claimRef still set, but PVCs bound to new volumes) |
| Feb 8 ~05:00 | Investigation began. 37 Released/Available PVs identified across staging |

## Root Cause

The Synology CSI driver uses iSCSI targets mapped to LUNs on the NAS. When the NAS shut down uncleanly:

1. **iSCSI sessions dropped** on all cluster nodes
2. Kubelet marked volumes as unmountable; pods using them entered `CrashLoopBackOff` or `ContainerCreating`
3. The CSI driver's `NodeStageVolume` calls failed for existing PVs (stale iSCSI targets)
4. The dynamic provisioner, seeing PVCs without a healthy bound volume, **provisioned new PVs** and bound them
5. Old PVs retained their data on NAS LUNs but lost their PVC binding → `Released` state
6. When the NAS came back, the old iSCSI targets were accessible again but no longer referenced by any PVC

The `Retain` reclaim policy prevented data deletion on the old PVs, which made recovery possible.

## Impact

- **12 staging app data PVCs** bound to new empty PVs (all app data appeared lost)
- **3 CNPG database PVs** (linkding, memos, immich) also released, each with a full PostgreSQL data directory
- **9+ CNPG replica PVs** released (expendable—replicas rebuild from primary)
- Apps were running but with empty volumes—no user-visible errors, just missing data

## Recovery: App Data PVCs (12 volumes)

### Approach: claimRef Pre-reservation

Direct PVC recreation with `volumeName` was incompatible with Flux (PVC spec is immutable; Flux-managed PVCs don't set `volumeName`). The working approach:

1. **Suspend Flux** (`flux suspend kustomization apps-staging`)
2. **Scale all staging deployments to 0** (release volume attachments)
3. **Delete all staging PVCs** (Flux-managed)
4. **Patch each old PV** with `spec.claimRef` pointing to the expected PVC name/namespace (UID left null initially):
   ```
   kubectl patch pv <pv-name> --type=merge -p \
     '{"spec":{"claimRef":{"namespace":"<ns>","name":"<pvc-name>","uid":null}}}'
   ```
5. **Resume Flux** — Flux recreated PVCs from git (without `volumeName`); the PV controller matched them to pre-reserved PVs via claimRef
6. **Patch PVs with actual PVC UIDs** (some PVs showed `Lost` until UID was set):
   ```
   kubectl patch pv <pv-name> --type=merge -p \
     '{"spec":{"claimRef":{"uid":"<actual-pvc-uid>"}}}'
   ```
7. **Scale deployments back to 1**

### Key failure mode encountered
One PVC (`authelia-data`) bound to a newly-provisioned PV before the claimRef could be set (race with dynamic provisioner). Fix: delete the PVC + new PV, re-reconcile Flux, and immediately patch the claimRef with the new PVC's UID.

## Recovery: CNPG Database PVCs (3 volumes)

CNPG-managed PVCs are created by the operator, not Flux. This required a different approach and multiple failed attempts before success.

### Failed Attempt 1: Manual PVC with `volumeName`

Created PVCs manually with `volumeName` pointing to old PVs. CNPG ran `initdb` which:
- Detected existing PGDATA via `pg_controldata`
- **Renamed** old data to `pgdata_<timestamp>`
- Created a fresh empty database as `pgdata`
- After initdb completed, operator went into "Cluster is unrecoverable" because it detected previously-created instances but PVCs lacked CNPG metadata

### Failed Attempt 2: Delete/recreate clusters with PVCs in place

Deleted CNPG cluster objects and let Flux recreate them. The operator ran initdb again (renaming data again), then went unrecoverable again. Same annotations issue.

### Successful Approach

1. **Identified missing CNPG PVC metadata** by comparing with a healthy production PVC:
   - Missing annotations: `cnpg.io/pvcStatus: ready`, `cnpg.io/nodeSerial: 1`, `cnpg.io/operatorVersion: 1.26.1`
   - Missing label: `cnpg.io/pvcRole: PG_DATA`

2. **Restored original PGDATA directories** using temporary busybox pods:
   ```sh
   # Mount PV, delete fresh pgdata, rename original back
   rm -rf pgdata pgdata_<second-initdb-timestamp>
   mv pgdata_<original-timestamp> pgdata
   ```
   Original data directories identified by Jan 1 file timestamps vs Feb 8 initdb timestamps.

3. **Annotated PVCs** with the required CNPG metadata:
   ```
   kubectl annotate pvc <name> -n <ns> \
     cnpg.io/nodeSerial=1 \
     cnpg.io/operatorVersion=1.26.1 \
     cnpg.io/pvcStatus=ready
   ```

4. **Deleted cluster objects** and reconciled Flux. This time:
   - Operator found PVCs with `cnpg.io/pvcStatus: ready` → **skipped initdb**
   - Started postgres instances directly with existing PGDATA
   - Primaries came up healthy; replicas began streaming replication

### Data verification

```
linkding: 3/3 replicas healthy, app serving HTTP 200
memos:    primary healthy, 4 memos + 1 user confirmed in DB
immich:   primary healthy, server running
```

All data recovered to the state as of Feb 7 (last write before NAS shutdown).

## Lessons & Action Items

| # | Item | Priority |
|---|---|---|
| 1 | **Enable CNPG scheduled base backups** — only WAL archives were in S3, no base backups. `pg_basebackup`-based recovery was impossible. Add `ScheduledBackup` resources. | High |
| 2 | **Document CNPG PVC metadata requirements** — the `cnpg.io/pvcStatus: ready` annotation is the critical signal that tells the operator a PVC is operator-managed and data-bearing. Without it, the operator always runs initdb. | High |
| 3 | **Clean up 37 orphaned Released PVs** — these consume iSCSI LUNs on the NAS. Delete after confirming no further data is needed. | Medium |
| 4 | **Add NAS shutdown procedures** — document that the NAS must not be shut down while the cluster is running, or staging workloads should be scaled down first. | Medium |
| 5 | **Consider velero or similar for PV snapshots** — the `Retain` reclaim policy saved us, but having scheduled snapshots would provide point-in-time recovery. | Low |

## Appendix: PV Mapping

### App Data (12 PVs recovered)

| App | PV | Size | PVC |
|---|---|---|---|
| audiobookshelf-data | `pvc-1145acca` | 2Gi | `audiobookshelf-stage/audiobookshelf-data-pvc` |
| audiobookshelf-meta | `pvc-ac16bde9` | 2Gi | `audiobookshelf-stage/audiobookshelf-meta-data-pvc` |
| authelia | `pvc-aa3dff32` | 1Gi | `authelia-stage/authelia-data` |
| homeassistant | `pvc-b4a70733` | 10Gi | `homeassistant-stage/homeassistant-config-pvc` |
| immich-upload | `pvc-2bc0b363` | 100Gi | `immich-stage/immich-upload-pvc` |
| immich-model-cache | `pvc-2994dbba` | 10Gi | `immich-stage/immich-model-cache-pvc` |
| jellyfin-config | `pvc-05ae507a` | 5Gi | `jellyfin-stage/jellyfin-config-pvc` |
| jellyfin-cache | `pvc-1d92b4bd` | 10Gi | `jellyfin-stage/jellyfin-cache-pvc` |
| linkding | `pvc-55c1bd3e` | 1Gi | `linkding-stage/linkding-data-pvc` |
| mealie | `pvc-dcffa60c` | 1Gi | `mealie-stage/mealie-data-pvc` |
| memos | `pvc-5424d4f7` | 1Gi | `memos-stage/memos-data-pvc` |
| snapcast | `pvc-621ac65b` | 1Gi | `snapcast-stage/snapcast-spotify-state` |

### CNPG Databases (3 PVs recovered)

| DB | PV | Size | Data Range |
|---|---|---|---|
| linkding | `pvc-0a4e4f3a` | 2Gi | Jan 1 – Feb 7 |
| memos | `pvc-50773850` | 10Gi | Jan 1 – Feb 7 |
| immich | `pvc-9fc35fcd` | 10Gi | Jan 1 – Feb 7 |
