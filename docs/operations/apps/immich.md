# Immich

## 1. Overview
Immich is a high-performance, self-hosted photo and video backup solution directly from your mobile phone. In this homelab, it serves as the primary Google Photos replacement, featuring hardware-accelerated video transcoding and machine learning for facial recognition and object detection.

## 2. Architecture
Immich is deployed as a set of microservices in the `immich-prod` (and `immich-stage`) namespace:
- **immich-server**: The main API and web interface. Handles uploads, metadata extraction, and video transcoding.
- **immich-machine-learning**: A dedicated service for running ML models (facial recognition, CLIP).
- **immich-redis**: Used for job queue management and caching.
- **PostgreSQL (CNPG)**: A CloudNativePG cluster (`immich-db-prod-cnpg-v2`) storing metadata and vector embeddings (pgvector).

### Storage
- **Uploads**: `immich-upload-pvc` (iSCSI) - Temporary storage for incoming files and generated thumbnails/transcodes.
- **Photos**: `immich-photos-pvc` (NFS) - The main library storage, mounted from the Synology NAS.
- **Model Cache**: `immich-model-cache-pvc` (iSCSI) - Caches downloaded ML models.

### Hardware Acceleration
Both the `immich-server` and `immich-machine-learning` pods mount `/dev/dri` from the host node to utilize the AMD GPU for hardware-accelerated video transcoding (VAAPI) and machine learning inference (ROCm).

## 3. URLs
- **Staging**: https://photos.stage.burntbytes.com
- **Production**: https://photos.burntbytes.com

## 4. Configuration
- **Environment Variables**:
  - Global settings are in the `immich-config` ConfigMap.
  - Database credentials are automatically injected from the CNPG cluster secret (`immich-db-prod-cnpg-v2-app`).
  - Hardware acceleration variables (`LIBVA_DRIVER_NAME`, `HSA_OVERRIDE_GFX_VERSION`) are set in the deployment patches.
- **ConfigMaps/Secrets**:
  - `immich-oauth-config`: Contains the `immich-config.yaml` template for configuring Authelia OIDC SSO.
  - `immich-sso-secret`: Contains the OIDC client secret. An init container injects this into the config template at startup.

## 5. Usage Instructions
- **Web UI**: Navigate to the URL and log in via Authelia (SSO).
- **Mobile App**: Download the Immich app (iOS/Android), enter the server URL, and log in via OAuth.

## 6. Testing
To verify Immich is working:
1. Navigate to the web UI and ensure photos load.
2. Check the Administration -> Jobs page to ensure background jobs (thumbnail generation, ML) are processing.
3. Verify all pods are running: `kubectl get pods -n immich-prod`

## 7. Monitoring & Alerting
- **Metrics**: Immich exposes Prometheus metrics.
- **Logs**:
  - Server logs: `kubectl logs -n immich-prod deploy/immich-server`
  - ML logs: `kubectl logs -n immich-prod deploy/immich-machine-learning`
- **Database**: Monitor the CNPG cluster status: `kubectl get cluster -n immich-prod`

## 8. Disaster Recovery
- **Backup Strategy**:
  - **Database**: The CNPG cluster is configured to take daily backups and stream WAL archives to an S3-compatible bucket (MinIO/Cloudflare R2).
  - **Photos**: The NFS share (`/mnt/photos`) is backed up natively on the Synology NAS using Hyper Backup.
  - **Uploads/Thumbnails**: The iSCSI volume is backed up via Synology Snapshot Replication.
- **Restore Procedure**:
  1. Restore the Postgres database using the CNPG recovery instructions (see `docs/plans/2026-02-21-cnpg-backup-upgrade.md`).
  2. Ensure the NFS share is intact.
  3. Re-deploy the Immich manifests.

## 9. Troubleshooting
- **Machine Learning Pod Crashing (OOM/Timeout)**:
  - The ML pod requires significant memory and CPU. If it crashes with Exit Code 143 or 132, check the `MACHINE_LEARNING_WORKERS` setting in the `immich-config` ConfigMap. Reducing workers can prevent OOM kills.
  - Ensure the liveness/readiness probe timeouts are sufficiently high (e.g., 10s) as model loading can block the event loop.
- **Hardware Transcoding Failing**:
  - Verify `/dev/dri` is mounted correctly and the pod has the necessary privileges/groups (e.g., `video`, `render`).
  - Check the server logs for FFmpeg errors.
- **Database Connection Issues**: Verify the CNPG cluster is healthy and the credentials secret matches the deployment environment variables.

## 10. Staging DB: fixed 10Gi mirror of prod

The staging Postgres cluster (`immich-db-staging-cnpg-v1`, ns `immich-stage`) is intended to be a **stable, fixed-size mirror of prod** for rehearsal — same shape as production (`immich-db-prod-cnpg-v3`, live at **10Gi**), just a smaller-scale disposable copy. The standard staging DB size is therefore **10Gi**, set in `apps/staging/immich/database.yaml` (`spec.storage.size`).

### Why a recreate is required to change the size

**CNPG cannot shrink a PVC in-place.** The staging PVCs had drifted to 20Gi; `apps/staging/immich/database.yaml` now declares 10Gi, but Flux applying that manifest does **not** shrink the live volumes. The size only takes effect when the Cluster (and its PVCs) is destroyed and recreated. Staging data is disposable/rebuildable, so this is safe to do whenever convenient.

### Recreate procedure (operator, live cluster)

Run from a machine with `kubectl` LAN access. Suspend Flux first so it does not race the delete.

```bash
# 1. Suspend Flux staging reconciliation so it doesn't recreate mid-delete.
flux suspend kustomization apps-staging -n flux-system

# 2. Scale immich-server down so nothing writes to the DB during teardown.
kubectl scale deploy -n immich-stage immich-server --replicas=0

# 3. Delete the CNPG Cluster (this removes the managed pods; PVCs are Retain so they persist).
kubectl delete cluster -n immich-stage immich-db-staging-cnpg-v1

# 4. Delete the live staging DB PVCs (currently 20Gi) so they can be recreated at 10Gi.
#    Check first, then delete each one that belongs to the cluster:
kubectl get pvc -n immich-stage | grep immich-db-staging-cnpg-v1
kubectl delete pvc -n immich-stage <each immich-db-staging-cnpg-v1-N above>

# 5. Resume Flux; it recreates the Cluster from Git at size: 10Gi.
flux resume kustomization apps-staging -n flux-system
flux reconcile kustomization apps-staging -n flux-system

# 6. Watch the cluster bootstrap (recovery from S3 per bootstrap.recovery in the manifest).
kubectl get cluster -n immich-stage -w
kubectl get pods  -n immich-stage -l cnpg.io/cluster=immich-db-staging-cnpg-v1 -w

# 7. Scale immich-server back up and verify the app.
kubectl scale deploy -n immich-stage immich-server --replicas=1
```

New PVCs will be created at 10Gi. Verify with:

```bash
kubectl get cluster -n immich-stage immich-db-staging-cnpg-v1 -o jsonpath='{.spec.storage.size}'; echo
kubectl get pvc -n immich-stage | grep immich-db-staging-cnpg-v1   # expect 10Gi
```

> Note: the staging cluster bootstraps via `bootstrap.recovery` (rebuilt from backup during the 2026-04-18 DR test). It restores from the S3 ObjectStore, so a recreate re-hydrates staging from the last good backup — no manual data reload needed. If the WAL/serverName path needs bumping during recovery, see the History comments in `database.yaml`.

### Orphaned Retain PV cleanup (operator, destructive)

Staging accumulated a backlog of `Released` + `Retain` PVs from prior CNPG replica churn (replicas -1/-2/-3/-4/-6 recycled multiple times) plus some stale app volumes. These are **not** in use (`Released` = no bound claim) and hold no data worth keeping — delete them to reclaim the underlying iSCSI/zvol space. Confirm each is still `Released` before deleting:

```bash
kubectl get pv | grep -i immich-stage | grep Released
```

Then delete each Released PV (do NOT touch any `Bound` PV — those back the live cluster):

```bash
# CNPG replica PVCs (old, recycled) — 9 volumes:
kubectl delete pv pvc-03264ca4-89e8-47e6-a8ae-02e4498170c9   # immich-db-staging-cnpg-v1-1
kubectl delete pv pvc-92fed6fa-85b7-453b-8a1d-1c24a5b307f1   # immich-db-staging-cnpg-v1-1
kubectl delete pv pvc-f5cd392a-e666-4f99-b22e-55e582c12b95   # immich-db-staging-cnpg-v1-1
kubectl delete pv pvc-06cc39fd-63f8-4c0b-bcdd-b27ad7555b3c   # immich-db-staging-cnpg-v1-2
kubectl delete pv pvc-f31762ee-0322-43c8-be6b-b5d5e55da7c1   # immich-db-staging-cnpg-v1-2
kubectl delete pv pvc-08245378-9a1a-454a-867a-798ebab2590d   # immich-db-staging-cnpg-v1-3
kubectl delete pv pvc-cd8f88f9-c2cd-4f97-a766-d786667001bd   # immich-db-staging-cnpg-v1-3
kubectl delete pv pvc-832d02bc-c96e-4d99-8d3d-dfb988687019   # immich-db-staging-cnpg-v1-4
kubectl delete pv pvc-78526a2e-dc00-48ae-b93b-4dce00cb9273   # immich-db-staging-cnpg-v1-6
# Stale app volumes (superseded by current Bound PVs):
kubectl delete pv pvc-4a7d497e-02ce-4469-bb59-23e78ce3dc6f   # immich-upload-pvc-tmp (100Gi)
kubectl delete pv pvc-5d411d62-80c3-4d53-b76b-232a51e8e60f   # immich-upload-pvc (100Gi, old)
kubectl delete pv pvc-574d9a9b-d8cc-4b79-b2ea-9d29637856e3   # immich-model-cache-pvc (10Gi, old)
```

The underlying democratic-csi zvols/LUNs on TrueNAS/Synology are `Retain`, so deleting the PV frees the Kubernetes object but the backing dataset may need a separate reclaim on the NAS if space is tight. The PV UUIDs above are a snapshot from 2026-07-03 — always re-run the `grep Released` command and delete by current name.
