# Linkding

## 1. Overview
Linkding is a simple bookmark manager that you can host yourself. It's designed to be minimal, fast, and easy to set up using Docker. In this homelab, it serves as the primary bookmarking tool.

## 2. Architecture
Linkding is deployed as a Kubernetes `Deployment` with a single replica in the `linkding-prod` (and `linkding-stage`) namespace.
- **Database**: Uses a CloudNativePG (CNPG) PostgreSQL cluster (`linkding-db-production-cnpg-v1`) for data storage.
- **Storage**: Uses a PersistentVolumeClaim (`linkding-data-pvc`) for storing favicons and other local data.
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://links.stage.burntbytes.com
- **Production**: https://links.burntbytes.com

## 4. Configuration
- **Environment Variables**:
  - Database connection details are provided via the `linkding-container-env` ConfigMap and `linkding-db-credentials` Secret.
  - OIDC configuration is provided via the `linkding-oidc-config` ConfigMap.
- **ConfigMaps/Secrets**:
  - `linkding-app-secret` (Secret): Contains the `LD_SUPERUSER_PASSWORD` (SOPS encrypted).
  - `linkding-oidc-secret` (Secret): Contains the OIDC client secret for Authelia integration (SOPS encrypted).
- **SSO Integration**: Uses `hostAliases` to resolve `auth.burntbytes.com` to the Gateway API IP (`192.168.5.33`) from within the pod, allowing it to communicate with Authelia for OIDC authentication.

## 5. Usage Instructions
- Navigate to the Linkding URL.
- Log in using your Authelia SSO credentials (if configured) or the local superuser account.
- Use the web interface or browser extensions to add and manage bookmarks.

## 6. Testing
To verify Linkding is working:
1. Navigate to the Linkding URL and ensure the login page loads.
2. Log in and create a test bookmark.
3. Verify the pod is running: `kubectl get pods -n linkding-prod`
4. Verify the database cluster is healthy: `kubectl get cluster -n linkding-prod`

## 7. Monitoring & Alerting
- **Metrics**: The CNPG PostgreSQL cluster exposes metrics via a `PodMonitor`.
- **Logs**: Check the pod logs for application errors:
  ```bash
  kubectl logs -n linkding-prod deploy/linkding
  ```

## 8. Disaster Recovery
- **Backup Strategy**:
  - The PostgreSQL database is backed up to S3 (MinIO/AWS) using CNPG's Barman integration (Note: currently pending S3 credentials configuration).
  - The `linkding-data-pvc` is backed up via Synology Snapshot Replication.
- **Restore Procedure**:
  1. Uncomment the `recovery` section in the `database.yaml` CNPG `Cluster` definition.
  2. Comment out the `initdb` section.
  3. Apply the changes to bootstrap a new cluster from the backup.
  4. Restore the `linkding-data-pvc` LUN via Synology DSM if necessary.

## 9. Troubleshooting
- **Database Connection Errors**:
  - Verify the CNPG cluster is running and healthy.
  - Check the Linkding pod logs for database connection errors.
- **SSO Login Failing**:
  - Verify the `linkding-oidc-secret` contains the correct client secret.
  - Ensure the `hostAliases` patch is correctly resolving `auth.burntbytes.com` to the Gateway API IP.
  - Check the Linkding pod logs for OIDC redirect URI mismatches or connection errors to Authelia.
