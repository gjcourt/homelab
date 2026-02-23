# Audiobookshelf

## 1. Overview
Audiobookshelf is a self-hosted audiobook and podcast server. In this homelab, it serves as the primary platform for managing and streaming audiobooks and podcasts, featuring a web interface and mobile apps.

## 2. Architecture
Audiobookshelf is deployed as a standard Kubernetes `Deployment` with a single replica in the `audiobookshelf-prod` (and `audiobookshelf-stage`) namespace.
- **Storage**:
  - **Config**: Uses a PersistentVolumeClaim (`audiobookshelf-data-pvc`) backed by the `synology-iscsi` storage class to store its SQLite database and configuration.
  - **Metadata**: Uses a PersistentVolumeClaim (`audiobookshelf-meta-data-pvc`) backed by the `synology-iscsi` storage class to store downloaded metadata (covers, author images).
  - **Media**: (Note: The media volume is typically mounted via NFS or iSCSI depending on the specific configuration, check the `storage.yaml` for exact details).
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://audiobooks.stage.burntbytes.com
- **Production**: https://audiobooks.burntbytes.com

## 4. Configuration
- **Environment Variables**: Loaded from the `audiobookshelf-container-env` ConfigMap.
- **ConfigMaps/Secrets**:
  - `audiobookshelf-sso-secret` (Secret): Contains the OIDC client secret for Authelia integration. Managed via SOPS.
- **SSO Integration**: Audiobookshelf is configured to use Authelia as an OpenID Connect (OIDC) provider. The `hostAliases` patch in the production deployment ensures the pod can resolve the Authelia URL internally.

## 5. Usage Instructions
- **Web UI**: Navigate to the URL and log in via Authelia (SSO).
- **Mobile App**: Download the Audiobookshelf app (iOS/Android), enter the server URL, and log in via OAuth.

## 6. Testing
To verify Audiobookshelf is working:
1. Navigate to the web UI and ensure the library loads.
2. Play an audiobook or podcast and verify it streams correctly.
3. Verify the pod is running: `kubectl get pods -n audiobookshelf-prod`

## 7. Monitoring & Alerting
- **Metrics**: Audiobookshelf does not expose Prometheus metrics natively.
- **Logs**: Check the pod logs for library scan errors or OIDC authentication issues:
  ```bash
  kubectl logs -n audiobookshelf-prod deploy/audiobookshelf
  ```

## 8. Disaster Recovery
- **Backup Strategy**:
  - **Media**: The audiobooks and podcasts are backed up natively on the Synology NAS.
  - **Config & Metadata**: The `audiobookshelf-data-pvc` and `audiobookshelf-meta-data-pvc` contain the SQLite database, user progress, and downloaded metadata. These are backed up via Synology Snapshot Replication.
- **Restore Procedure**:
  1. Restore the `audiobookshelf-data` and `audiobookshelf-meta-data` LUNs via Synology DSM if necessary.
  2. Ensure the media share is intact.
  3. Re-deploy the Audiobookshelf manifests.

## 9. Troubleshooting
- **OIDC Login Failing**:
  - Verify the `audiobookshelf-sso-secret` contains the correct client secret.
  - Check the pod logs for OIDC redirect URI mismatches or connection errors to Authelia.
  - Ensure the `hostAliases` patch is correctly resolving `auth.burntbytes.com` to the Gateway API IP.
- **Media Not Showing Up**:
  - Verify the media volume is mounted correctly and the pod has read permissions.
  - Trigger a manual library scan from the Audiobookshelf web UI.
