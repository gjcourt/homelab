# Storage

## 1. Overview
Storage in the homelab is provided by a Synology NAS (`192.168.5.8`). The cluster uses the Synology CSI driver to dynamically provision both iSCSI LUNs (for block storage) and NFS shares (for shared file storage).

## 2. Architecture
The Synology CSI driver is deployed in the `synology-csi` namespace. It communicates with the Synology DSM API to create, delete, and manage storage volumes.
- **iSCSI**: Used for high-performance, ReadWriteOnce (RWO) block storage (e.g., databases, application data).
- **NFS**: Used for ReadWriteMany (RWX) shared file storage (e.g., media libraries).

## 3. URLs
- **Synology DSM**: https://192.168.5.8:5001

## 4. Configuration
- **Storage Classes**:
  - `synology-iscsi` (Default): Persistent iSCSI storage (`ReclaimPolicy: Retain`).
  - `synology-iscsi-ephemeral`: Ephemeral iSCSI storage (`ReclaimPolicy: Delete`).
  - `synology-nfs`: NFS storage for shared media (`ReclaimPolicy: Retain`).
- **Volume Snapshot Classes**:
  - `synology-iscsi-snapshot`: Used for taking snapshots of iSCSI volumes.
- **Secrets**:
  - `client-info-secret`: Contains the Synology DSM credentials (username/password) required by the CSI driver. Encrypted via SOPS.

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
  storageClassName: synology-iscsi
  resources:
    requests:
      storage: 10Gi
```

## 6. Testing
To verify the CSI driver is working:
```bash
kubectl get pods -n synology-csi
kubectl get storageclasses
```
Create a test PVC and verify it binds successfully:
```bash
kubectl get pvc example-pvc
```

## 7. Monitoring & Alerting
- **Metrics**: The CSI driver exposes metrics related to volume provisioning and attachment.
- **Logs**: Check the CSI controller and node plugin logs:
  ```bash
  kubectl logs -n synology-csi deploy/synology-csi-controller
  kubectl logs -n synology-csi ds/synology-csi-node
  ```

## 8. Disaster Recovery
- **Backup Strategy**: 
  - iSCSI LUNs are backed up using Synology Snapshot Replication or Hyper Backup on the NAS itself.
  - Application-level backups (e.g., CNPG for Postgres) are preferred over raw block snapshots for databases.
- **Restore Procedure**: 
  - Restore the LUN via Synology DSM.
  - Recreate the PV/PVC in Kubernetes pointing to the restored LUN.

## 9. Troubleshooting
- **PVC stuck in Pending**: 
  - Check the CSI controller logs for API errors (e.g., invalid credentials, target limits reached).
  - Verify the Synology NAS is reachable from the cluster.
- **iSCSI Zombie Targets**: The Synology NAS has a hard limit of 128 iSCSI targets. If this limit is reached, new PVCs cannot be provisioned. See `docs/guides/synology-iscsi-operations.md` for cleanup procedures.
- **Volume Attachment Issues**: If a pod is stuck terminating and the volume cannot be detached, you may need to force delete the pod or manually disconnect the iSCSI session on the node.
