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
- **Recovery**: an **initContainer** runs `litestream restore -if-replica-exists` before the app
  starts. ⚠️ This, not the sidecar's `-restore-if-db-not-exists` flag, is what recovers an empty
  volume — see §8.
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

- **Metrics**: the litestream sidecar serves Prometheus metrics on port `9090` (`metrics`), scraped
  every 60s by the `vitals-litestream` PodMonitor. The CNPG `PodMonitor` went with the database.
- **Backup alerts** (`infra/configs/alerts/prometheus-rules.yaml`, group `litestream`):

  | Alert | Fires when | Severity |
  | :--- | :--- | :--- |
  | `LitestreamNoRecentUpload` | **nothing** uploaded in 26h (the 24h snapshot should have) | critical |
  | `LitestreamReplicationStalled` | local writes in 30m, **zero** uploads in 30m | critical |
  | `LitestreamReplicationStalledDaily` | same comparison over 24h — covers a database written to only occasionally | critical |
  | `LitestreamMetricsAbsent` | any metric the rules depend on missing for 15m | critical |
  | `LitestreamDiskFull` | litestream reports a full volume | critical |
  | `LitestreamSyncErrors` | sustained sync error rate for 15m | warning |
  | `LitestreamReplicationStalledStaging` | the 24h rule, for staging | warning |

  Metrics behind them: `litestream_txid`, `litestream_replica_operation_total{operation}`,
  `litestream_sync_count`, `litestream_sync_error_count`, `litestream_disk_full`.

  ⚠️ **Litestream exposes no "last successful sync" timestamp** — checked against the live 0.5.17
  endpoint. Every rule is built from the above.

  ⚠️ **The known gap: a broken replica on a completely idle database is not detected until the next
  write.** An absolute "nothing uploaded in 26h" floor was written and then **rejected on
  measurement** — an idle replica does not upload at all (staging's PUT counter sat flat for two
  hours while perfectly healthy), so that rule pages for a working system. Both stalled rules
  therefore key off local writes, which ties detection to data actually being at risk rather than to
  elapsed time.

  ⚠️ **Severity is routing, not drama.** `warning` goes to `gjcourt+alerts@`, which is filtered out
  of the inbox; an off-site copy that stopped being written is data at risk, so those rules are
  `critical` and reach `gjcourt+critical@`. **Staging warnings route to the null receiver** — the
  staging rule is visible in Alertmanager and sends no mail, which is the right trade for a preview
  environment but means a broken staging replica is only found by looking.

## 8. Disaster Recovery

**Backup strategy: Litestream, as a sidecar in the vitals pod**, replicating `/data/vitals.db`
continuously to S3.

| | |
| :--- | :--- |
| Production | `s3://gjcourt-homelab-backup/production/vitals-sqlite` |
| Staging | `s3://gjcourt-homelab-backup/staging/vitals-sqlite` |
| Region | `us-east-2` |
| Sync interval | 10s |
| Snapshot / retention | every 24h, kept **30d production / 14d staging** — matching the CNPG scheme this replaced. Litestream's own defaults are 24h/24h, and retention deletes snapshots *and* their transaction files |
| On empty volume | `-restore-if-db-not-exists`: if the PVC comes back blank, the database is pulled from S3 before replication starts |
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

⚠️ **Rehearsed end to end on staging, 2026-09-19.** The procedure below is what was actually run,
not a plan. Seeded 1 user / 60 weights / 60 weight events / 120 water events, wiped the volume
completely (database, `-wal`, `-shm` and `.vitals.db-litestream`), and restored. **Row counts and
checksums came back identical.**

🔴 **What the rehearsal found: the pod does not self-heal from an empty volume unless the
initContainer does it.** On restart litestream logged:

```
level=INFO msg="database exists, skipping restore" path=/data/vitals.db
```

The app container had already created an empty `vitals.db`, so the sidecar's
`-restore-if-db-not-exists` never fired — and the empty database then replicated *forward* into S3
as new transactions. The older history survived, which is why the restore below worked, but
**automatic recovery is the initContainer's job; the flag loses the race every time.**

Litestream restores to a file, so the app must not be running against the target path:

```bash
# 1. Stop Flux FIRST, or it scales the Deployment back to 1 within 10 minutes
#    and restarts the pod on a half-restored database.
flux suspend kustomization apps-production -n flux-system

# 2. Stop the app (releases the RWO volume).
kubectl scale -n vitals-prod deploy/vitals --replicas=0
kubectl wait -n vitals-prod --for=delete pod -l app=vitals --timeout=120s

# 3. Restore in a scratch pod that mounts the same PVC and the credentials
#    secret, running the litestream image. It must:
#      - delete /data/vitals.db, -wal, -shm AND /data/.vitals.db-litestream
#        (stale WAL replays over the restored file; stale metadata resumes from
#        a transaction id that no longer matches)
#      - litestream restore -dry-run ... first, which prints the exact files it
#        will fetch and their timestamps
#      - then restore, choosing the target explicitly:
#          -txid <last good txid>     e.g. -txid 000000000000000d
#          -timestamp 2026-09-19T00:18:00Z
#        Both land on the boundaries of files that still exist; the dry run
#        lists them.
#
#    Find the last good txid with:
#      kubectl exec -n vitals-prod deploy/vitals -c litestream -- \
#        litestream ltx -config /etc/litestream/litestream.yml -level all /data/vitals.db

# 4. Scale up, verify, then resume Flux — ALWAYS, even if the restore failed.
kubectl scale -n vitals-prod deploy/vitals --replicas=1
flux resume kustomization apps-production -n flux-system
```

**Verify against something countable**, not just "the pod is up":

```bash
kubectl exec -n vitals-prod deploy/vitals -c litestream -- sqlite3 /data/vitals.db \
  "select 'users='||(select count(*) from users)||' weights='||(select count(*) from weights);"
```

**On the restart after a restore**, litestream logs `detected database behind replica` and fetches
the newest replica file. In the rehearsal this did **not** clobber the restored data — it
replicated the restored state forward as new transactions — but verify the counts after the pod
comes back, not only before.

**Rehearse this in staging**, which replicates to its own prefix for exactly that reason.

### Not yet covered

- **Restore is still unrehearsed.** The procedure above has not been executed end to end. Staging
  replicates to its own prefix precisely so it can be, and until it has been this section is a
  plan rather than a tested runbook.
- **The readinessProbe still proves only that the process is alive**, not that replication works —
  litestream retries S3 errors rather than exiting. That gap is now covered by the alerts above
  rather than by the probe.
- **Retention enforcement is only partly exercised.** `s3:DeleteObject` is confirmed working
  (read directly off the sidecar's metrics endpoint on 2026-09-18: `litestream_replica_operation_total{operation="DELETE"}` had incremented with `litestream_sync_error_count` at 0), but
  no snapshot has yet aged past the 30d/14d retention window, so the full enforcement path is
  unproven.

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
