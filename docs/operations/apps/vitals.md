# Vitals

## 1. Overview

Vitals is a custom health and wellness tracking application for the homelab. It allows users to log and monitor various health metrics over time.

## 2. Architecture

Vitals is deployed as a Kubernetes `Deployment` with a single replica in the `vitals-prod` (and `vitals-stage`) namespace.

- **Image**: Uses a custom image hosted on GitHub Container Registry (`ghcr.io/gjcourt/vitals`).
- **Database**: **SQLite**, in a single file on a PersistentVolume. The CNPG PostgreSQL cluster
  (`vitals-db-production-cnpg-v1`) was retired in
  [#1430](https://github.com/gjcourt/homelab/pull/1430) — it had held zero user tables for seven
  months while occupying three replicas and three iSCSI PVCs.
- **Storage**: `vitals-data` PVC, 1Gi, `truenas-iscsi`, **ReadWriteOnce**, mounted at `/data`. The
  database file is `/data/vitals.db`.
- **Rollout strategy**: `strategy: Recreate`, and it is load-bearing. The default `RollingUpdate`
  starts the replacement pod before terminating the old one, and on an RWO volume the new pod cannot
  attach what the old one still holds — the same failure this repo documents in
  [`AGENTS.md`](../../../AGENTS.md) under *Deployments on RWO*.
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`). Egress is DNS-only.

## 3. URLs

- **Staging**: https://vitals.stage.burntbytes.com
- **Production**: https://vitals.burntbytes.com

## 4. Configuration

- **Environment Variables**:
  - `SQLITE_PATH`: `/data/vitals.db`, set directly in the deployment manifest.
  - `ADDR`, `WEB_DIR`: provided via the `vitals-container-env` ConfigMap.
- **ConfigMaps/Secrets**:
  - `vitals-container-env` (ConfigMap): listen address and web asset directory.
  - `ghcr-secret` (Secret): used as an `imagePullSecret` to pull the custom image from GHCR.
  - There are **no database credentials** — a local file needs none.

## 5. Usage Instructions

- Navigate to the Vitals URL.
- Use the web interface to log new health metrics or view historical data.

## 6. Testing

To verify Vitals is working:

1. Navigate to the Vitals URL and ensure the UI loads.
2. Verify the `/api/health` endpoint returns a successful response. ⚠️ **Treat this as a liveness
   signal only.** It answered 200 continuously while the PostgreSQL database held zero tables, and
   whether it exercises storage at all has not been confirmed — so a 200 is not evidence that the
   database file is present and writable. Use step 4 for that.
3. Verify the pod is running: `kubectl get pods -n vitals-prod`
4. Verify the volume is bound and mounted:

   ```bash
   kubectl get pvc -n vitals-prod vitals-data
   kubectl exec -n vitals-prod deploy/vitals -- ls -l /data
   ```

5. Confirm the storage mode in the logs — the app prints `Using SQLite database` at startup.

## 7. Monitoring & Alerting

- **Metrics**: none app-specific. The CNPG `PodMonitor` went with the database.
- **Volume**: `pvc-writeprobe` discovers PVC-mounting pods automatically, so `vitals-data` is
  covered by `PvcNotWritable` with no configuration.
- **Logs**: Check the pod logs for application errors:

  ```bash
  kubectl logs -n vitals-prod deploy/vitals
  ```

## 8. Disaster Recovery

⚠️ **There is no automated backup today. This is a known regression from the CNPG setup**, which had
WAL archiving and daily base backups to S3. A SQLite file has neither. That is tolerable only while
the database is empty or trivially reproducible — **it stops being tolerable after the first logged
measurement.** Tracked as a follow-up: Litestream, or a `VACUUM INTO` cron to object storage.

- **Manual copy** (do this before any risky change):

  ```bash
  kubectl exec -n vitals-prod deploy/vitals -- \
    sh -c 'sqlite3 /data/vitals.db ".backup /data/vitals-backup.db"' 2>/dev/null \
    || kubectl cp vitals-prod/$(kubectl get pod -n vitals-prod -l app=vitals \
         -o jsonpath='{.items[0].metadata.name}'):/data/vitals.db ./vitals.db
  ```

  The image may not ship `sqlite3`; the `kubectl cp` fallback copies the file itself. Copying a live
  SQLite file is only safe when the app is idle — for a clean copy, scale to 0 first.

- **Restore**: scale the Deployment to 0, copy a known-good file back to `/data/vitals.db`, scale
  back to 1. The old PostgreSQL backups under `s3://gjcourt-homelab-backup/{production,staging}/vitals`
  are **frozen at the retirement date** and contain no user tables; they are not a restore path.

## 9. Troubleshooting

- **Pod stuck `ContainerCreating` / `Multi-Attach error`**: the RWO volume is still attached to the
  previous pod. Confirm `strategy` is `Recreate` (`kubectl get deploy -n vitals-prod vitals -o
  jsonpath='{.spec.strategy}'`) and delete the old pod — see *Deployments on RWO* in `AGENTS.md`.
- **Flux reports `spec.strategy.rollingUpdate: Forbidden`**: the live Deployment still carries
  API-server-defaulted `rollingUpdate` fields that server-side apply merges into `type: Recreate`.
  Neither `rollingUpdate: null` nor `rollingUpdate: {}` in the manifest clears them. Replace the
  whole field once, out of band:

  ```bash
  kubectl -n vitals-prod patch deploy vitals --type=json \
    -p '[{"op":"replace","path":"/spec/strategy","value":{"type":"Recreate"}}]'
  ```

  Only needed on a Deployment that predates the `Recreate` manifest.
- **Database errors in the logs**: check that `/data` is mounted and writable. `fsGroup: 65534`
  matches `runAsUser`, and `readOnlyRootFilesystem: true` is fine because `/data` is an explicit
  writable mount.
- **Image Pull Errors**: Verify the `ghcr-secret` is valid and has permissions to pull the image.
