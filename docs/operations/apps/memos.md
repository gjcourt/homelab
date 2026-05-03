# Memos

## 1. Overview
Memos is an open-source, self-hosted memo hub with knowledge management and social networking features. It serves as a lightweight note-taking application in the homelab.

## 2. Architecture
Memos is deployed as a Kubernetes `Deployment` with a single replica in the `memos-prod` (and `memos-stage`) namespace.
- **Database**: Uses a CloudNativePG (CNPG) PostgreSQL cluster (`memos-db-production-cnpg-v1`) with 3 instances for data storage.
- **Storage**: A 1Gi PersistentVolumeClaim (`memos-data-pvc`) is mounted at `/var/opt/memos` for local file storage (attachments, exports).
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://memos.stage.burntbytes.com
- **Production**: https://memos.burntbytes.com

## 4. Configuration
- **Environment Variables**:
  - `MEMOS_MODE`: Set to `prod`.
  - `MEMOS_PORT`: Set to `5230`.
  - `MEMOS_DRIVER`: Set to `postgres`.
  - `MEMOS_DB_HOST`, `MEMOS_DB_PORT`, `MEMOS_DB_USER`, `MEMOS_DB_NAME`: Database connection details.
- **ConfigMaps/Secrets**:
  - `memos-container-env` (ConfigMap): Contains the non-sensitive environment variables.
  - `memos-db-credentials` (Secret): Contains the PostgreSQL database credentials.
  - `memos-sso-secret` (Secret): Contains the OIDC client secret for Authelia integration (SOPS encrypted).
- **Init Container**: An init container (`init-dsn`) is used to construct the `MEMOS_DSN` connection string from the database credentials and pass it to the main container via a shared `emptyDir` volume.
- **SSO Integration**: Uses `hostAliases` to resolve `auth.burntbytes.com` to the Gateway API IP (`10.42.2.40`) from within the pod, allowing it to communicate with Authelia for OIDC authentication.

## 5. Usage Instructions
- Navigate to the Memos URL.
- Log in using your Authelia SSO credentials (if configured) or local admin account.
- Use the web interface to create, tag, and search memos.

## 6. Testing
To verify Memos is working:
1. Navigate to the Memos URL and ensure the login page loads.
2. Log in and create a test memo.
3. Verify the pod is running: `kubectl get pods -n memos-prod`
4. Verify the database cluster is healthy: `kubectl get cluster -n memos-prod`

## 7. Monitoring & Alerting
- **Metrics**: The CNPG PostgreSQL cluster exposes metrics via a `PodMonitor`.
- **Logs**: Check the pod logs for application errors:
  ```bash
  kubectl logs -n memos-prod deploy/memos
  ```

## 8. Disaster Recovery
- **Backup Strategy**:
  - The PostgreSQL database is backed up continuously to `s3://gjcourt-homelab-backup/production/memos` via the Barman Cloud Plugin (WAL archiving + daily base backups, gzip-compressed, 30-day retention).
  - The `memos-data-pvc` is backed up via Synology Snapshot Replication.
- **Restore Procedure**:
  1. Uncomment the `recovery` section in `apps/production/memos/database.yaml`.
  2. Comment out the `initdb` section.
  3. Apply the changes; CNPG will bootstrap a new cluster from the S3 backup via PITR.

## 9. Troubleshooting
- **Database Connection Errors**:
  - Verify the CNPG cluster is running and healthy.
  - Check the `init-dsn` init container logs to ensure the DSN was constructed correctly.
- **SSO Login Failing**:
  - Verify the `memos-sso-secret` contains the correct client secret.
  - Ensure the `hostAliases` patch is correctly resolving `auth.burntbytes.com` to the Gateway API IP.
  - Check the Memos pod logs for OIDC redirect URI mismatches or connection errors to Authelia.
