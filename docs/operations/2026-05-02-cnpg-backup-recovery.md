---
title: CNPG backup and disaster recovery
status: Stable
created: 2026-05-02
updated: 2026-05-02
updated_by: gjcourt
tags: [operations, cnpg, postgres, backup, recovery]
---

# CNPG Backup & Disaster Recovery Guide

> **Last tested:** 2026-04-18 — full wipe + S3 restore of `immich-stage` in ~5 minutes  
> **Cluster:** Talos Kubernetes single-node (`talos-ykb-uir`)  
> **Backup method:** CloudNativePG + barman-cloud plugin v0.11.0 → AWS S3

---

## Overview

All CNPG clusters use the **barman-cloud plugin** (not the legacy sidecar) for WAL archiving and base backups. The plugin runs as an init container (`plugin-barman-cloud`) in each Postgres pod.

### Databases protected

| App | Staging namespace | Production namespace | S3 path |
|-----|------------------|----------------------|---------|
| golinks | `golinks-stage` | `golinks-prod` | `s3://gjcourt-homelab-backup/{env}/golinks` |
| immich | `immich-stage` | `immich-prod` | `s3://gjcourt-homelab-backup/staging/immich/v3` |
| linkding | `linkding-stage` | `linkding-prod` | `s3://gjcourt-homelab-backup/{env}/linkding` |
| memos | `memos-stage` | `memos-prod` | `s3://gjcourt-homelab-backup/staging/memos/v2` |
| vitals | `vitals-stage` | `vitals-prod` | `s3://gjcourt-homelab-backup/{env}/vitals` |

**Backup schedule:** Daily at 02:00 UTC (via `ScheduledBackup` CRD)  
**Retention:** 14 days (staging), 30 days (production)  
**Continuous WAL archiving:** Enabled on all clusters

---

## Architecture

```
CNPG Cluster
  └─ postgres pod
       ├─ init: bootstrap-controller  (sets up /controller dir)
       ├─ init: plugin-barman-cloud   (WAL archiver + restore hooks)
       └─ container: postgres
  └─ ObjectStore CRD  →  S3 bucket
  └─ ScheduledBackup CRD  →  daily base backup trigger
```

Each cluster has three YAML files:
- `database.yaml` — `Cluster` CRD (instances, storage, plugin config, bootstrap)
- `objectstore.yaml` — `ObjectStore` CRD (S3 bucket, credentials, retention)
- `scheduledbackup.yaml` — `ScheduledBackup` CRD (daily trigger)

---

## Checking backup health

```bash
# Current backup status across all namespaces
kubectl get backup -A

# Latest backup for a specific cluster
kubectl get backup -n <namespace> --sort-by=.metadata.creationTimestamp | tail -5

# WAL archiving status (check plugin-barman-cloud container logs)
kubectl logs <primary-pod> -n <namespace> -c plugin-barman-cloud | grep -E "Archived|Error" | tail -20

# Full cluster health
kubectl cnpg status <cluster-name> -n <namespace>
```

**Healthy signs:**
- `kubectl get backup` shows recent `completed` entries
- Logs show `"Archived WAL file"` messages
- `kubectl cnpg status` shows `Continuous Backup status: Not configured` (expected with plugin-based backups — this field only reflects the built-in backup config, not plugin-based)

---

## Triggering a manual backup

```bash
kubectl cnpg backup <cluster-name> -n <namespace> \
  --method plugin \
  --plugin-name barman-cloud.cloudnative-pg.io
```

---

## Disaster Recovery Procedure

> ⚠️ **Critical:** Read the "serverName versioning" section below before starting a restore!

### When to use this

- All 3 DB pods are gone and PVCs are deleted (full node loss)
- PVC data is corrupt
- Accidental data deletion requiring point-in-time recovery

### Step 1: Scale down the application

```bash
kubectl scale deployment <app>-server --replicas=0 -n <namespace>
```

This prevents writes during recovery.

### Step 2: Delete the broken cluster (if it still exists)

```bash
kubectl delete cluster <cluster-name> -n <namespace>
# Wait for pods to terminate
kubectl wait --for=delete pod -l cnpg.io/cluster=<cluster-name> -n <namespace> --timeout=60s
# Delete any remaining PVCs
kubectl delete pvc -l cnpg.io/cluster=<cluster-name> -n <namespace>
```

> **Note:** CNPG auto-deletes PVCs when the Cluster is deleted (verified 2026-04-18).

### Step 3: Determine the correct serverName versions

This is the most critical step. The barman-cloud plugin runs `barman-cloud-check-wal-archive` on startup, which **fails if the WAL destination is not empty**. This means:

- The **WAL archiver** `serverName` (in `plugins[].parameters.serverName`) must point to a **new, never-used** path
- The **restore source** `serverName` (in `externalClusters[].plugin.parameters.serverName`) must point to the **last known-good** backup path

**How to determine versions:**

```bash
# What serverName is currently in Git (this is the last-known-good backup source)
grep serverName apps/<env>/<app>/database.yaml

# e.g. output: serverName: immich-db-staging-cnpg-v1-v4
# → restore source = v4
# → new WAL archiver serverName = v5
```

### Step 4: Create the recovery cluster manifest

```yaml
# Save as /tmp/<app>-recovery.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: <cluster-name>
  namespace: <namespace>
spec:
  # ... (copy all spec from database.yaml, then modify bootstrap and serverNames)
  
  plugins:
    - name: barman-cloud.cloudnative-pg.io
      isWALArchiver: true
      parameters:
        barmanObjectName: <objectstore-name>
        serverName: <cluster-name>-v<N+1>   # ← BUMP VERSION (new empty path)

  bootstrap:
    recovery:
      source: <cluster-name>-backup          # ← CHANGE from initdb to recovery

  externalClusters:
    - name: <cluster-name>-backup
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters:
          barmanObjectName: <objectstore-name>
          serverName: <cluster-name>-v<N>    # ← KEEP OLD VERSION (restore source)
```

### Step 5: Apply and monitor

```bash
kubectl apply -f /tmp/<app>-recovery.yaml

# Watch the recovery pod
kubectl get pods -n <namespace> -w
# You'll see: <cluster>-1-full-recovery-<hash> → Init → Running → Completed
# Then: <cluster>-1 (primary) → <cluster>-2-join → <cluster>-3-join

# Monitor status
kubectl cnpg status <cluster-name> -n <namespace>
```

**Expected timeline:**
- Recovery pod starts: ~10s
- Base backup restore from S3: ~1–3 min (depends on DB size)
- WAL replay to latest: ~30s
- Replicas clone + join: ~1–2 min each
- **Total: ~5 minutes for a small DB**

### Step 6: Verify the restore

```bash
# Check cluster is healthy
kubectl cnpg status <cluster-name> -n <namespace>
# Expected: "Cluster in healthy state", 3/3 Ready, all streaming

# Verify schema/data
kubectl exec -it <primary-pod> -n <namespace> -c postgres -- \
  psql -U postgres -d <dbname> -c "\dt"
```

### Step 7: Update Git and scale app back up

**Update `database.yaml`** — change both `serverName` fields to the new version (N+1):

```bash
cd ~/src/homelab
git checkout -b fix/<app>-recovery-vN
# Edit apps/<env>/<app>/database.yaml — bump serverName from vN to v(N+1)
# Update comment history in objectstore.yaml
git add apps/<env>/<app>/
git commit -m "fix(<app>): bump serverName to v(N+1) after disaster recovery"
gh pr create --title "fix(<app>): bump serverName post-recovery" --body "..."
# Merge to master — Flux will reconcile (cluster already running, CNPG will patch in-place)
```

**Scale app back up:**

```bash
kubectl scale deployment <app>-server --replicas=1 -n <namespace>
```

---

## The "Expected empty archive" Problem

### What it is

`barman-cloud-check-wal-archive` is a safety check that runs when a new CNPG instance starts. It verifies the WAL destination is empty to prevent two clusters from writing to the same WAL path (which would corrupt the archive).

**During normal bootstrap (`initdb`):** The path is fresh, check passes.  
**During disaster recovery:** The path already has WAL from the cluster you're recovering — the check fails!

### The fix: serverName versioning

The `serverName` parameter in the plugin config determines the WAL subdirectory path within the S3 bucket. By incrementing it (e.g. `-v3` → `-v4`), you get a fresh empty path for the archiver while still reading the backup from the old path.

```
S3 bucket layout:
s3://gjcourt-homelab-backup/staging/immich/v3/
  ├── immich-db-staging-cnpg-v1-v3/    ← OLD: base backups + WAL (restore source)
  │     ├── base/
  │     └── wals/
  └── immich-db-staging-cnpg-v1-v4/    ← NEW: empty, fresh WAL archiving path
```

### Keeping track of versions

Each time you perform a recovery, increment the serverName suffix. Document this in the `database.yaml` and `objectstore.yaml` comments. Current versions as of 2026-04-18:

| Cluster | Current serverName |
|---------|-------------------|
| immich-stage | `immich-db-staging-cnpg-v1-v4` |
| immich-prod | `immich-db-prod-cnpg-v3` (v1 of the current cluster) |
| memos-stage | `memos-db-staging-cnpg-v1` |
| memos-prod | `memos-db-production-cnpg-v1` |
| vitals-stage | `vitals-db-staging-cnpg-v1` |
| vitals-prod | `vitals-db-production-cnpg-v1` |
| linkding-stage | `linkding-db-staging-cnpg-v1` |
| linkding-prod | `linkding-db-production-cnpg-v1` |
| golinks-stage | `golinks-db-staging-cnpg-v1` |
| golinks-prod | `golinks-db-production-cnpg-v1` |

---

## Audit findings (2026-04-18)

### ✅ What's working well

- All 10 clusters (5 apps × 2 envs) have `ObjectStore` + `ScheduledBackup` configured
- Daily base backups completing successfully since mid-March
- WAL archiving active on all clusters (verified via plugin-barman-cloud logs)
- Retention policies enforced (14d staging, 30d production)
- Backup data successfully restored in ~5 minutes (tested on immich-stage)

### ⚠️ Known issues / gotchas

1. **`barman-cloud-check-wal-archive` blocks restore** — See "Expected empty archive" section above. Always increment serverName during recovery.

2. **Immich-stage backup failures (exit 4)** — Three failed backups (2026-03-23, 2026-04-15) were caused by disk-full conditions. The PVC was expanded from 10Gi → 20Gi on 2026-04-17. Future failures of this type should be investigated for disk pressure.

3. **Early failures (March 6–9)** — Old failed backup objects exist in all namespaces from the barman plugin's initial deployment period. These are stale and not actionable; retention will clean them up eventually.

4. **`Continuous Backup status: Not configured`** — This is expected and not a bug. The `status.continuousArchiving` field in `kubectl cnpg status` only reflects the built-in CNPG backup configuration. Plugin-based WAL archiving (our setup) is not reflected there. Use `kubectl logs -c plugin-barman-cloud` to verify WAL archiving health.

### 🔲 Robustness improvements to consider

- [ ] **AlertManager rules** for backup failures — currently no alerting when a scheduled backup fails
- [ ] **Regular restore verification** — schedule a monthly automated restore test
- [ ] **Document PITR procedure** — point-in-time recovery steps not yet documented (recovery to a specific LSN/timestamp using `bootstrap.recovery.recoveryTarget`)

---

## Quick Reference

```bash
# Health check
kubectl get backup -A | grep -v completed     # Show non-healthy backups
kubectl cnpg status <cluster> -n <ns>          # Full cluster status

# Manual backup
kubectl cnpg backup <cluster> -n <ns> \
  --method plugin --plugin-name barman-cloud.cloudnative-pg.io

# WAL archiving check
kubectl logs <primary-pod> -n <ns> -c plugin-barman-cloud \
  | grep -E "Archived|Error" | tail -10

# Recovery (see full procedure above)
# 1. Scale down app
# 2. Delete cluster + PVCs
# 3. Apply recovery manifest (serverName N → restore from N, archive to N+1)
# 4. Wait ~5 min, verify
# 5. Update Git, scale app back up
```
