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
  1. Restore the Postgres database using the CNPG recovery instructions (see `docs/plans/cnpg-backup-upgrade.md`).
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
