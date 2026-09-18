# Vitals

## 1. Overview

Vitals is a custom health and wellness tracking application for the homelab. It allows users to log and monitor various health metrics over time.

## 2. Architecture

Vitals is deployed as a Kubernetes `Deployment` with a single replica in the `vitals-prod` (and `vitals-stage`) namespace.

- **Image**: Uses a custom image hosted on GitHub Container Registry (`ghcr.io/gjcourt/vitals`).
- **Database**: **SQLite**, in a single file on a PersistentVolume, replicated to S3 by a Litestream sidecar (see §8). The CNPG PostgreSQL cluster
  (`vitals-db-production-cnpg-v1`) was retired in
  [#1430](https://github.com/gjcourt/homelab/pull/1430) — it had held zero user tables for seven
  months while occupying three replicas and three iSCSI PVCs.
- **Storage**: `vitals-data` PVC, 1Gi, `truenas-iscsi`, **ReadWriteOnce**, mounted at `/data`. The
  database file is `/data/vitals.db`.
- **Rollout strategy**: `strategy: Recreate`, and it is load-bearing. The default `RollingUpdate`
  starts the replacement pod before terminating the old one, and on an RWO volume the new pod cannot
  attach what the old one still holds — the same failure this repo documents in
  [`AGENTS.md`](../../../AGENTS.md) under *Deployments on RWO*.
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`). Egress is DNS plus HTTPS to the
  S3 backup bucket (`*.s3.us-east-2.amazonaws.com`) for the Litestream sidecar — nothing else.

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
  - `vitals-aws-creds-secret` (Secret, SOPS): S3 credentials for the Litestream sidecar, exposed to
    it as `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY`.
  - `vitals-litestream` (ConfigMap): the replication config; the replica URL differs per overlay.
  - There are **no database credentials** — a local SQLite file needs none.

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
- **Logs**: the pod has two containers, so name the one you want:

  ```bash
  kubectl logs -n vitals-prod deploy/vitals -c vitals
  kubectl logs -n vitals-prod deploy/vitals -c litestream
  ```

## 8. Disaster Recovery

**Backup strategy: Litestream, as a sidecar in the vitals pod**, replicating `/data/vitals.db`
continuously to S3.

| | |
| :--- | :--- |
| Production | `s3://gjcourt-homelab-backup/production/vitals-sqlite` |
| Staging | `s3://gjcourt-homelab-backup/staging/vitals-sqlite` |
| Region | `us-east-2` |
| Sync interval | 10s |
| Credentials | `vitals-aws-creds-secret` (SOPS), the same keys the retired Barman ObjectStore used |
| Config | `vitals-litestream` ConfigMap, one per overlay |

It runs in the app's pod on purpose: the volume is ReadWriteOnce, so a separate CronJob pod could
not mount it while vitals holds it.

⚠️ **The sidecar shares the pod's fate, and the pod shares the sidecar's.** If Litestream
crashloops — revoked credentials, bucket unreachable — the pod stops being Ready and the app goes
down with it. That trade was made knowingly; see the comment in `apps/base/vitals/deployment.yaml`.

⚠️ **The old PostgreSQL backups under `s3://gjcourt-homelab-backup/{production,staging}/vitals`
(no `-sqlite` suffix) are frozen at the retirement date and hold no user tables.** They are not a
restore path. That is why the SQLite replica uses a separate prefix.

### Verify replication is actually happening

```bash
kubectl logs -n vitals-prod deploy/vitals -c litestream --tail=20
kubectl exec -n vitals-prod deploy/vitals -c litestream -- \
  litestream ltx -config /etc/litestream/litestream.yml -level all /data/vitals.db
```

`-config` is required — the config lives at `/etc/litestream/litestream.yml`, not the default
`/etc/litestream.yml`, and without it the command exits with `config file not found`.

The second command lists the transaction files in S3 with their timestamps — an empty list, or a
newest entry hours old on a database being written to, means replication is broken.

### Restore

Litestream restores to a file, so the app must not be running against the target path:

```bash
# 1. Stop the app (releases the RWO volume).
kubectl scale -n vitals-prod deploy/vitals --replicas=0

# 2. Restore into a scratch pod that mounts the same PVC, or restore locally and copy back.
#    Point-in-time is supported: add -timestamp 2026-09-17T22:00:00Z
litestream restore -o ./vitals.db s3://gjcourt-homelab-backup/production/vitals-sqlite

# 3. Put the file back at /data/vitals.db, then scale up.
kubectl scale -n vitals-prod deploy/vitals --replicas=1
```

`litestream restore -dry-run` prints the plan without writing anything, and `-timestamp` /
`-txid` land on the boundaries of transaction files that still exist.

**Rehearse this in staging**, which replicates to its own prefix for exactly that reason.

### Not yet covered

- **No alert on replication staleness.** Litestream can expose Prometheus metrics via `addr`, but
  that is not enabled here and no rule watches it, so a silently broken replica would not page.
  Follow-up: enable metrics, scrape them, and alert on the age of the newest transaction.

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
