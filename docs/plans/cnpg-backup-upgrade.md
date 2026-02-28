---
status: complete
last_modified: 2026-02-27
---

# CNPG Backup Upgrade Plan

This document outlines the plan to upgrade the CloudNativePG (CNPG) backup configuration from the deprecated in-tree `barmanObjectStore` to the new Barman Cloud Plugin architecture.

## Background

CloudNativePG version 1.26 deprecated the in-tree support for Barman Cloud backups (`.spec.backup.barmanObjectStore`). The new approach uses the Barman Cloud Plugin, which provides improved error handling, status reporting, and a more modular architecture.

## Prerequisites

1. **CloudNativePG Version**: Ensure the cluster is running CNPG version 1.26 or later.
   - *Status*: Verified. The cluster is running `ghcr.io/cloudnative-pg/cloudnative-pg:1.26.1`.
2. **cert-manager**: Ensure `cert-manager` is installed and running.
   - *Status*: Verified. `cert-manager` pods are running in the `security` namespace.

## Upgrade Steps

The migration process involves the following steps for each cluster that currently uses the in-tree backup configuration:

### 1. Install the Barman Cloud Plugin

Install the plugin in the same namespace as the CloudNativePG operator (`cnpg-system`).

```bash
kubectl apply -f https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/v0.11.0/manifest.yaml
```

Verify the installation:

```bash
kubectl rollout status deployment -n cnpg-system barman-cloud
```

### 2. Define the `ObjectStore` Resource

For each cluster, create an `ObjectStore` resource that maps the existing `.spec.backup.barmanObjectStore` configuration.

**Example (Immich Staging):**

```yaml
apiVersion: barmancloud.cnpg.io/v1
kind: ObjectStore
metadata:
  name: immich-db-staging-cnpg-v1-backup
  namespace: immich-stage
spec:
  configuration:
    destinationPath: s3://gjcourt-homelab-backup/staging/immich/v2
    s3Credentials:
      accessKeyId:
        name: immich-aws-creds-secret
        key: ACCESS_KEY_ID
      secretAccessKey:
        name: immich-aws-creds-secret
        key: ACCESS_SECRET_KEY
    wal:
      compression: gzip
    data:
      compression: gzip
  retentionPolicy: 14d
```

*Note: The `retentionPolicy` is moved from the `Cluster` resource to the `ObjectStore` resource.*

### 3. Update the `Cluster` Resource

Modify the `Cluster` resource to use the plugin for WAL archiving. This must be done in a single atomic change.

1. Remove the `.spec.backup.barmanObjectStore` section.
2. Remove `.spec.backup.retentionPolicy`.
3. Add the plugin configuration to the `plugins` list.

**Example (Immich Staging):**

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: immich-db-staging-cnpg-v1
  namespace: immich-stage
spec:
  # ... other configuration ...
  plugins:
    - name: barman-cloud.cloudnative-pg.io
      isWALArchiver: true
      parameters:
        barmanObjectName: immich-db-staging-cnpg-v1-backup
        serverName: immich-db-staging-cnpg-v1
```

### 4. Update `externalClusters` Configuration (If Applicable)

If the cluster uses `externalClusters` for bootstrapping or recovery, update the configuration to use the plugin.

1. Create an `ObjectStore` resource for the external cluster (if it doesn't already exist).
2. Update the `externalClusters` section in the `Cluster` resource.

**Example (Immich Staging):**

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: immich-db-staging-cnpg-v1
  namespace: immich-stage
spec:
  # ... other configuration ...
  externalClusters:
    - name: immich-db-backup
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters:
          barmanObjectName: immich-db-staging-cnpg-v1-backup
          serverName: immich-db-staging-cnpg-v1
```

### 5. Update `ScheduledBackup` Resources (If Applicable)

If there are any `ScheduledBackup` resources, update them to use the plugin method.

**Example:**

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: immich-db-staging-cnpg-v1-backup
  namespace: immich-stage
spec:
  cluster:
    name: immich-db-staging-cnpg-v1
  schedule: '0 0 0 * * *'
  backupOwnerReference: self
  method: plugin
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
```

## Rollout Plan

1. **Install Plugin**: Install the Barman Cloud Plugin in the `cnpg-system` namespace.
2. **Staging Migration**:
   - Migrate `immich-db-staging-cnpg-v1`
   - Migrate `linkding-db-staging-cnpg-v1`
   - Migrate `memos-db-staging-cnpg-v1`
3. **Production Migration**:
   - Migrate `immich-db-prod-cnpg-v2`
   - Migrate `linkding-db-production-cnpg-v1`
   - Migrate `memos-db-production-cnpg-v1`
4. **Verification**: Monitor the new metrics (`barman_cloud_cloudnative_pg_io_*`) to ensure backups are completing successfully.
