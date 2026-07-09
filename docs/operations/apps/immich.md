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

## 10. Staging testbed (fixed 5Gi DB + last-30-days external library)

`immich-stage` is a small, **fixed-size, representative rehearsal environment** — not a mirror of prod. After the Immich v3 + VectorChord migration, staging had 0 photos / 0 embeddings, so it had little rehearsal value. The testbed gives it a **bounded, realistic dataset** (the last ~30 days of the real photo tree) at a **fixed small size**, kept fresh automatically.

Two independent pieces:

1. **Fixed 5Gi DB, fresh-initdb bootstrap** — the CNPG cluster is 5Gi and initializes **empty** (no S3 recovery), then Immich re-indexes the 30-day library.
2. **Last-30-days external library** — a daily CronJob maintains a bounded slice of the prod photo tree on hestia; Immich indexes only that slice.

### 10.1 Fixed 5Gi DB + fresh-bootstrap correctness

`apps/staging/immich/database.yaml` sets `spec.storage.size: 5Gi`. This is **independent of prod** (prod is 10Gi) — staging holds no real data, so it is sized only for the 30-day metadata + embeddings.

**Why fresh initdb, not recovery.** Staging previously bootstrapped via `bootstrap.recovery` (a 2026-04-18 DR artifact), which re-hydrated the whole pre-migration DB from S3 — large, and pointless for a 30-day testbed. It now uses `bootstrap.initdb`, mirroring prod, so a recreate yields an **empty** DB that Immich repopulates by scanning the slice.

**Why postInitSQL is critical.** The DB runs on the VectorChord-only image (`ghcr.io/tensorchord/cloudnative-vectorchord:16-0.4.2`, `shared_preload_libraries: [vchord.so]`). The `immich` role is **not** a superuser and cannot `CREATE EXTENSION` (this bit prod). `bootstrap.initdb.postInitSQL` runs once at init **as the postgres superuser**, so it creates the extensions a fresh cluster needs:

```yaml
postInitSQL:
  - CREATE EXTENSION IF NOT EXISTS vchord CASCADE;      # CASCADEs in pgvector
  - CREATE EXTENSION IF NOT EXISTS earthdistance CASCADE;
```

Without this, a fresh 5Gi bootstrap would leave Immich unable to create vchord → broken. (The legacy `immich-db-init` Job in `job-patch.yaml` still issues `CREATE EXTENSION vectors` — the *removed* pgvecto.rs extension — and may log errors on the VectorChord-only image. It is redundant now that postInitSQL creates the real extensions and does **not** gate the app; a cleanup of that Job for both prod and staging is a separate follow-up, intentionally not done here to preserve prod/staging parity.)

### 10.2 DB recreate procedure (operator, live cluster) — REQUIRED to apply 5Gi

**CNPG cannot shrink a PVC in-place, and its admission webhook REJECTS a spec whose size is < the live PVCs, which blocks the entire `apps-staging` reconcile** (learned 2026-07-03 — #1028 set 10Gi against a live 20Gi and broke reconcile). So the `20Gi → 5Gi` change in Git **must be paired with a destroy + recreate**. The switch from `bootstrap.recovery → bootstrap.initdb` also only takes effect on (re)create. Run from a machine with `kubectl` LAN access:

```bash
# 1. Suspend Flux staging reconciliation so it doesn't race the delete
#    (also avoids the webhook rejecting the 5Gi-vs-live-20Gi spec mid-flight).
flux suspend kustomization apps-staging -n flux-system

# 2. Scale immich-server down so nothing writes to the DB during teardown.
kubectl scale deploy -n immich-stage immich-server --replicas=0

# 3. Delete the CNPG Cluster (removes managed pods; PVCs are Retain so they persist).
kubectl delete cluster -n immich-stage immich-db-staging-cnpg-v1

# 4. Delete the live staging DB PVCs (currently 20Gi) so they are recreated at 5Gi.
#    Inspect first, then delete each one that belongs to the cluster:
kubectl get pvc -n immich-stage | grep immich-db-staging-cnpg-v1
kubectl delete pvc -n immich-stage <each immich-db-staging-cnpg-v1-N above>

# 5. Resume Flux; it recreates the Cluster from Git at size: 5Gi with fresh initdb.
flux resume kustomization apps-staging -n flux-system
flux reconcile kustomization apps-staging -n flux-system

# 6. Watch the cluster bootstrap (fresh initdb — NOT S3 recovery). postInitSQL creates
#    vchord + earthdistance. WAL now archives to the empty serverName ...-v5 path.
kubectl get cluster -n immich-stage -w
kubectl get pods  -n immich-stage -l cnpg.io/cluster=immich-db-staging-cnpg-v1 -w

# 7. Scale immich-server back up.
kubectl scale deploy -n immich-stage immich-server --replicas=1
```

Verify:

```bash
kubectl get cluster -n immich-stage immich-db-staging-cnpg-v1 -o jsonpath='{.spec.storage.size}'; echo   # 5Gi
kubectl get pvc -n immich-stage | grep immich-db-staging-cnpg-v1                                          # expect 5Gi
# Extensions present (fresh initdb):
kubectl exec -n immich-stage immich-db-staging-cnpg-v1-1 -- \
  psql -U postgres -d immich -c '\dx' | grep -E 'vchord|vector|earthdistance'
```

> **WAL serverName bump.** A freshly-initdb'd cluster must archive WAL to an **empty** path or `barman-cloud-check-wal-archive` fails ("Expected empty archive"). The manifest bumps the WAL archiver `serverName` to `immich-db-staging-cnpg-v1-v5` (the old `v4` still holds the DR'd cluster's WAL). **Bump this again (`v6`, …) on every future recreate** if the previous path is non-empty.

### 10.3 Last-30-days external library

Immich has **no** built-in date-window filter — an external library indexes *every* file under its mounted path. So the window is enforced at the filesystem: staging's external-library PV points at `photos-staging-30d/`, a directory kept equal to "the last ~30 days" by a daily CronJob.

**Sync mechanism — COPY, not symlinks, not hardlinks.** Research finding + rationale:

- **Symlinks are OUT.** Immich's docs explicitly say *"don't use symlinks in your import libraries,"* and multiple reports confirm symlinked files/directories are **not** followed or scanned (immich-app/immich #6311, #9335). A symlink farm would index nothing.
- **Hardlinks are OUT (here).** A hardlink cannot cross mount points (`ln` → `EXDEV`), so hardlinks would only work if the CronJob mounted the **real** photo tree read-**write** and linked within it — an unacceptable risk to irreplaceable originals. Rejected for safety.
- **COPY (chosen).** The CronJob (`cronjob-photos-30d.yaml`) mounts the full tree **read-only** (originals can't be harmed) and the slice read-write, and `cp -p`-copies files with `mtime -30`. Copied files are indistinguishable from real files to Immich; `cp -p` preserves mtime so the age-based prune stays consistent. Data duplication is bounded to ~30 days — fine for a small testbed. Robust regardless of NFS mount topology.

The CronJob (busybox, daily at 03:00) does: (1) `find /src -type f -mtime -30` → `cp -pu` into `/dst`; (2) `find /dst -type f -mtime +30 -delete` to prune; (3) remove empty dirs. `/src` = `immich-photos-src-pvc` (full tree, RO), `/dst` = `immich-photos-pvc` (the slice, RW — the same PVC Immich uses).

> Date basis is file **mtime**, not EXIF capture date. On the hestia SOT tree mtime tracks import and is a good proxy; if precise capture-date windowing is ever needed, switch the `find` to walk the `YYYY/MM` directory layout or read EXIF (not worth the extra tooling today).

**Operator prereqs on hestia (10.42.2.10):**

1. Create the slice directory on the `main` pool:
   ```bash
   sudo mkdir -p /mnt/main/family/images/photos-staging-30d
   ```
   It sits beside `photos/` under the existing `/mnt/main` NFS export, so **no new export is required** — confirm it is reachable at `10.42.2.10:/mnt/main/family/images/photos-staging-30d`.
2. Make it **writable by the CronJob's NFS identity.** The job runs as UID 0; if the export root-squashes, `chown` the dir to the mapped identity or open it up (derived data, low sensitivity):
   ```bash
   sudo chmod 0777 /mnt/main/family/images/photos-staging-30d   # simplest for a testbed
   ```
   Validate after the first run: `kubectl -n immich-stage create job --from=cronjob/immich-photos-30d-sync 30d-manual && kubectl -n immich-stage logs -f job/30d-manual` — expect a non-empty `du -sh` line and no `Permission denied`.

**Trigger a rescan after a sync.** Immich indexes the slice on its **scheduled external-library scan** (daily, admin default — set under Administration → Settings → External Library). The CronJob runs earlier (03:00) so fresh files are present when Immich scans. To force it immediately: click **Scan** on the library in the Immich UI, or `POST /api/libraries/{id}/scan` with an API key.

**Networking note.** The CronJob needs no cluster egress — NFS is mounted by the kubelet (host-side), not the pod network — and no CiliumNetworkPolicy selects it, so it runs in default-allow. Nothing to add.

### 10.4 Refresh cadence

- **Photo set — automatic, daily.** The 03:00 CronJob keeps the slice equal to the last 30 days and prunes older entries, so the dataset stays bounded with no operator action. Immich's daily library scan indexes the delta.
- **Database — manual recreate, recommended weekly / as-needed.** Do **not** automate a CNPG Cluster deletion (scripting a Cluster-CR delete is too risky). Run §10.2 by hand when you want a clean-slate DB (e.g. before rehearsing a migration, or if staging metadata drifts). Weekly is a reasonable default; there's no urgency since the photo set self-maintains.
- **Lighter refresh (optional).** Instead of a full recreate you can trim accumulated cruft with Immich's own maintenance jobs (Administration → Jobs): re-run the **Library** scan and **Storage Migration**, and use **asset/orphan cleanup** to drop entries whose files aged out of the slice. Prefer the documented recreate when you want a guaranteed-clean DB.

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
