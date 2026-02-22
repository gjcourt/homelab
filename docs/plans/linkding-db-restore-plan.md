# Linkding Staging DB Restore Test Plan

This document outlines the plan to run a live test of destroying the Linkding staging database and restoring it from a backup.

## Prerequisites

1. **Verify Backups**: Ensure that there is a recent base backup and WAL archives available in the S3 bucket for the Linkding staging database.
   - Check the `Cluster` status for `ContinuousArchivingSuccess`.
   - Verify the S3 bucket `gjcourt-homelab-backup` under the path `staging/linkding/v1`.
2. **Access**: Ensure you have `kubectl` access to the cluster and the `linkding-stage` namespace.
3. **Downtime**: Acknowledge that the Linkding staging application will experience downtime during this test.

## Test Plan

### 1. Verify Current State

Before destroying anything, verify the current state of the application and database.

1. Access the Linkding staging application and create a test bookmark. Note the details of this bookmark.
2. Check the status of the CNPG cluster:
   ```bash
   kubectl get cluster -n linkding-stage linkding-db-staging-cnpg-v1
   ```
3. Verify that a base backup exists. You can check the S3 bucket directly or use the `cnpg` plugin if installed:
   ```bash
   kubectl cnpg status -n linkding-stage linkding-db-staging-cnpg-v1
   ```

### 2. Destroy the Database

Simulate a disaster by deleting the CNPG cluster and its associated Persistent Volume Claims (PVCs).

1. **Suspend Flux Reconciliation**: Prevent Flux from automatically recreating the cluster during the destruction phase.
   ```bash
   flux suspend kustomization apps-staging -n flux-system
   ```
2. **Delete the Cluster**:
   ```bash
   kubectl delete cluster -n linkding-stage linkding-db-staging-cnpg-v1
   ```
3. **Delete the PVCs**: Ensure all data is wiped.
   ```bash
   kubectl delete pvc -n linkding-stage -l app.kubernetes.io/instance=linkding-db-staging-cnpg-v1
   ```
4. **Verify Destruction**: Ensure the pods and PVCs are gone.
   ```bash
   kubectl get pods,pvc -n linkding-stage -l app.kubernetes.io/instance=linkding-db-staging-cnpg-v1
   ```

### 3. Restore from Backup

Modify the cluster configuration to bootstrap from the existing backup.

1. **Edit the Database Manifest**: Open `apps/staging/linkding/database.yaml`.
2. **Comment out `initdb`**:
   ```yaml
   # bootstrap:
   #   initdb:
   #     database: app
   #     owner: app
   #     secret:
   #       name: linkding-db-credentials
   ```
3. **Uncomment/Add `recovery`**:
   ```yaml
   bootstrap:
     recovery:
       source: linkding-db-backup
   ```
   *Note: The `externalClusters` section is already configured with the `linkding-db-backup` source pointing to the correct S3 path.*
4. **Apply the Changes**:
   ```bash
   kubectl apply -f apps/staging/linkding/database.yaml
   ```
   *Alternatively, you can commit the changes and resume Flux reconciliation.*

### 4. Monitor the Restoration

Monitor the progress of the restoration process.

1. **Watch the Pods**:
   ```bash
   kubectl get pods -n linkding-stage -w
   ```
   You should see the primary pod start in a recovery state, download the base backup, and apply WAL files.
2. **Check Cluster Status**:
   ```bash
   kubectl get cluster -n linkding-stage linkding-db-staging-cnpg-v1 -o yaml
   ```
   Look for the `phase` to transition to `Cluster in healthy state`.

### 5. Verify the Restoration

Once the cluster is healthy, verify that the data was restored successfully.

1. Access the Linkding staging application.
2. Verify that the test bookmark created in Step 1 is present.
3. Verify that other existing bookmarks are also present.

### 6. Cleanup and Revert

After a successful test, revert the configuration to its normal state.

1. **Edit the Database Manifest**: Open `apps/staging/linkding/database.yaml`.
2. **Revert Bootstrap Configuration**: Comment out `recovery` and uncomment `initdb`.
   ```yaml
   bootstrap:
     initdb:
       database: app
       owner: app
       secret:
         name: linkding-db-credentials
   #   recovery:
   #     source: linkding-db-backup
   ```
   *Note: CNPG ignores the `bootstrap` section after the cluster is initialized, but it's good practice to keep the manifest clean.*
3. **Commit and Push**: If you made changes via Git, commit and push the reverted manifest.
4. **Resume Flux Reconciliation**:
   ```bash
   flux resume kustomization apps-staging -n flux-system
   ```

## Troubleshooting

- **No Base Backup Found**: If the recovery fails because no base backup is found, ensure that a base backup was actually taken. You may need to trigger a manual backup before starting the test.
- **WAL Archive Errors**: If the recovery fails while applying WAL files, check the logs of the primary pod for specific errors related to downloading or applying WALs.
