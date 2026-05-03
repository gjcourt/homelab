# Post-Mortem: Immich Staging WAL Archive Failure & Disk Full (2026-02-20)

## Summary

The Immich staging PostgreSQL cluster (`immich-db-staging-cnpg-v1`) experienced a complete outage due to a full disk (`No space left on device`). The root cause was a continuous failure of the WAL (Write-Ahead Log) archiving process. The cluster was attempting to archive WALs to an S3 path (`.../v1`) that already contained WAL files from a previous, deleted cluster. The `barman-cloud-check-wal-archive` safety check prevented archiving to protect the existing backups, causing WAL files to accumulate locally until the 10Gi PersistentVolume was completely exhausted.

## Timeline

| Time | Event |
|---|---|
| ~Feb 15 | Immich staging cluster was recreated/rebuilt, generating a new PostgreSQL system ID (`7607225305312497692`). |
| ~Feb 15 - Feb 20 | The new cluster attempted to archive WALs to `s3://gjcourt-homelab-backup/staging/immich/v1`. |
| ~Feb 15 - Feb 20 | Archiving failed continuously because the `v1` path contained WALs from the old cluster (system ID `7590465042925559836`). |
| Feb 20 | Unarchived WAL segments (141+ files, ~16MB each) filled the 10Gi PVC to 100% capacity. |
| Feb 20 | The primary database pod crashed with `No space left on device` and entered a CrashLoopBackOff state. |
| Feb 20 | A failover was attempted, but the standby pod failed to recover because it tried to restore from the `v1` archive, which had the wrong system ID. |
| Feb 20 | Incident investigated and resolved by migrating the backup path to `v2` and expanding the PVC. |

## Root Cause

When a CloudNativePG (CNPG) cluster is recreated, it generates a new PostgreSQL system ID. If the new cluster is configured to use the same backup destination path as the old cluster, the `barman-cloud-wal-archive` tool performs a safety check (`barman-cloud-check-wal-archive`). This check ensures that the destination archive is either empty or belongs to the same system ID.

Because the `v1` path contained WALs from the old cluster, the safety check failed, and the archiving process was aborted. CNPG retains unarchived WAL files locally to ensure no data is lost before it is safely backed up. Over several days, these unarchived WAL files accumulated until they consumed all available space on the 10Gi PVC, causing the database to crash.

Additionally, the incorrect backup path confused the standby recovery process during a failover attempt, as the standby tried to fetch base backups and WALs from the `v1` path, which belonged to the old cluster.

## Impact

- **Complete outage** of the Immich staging environment.
- **Database crashloop** due to 100% disk utilization on the primary pod.
- **Failover failure** due to the standby pod being unable to restore from the incorrect backup archive.

## Recovery & Resolution

1. **PVC Expansion**: The `storage` request for the CNPG cluster was increased from `10Gi` to `20Gi` to provide immediate relief and allow the database to start up.
2. **Backup Path Migration**: The `destinationPath` in the `database.yaml` was updated from `s3://gjcourt-homelab-backup/staging/immich/v1` to `s3://gjcourt-homelab-backup/staging/immich/v2`.
3. **External Clusters Update**: The `externalClusters` configuration was also updated to point to the new `v2` path to ensure point-in-time recovery (PITR) works correctly for the new cluster.
4. **Archiving Resumed**: Once the configuration was applied, the cluster successfully initialized the new `v2` archive path and began offloading the accumulated WAL files, freeing up local disk space.

## Lessons Learned & Action Items

- **Always bump the backup path version** (e.g., `v1` -> `v2`) when recreating a CNPG cluster to avoid system ID conflicts and archiving failures.
- **Monitor PVC utilization** for database pods to catch WAL accumulation before it leads to a full disk outage.
- **Verify WAL archiving status** (`kubectl cnpg status <cluster-name>`) after any cluster recreation or backup configuration change.
