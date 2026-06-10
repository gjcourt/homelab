# Storage

## 1. Overview
Storage in the homelab is provided by a TrueNAS Scale server, `hestia` (`10.42.2.10`). The cluster uses the [democratic-csi](https://github.com/democratic-csi/democratic-csi) driver to dynamically provision iSCSI LUNs (for ReadWriteOnce block storage). NFS shares from `alcatraz` (`10.42.2.11`) back the Immich photo library and Jellyfin/Navidrome media libraries via static `PersistentVolume` manifests (ReadWriteMany / ReadOnlyMany).

## 2. Architecture
The democratic-csi driver is deployed in the `democratic-csi` namespace as a Helm chart. It speaks to the TrueNAS API to create, delete, snapshot, and manage iSCSI LUNs on the `main` ZFS pool.
- **iSCSI** (truenas): RWO block storage for databases (CNPG), app config (`-data`/`-config` PVCs), and STS volumeClaimTemplates.
- **NFS** (alcatraz): RWX/ROX shared file storage for the photo and media libraries. Mounted via static PVs declared in the app overlays (e.g. `apps/production/immich/nfs-photos.yaml`, `apps/base/jellyfin/media/nfs-media.yaml`).

## 3. URLs
- **TrueNAS Scale (hestia)**: https://hestia.burntbytes.com (via Cloudflare Tunnel) or https://10.42.2.10 (LAN-direct)

## 4. Configuration
- **Storage Classes**:
  - `truenas-iscsi` (Default): Persistent iSCSI storage on the `main` pool (`ReclaimPolicy: Retain`).
  - `truenas-iscsi-ssd`: iSCSI storage targeted at the SSD-backed dataset (`ReclaimPolicy: Retain`).
  - `truenas-iscsi-ephemeral`: Ephemeral iSCSI storage (`ReclaimPolicy: Delete`).
- **Volume Snapshot Classes**:
  - Provided by democratic-csi; see `kubectl get volumesnapshotclasses`.
- **Secrets**:
  - `democratic-csi` Helm values reference TrueNAS API credentials encrypted via SOPS in `infra/controllers/democratic-csi/`.

## 5. Usage Instructions
To provision storage, create a `PersistentVolumeClaim` (PVC) referencing the desired `StorageClass`.

Example iSCSI PVC:
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: example-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: truenas-iscsi
  resources:
    requests:
      storage: 10Gi
```

For shared media NFS mounts, define a static `PersistentVolume` (see `apps/base/jellyfin/media/nfs-media.yaml` for the canonical pattern) and bind a `PersistentVolumeClaim` to it via `volumeName`.

## 6. Testing
To verify the CSI driver is working:
```bash
kubectl get pods -n democratic-csi
kubectl get storageclasses
```
Create a test PVC and verify it binds successfully:
```bash
kubectl get pvc example-pvc
```

## 7. Monitoring & Alerting
- **Metrics**: The democratic-csi controller exposes metrics related to volume provisioning and attachment.
- **Logs**: Check the CSI controller and node plugin logs:
  ```bash
  kubectl logs -n democratic-csi deploy/democratic-csi-controller
  kubectl logs -n democratic-csi ds/democratic-csi-node
  ```
- **TrueNAS dashboard**: `truenas-iscsi-monitor` (in `apps/base/truenas-iscsi-monitor/`) exports TrueNAS-side iSCSI metrics and target-count alerts to Prometheus.

## 8. Disaster Recovery
- **Backup Strategy**:
  - iSCSI LUNs sit on ZFS datasets that are snapshotted by TrueNAS's periodic-snapshot tasks.
  - Application-level backups (e.g. CNPG → S3 via the Barman Cloud plugin) are preferred over raw block snapshots for databases.
  - The Immich photo library is mirrored from alcatraz to hestia on a daily rsync schedule with ZFS snapshot retention (see `docs/plans/2026-05-20-alcatraz-to-hestia-migration.md` Phase 2).
- **Restore Procedure**:
  - For ZFS snapshot rollback: use TrueNAS UI or `zfs rollback <dataset>@<snap>`.
  - To recover a destroyed PVC whose PV had `Retain`: clear the PV's `claimRef`, then create a new PVC with `volumeName` pointing to the PV (see `docs/operations/pv-retain-recovery.md` or memory `pv-retain-recovery-pattern`).

## 9. Troubleshooting
- **PVC stuck in Pending**:
  - Check the democratic-csi controller logs for TrueNAS API errors.
  - Verify hestia is reachable from the cluster (`kubectl run debug --image=alpine -- nc -vz 10.42.2.10 3260`).
- **Volume Attachment Issues**: If a pod is stuck terminating and the volume cannot be detached, you may need to force-delete the pod or manually disconnect the iSCSI session on the Talos node.
- **Flux SSA + `volumeName` immutability**: For statically-bound PVCs under Flux management, prefer `PV.claimRef` pre-binding over `PVC.spec.volumeName` — Flux's strategic-merge dry-run will otherwise try to unset `volumeName` and fail with "spec is immutable". See memory `flux-pvc-volumename-anti-pattern`.

## 10. Historical context
The cluster originally ran on Synology iSCSI (`10.42.2.11`, `synology-csi` driver). The full migration to TrueNAS/hestia happened in May 2026 and is documented in `docs/plans/2026-05-20-alcatraz-to-hestia-migration.md`. Alcatraz now serves only the photo NFS share and is the rsync source for the hestia photo-backup dataset.
