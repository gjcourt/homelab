# Runbook: Shrink CNPG Cluster PVC Storage

CNPG (CloudNativePG) **cannot shrink PVCs in-place**. To reduce the storage
allocation for a PostgreSQL cluster you must:

1. Take a base backup via Barman to object storage.
2. Delete the existing cluster (which deletes its PVCs and old iSCSI LUNs).
3. Recreate the cluster with the smaller `storage.size`, bootstrapping from the
   Barman backup.

This runbook covers both the Barman-backed path (immich, memos, linkding) and
the pg_dump fallback path (apps that do not yet have a Barman backup).

---

## Prerequisites

- `kubectl` configured with cluster access.
- The Barman Cloud Plugin is installed: `kubectl get deployment -n cnpg-system barman-cloud`
- For the Barman path: the ObjectStore resource is healthy and a recent base
  backup exists (check `kubectl get backup -n <ns>`).
- For the pg_dump path: direct access to the running primary pod.
- The desired new `storage.size` is already set in git (done) — it becomes
  effective only after cluster recreation.

---

## Part A — Barman backup/restore path

Use this path for any cluster that has a `ScheduledBackup` resource and a
working ObjectStore (immich, memos-staging, linkding-staging, etc.).

### 1. Verify a healthy base backup exists

```bash
APP=immich
NS=immich-prod   # or immich-stage, memos-stage, etc.
CLUSTER=immich-db-prod-cnpg-v2

kubectl get backup -n $NS
# Look for at least one backup with status "completed"
```

If no completed backup exists yet, trigger one manually:

```bash
kubectl cnpg backup $CLUSTER -n $NS
# Wait for it to complete
kubectl get backup -n $NS -w
```

### 2. Scale down the application

Prevent new writes while the backup is finalising.

```bash
# Replace with the actual Deployment / StatefulSet name
kubectl scale deployment immich-server -n $NS --replicas=0
kubectl scale deployment immich-microservices -n $NS --replicas=0
# ... and any other app pods that write to the database
```

Wait for pods to terminate:

```bash
kubectl get pods -n $NS -w
```

### 3. Take a final on-demand backup

Ensures the backup reflects the latest data state.

```bash
kubectl cnpg backup $CLUSTER -n $NS --name "${CLUSTER}-pre-shrink"
# Wait for completion
kubectl get backup -n $NS -w
```

### 4. Note the backup name / serverName

```bash
kubectl get backup -n $NS "${CLUSTER}-pre-shrink" -o jsonpath='{.status.backupId}'
```

Record the `serverName` used in the ObjectStore — you will need it for the
`externalClusters.plugin.parameters.serverName` field (it was set when the
cluster was first created, typically equal to the cluster name).

### 5. Delete the existing cluster

This is **irreversible for PVC data** — the backup in object storage is the
only recovery point.

```bash
kubectl delete cluster $CLUSTER -n $NS
# Watch PVCs disappear
kubectl get pvc -n $NS -w
```

Flux will immediately try to reconcile and recreate the cluster. Suspend Flux
temporarily to prevent it from recreating before you swap to the recovery
bootstrap:

```bash
flux suspend kustomization apps-production  # or apps-staging
```

### 6. Update the Cluster resource to bootstrap from backup

In the app's `database.yaml`, comment out the `initdb` section and uncomment
the `recovery` section:

```yaml
bootstrap:
  # initdb section commented out during recovery
  # initdb:
  #   database: ...
  recovery:
    source: <cluster>-db-backup
    # Optional: restore to a specific point in time
    # recoveryTarget:
    #   targetTime: "2026-02-27T00:00:00Z"
```

The `externalClusters` entry (already present, using the plugin) provides the
restore source. The `serverName` in the plugin parameters **must match the
serverName the original cluster used when it wrote WALs to the object store**.

Commit and push the change. Then resume Flux:

```bash
flux resume kustomization apps-production
```

### 7. Monitor cluster recreation

```bash
kubectl get cluster $CLUSTER -n $NS -w
# Expect: Cluster in healthy state
```

Watch the pods come up:

```bash
kubectl get pods -n $NS -w
```

### 8. Verify data

```bash
kubectl cnpg psql $CLUSTER -n $NS -- -c "\dt"
# Spot-check a few rows
```

### 9. Re-enable the application

```bash
kubectl scale deployment immich-server -n $NS --replicas=1
# etc.
```

### 10. Revert the bootstrap section in git

Switch `database.yaml` back to `initdb` bootstrap (or keep `recovery` — either
is fine for a running cluster; `recovery` is ignored after initial bootstrap).

The convention in this repo is to keep `initdb` in git and use recovery only
during the shrink procedure.

---

## Part B — pg_dump fallback path

Use this for clusters without a working Barman backup (golinks, vitals, or any
cluster where the ObjectStore is not yet healthy).

### 1. Dump the database

```bash
CLUSTER=golinks-db-production-cnpg-v1
NS=golinks-prod

# Get the primary pod name
PRIMARY=$(kubectl get cluster $CLUSTER -n $NS \
  -o jsonpath='{.status.currentPrimary}')

# Stream a plain-SQL dump to a local file
kubectl exec -n $NS $PRIMARY -- \
  pg_dump -U app app > /tmp/${CLUSTER}-dump.sql

wc -l /tmp/${CLUSTER}-dump.sql   # sanity check, must be > 0
```

### 2. Scale down the application

```bash
kubectl scale deployment golinks -n $NS --replicas=0
kubectl get pods -n $NS -w
```

### 3. Take a final dump

```bash
kubectl exec -n $NS $PRIMARY -- \
  pg_dump -U app app > /tmp/${CLUSTER}-dump-final.sql
```

### 4. Delete the existing cluster

```bash
flux suspend kustomization apps-production
kubectl delete cluster $CLUSTER -n $NS
kubectl get pvc -n $NS -w   # confirm PVCs deleted
```

### 5. Recreate the cluster (initdb bootstrap)

The `storage.size` in git is already set to the reduced size. Resume Flux:

```bash
flux resume kustomization apps-production
kubectl get cluster $CLUSTER -n $NS -w
```

Wait for `Cluster in healthy state`.

### 6. Restore the dump

```bash
PRIMARY=$(kubectl get cluster $CLUSTER -n $NS \
  -o jsonpath='{.status.currentPrimary}')

cat /tmp/${CLUSTER}-dump-final.sql | kubectl exec -i -n $NS $PRIMARY -- \
  psql -U app app
```

### 7. Verify and re-enable the application

```bash
kubectl cnpg psql $CLUSTER -n $NS -- -c "SELECT COUNT(*) FROM <main_table>;"
kubectl scale deployment golinks -n $NS --replicas=1
```

---

## PVC size reference

### CNPG clusters (updated target sizes)

| Cluster | Namespace | New Size | Old Size | Actual Usage |
|---|---|---|---|---|
| immich-db-prod-cnpg-v2 | immich-prod | 2Gi | 20Gi | ~994Mi |
| immich-db-staging-cnpg-v1 | immich-stage | 10Gi | 20Gi | ~7.2Gi |
| memos-db-production-cnpg-v1 | memos-prod | 1Gi | 10Gi | ~633Mi |
| memos-db-staging-cnpg-v1 | memos-stage | 1Gi | 10Gi | ~634Mi |
| linkding-db-production-cnpg-v1 | linkding-prod | 1Gi | 10Gi | ~602Mi |
| linkding-db-staging-cnpg-v1 | linkding-stage | 2Gi | 10Gi | — |
| golinks-db-production-cnpg-v1 | golinks-prod | 1Gi | 10Gi | ~488Mi |
| golinks-db-staging-cnpg-v1 | golinks-stage | 2Gi | — | — |
| vitals-db-production-cnpg-v1 | vitals-prod | 512Mi | 10Gi | ~178Mi |
| vitals-db-staging-cnpg-v1 | vitals-stage | 2Gi | — | — |

### Non-CNPG PVCs (file-based)

For non-database PVCs (homeassistant-config, jellyfin-config, audiobookshelf)
use a similar procedure but replace the pg_dump step with a `tar` or `rsync`
of the volume contents:

```bash
# Dump
kubectl exec -n $NS $POD -- tar czf - /config > /tmp/config-backup.tar.gz

# Delete PVC (after deleting the owning Pod/Deployment)
kubectl delete pvc <pvc-name> -n $NS

# Let Flux recreate at new size, then restore
cat /tmp/config-backup.tar.gz | kubectl exec -i -n $NS $NEW_POD -- \
  tar xzf - -C /
```

---

## Rollback

If anything goes wrong during cluster deletion, the backup in S3 is your
recovery point. Do **not** delete the ObjectStore or the S3 prefix until the
new cluster is healthy and you have verified the data.

To restore to a specific point in time (PITR):

```yaml
bootstrap:
  recovery:
    source: <cluster>-db-backup
    recoveryTarget:
      targetTime: "2026-02-27T02:00:00Z"
```
