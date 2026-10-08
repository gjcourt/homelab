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
- **SSO Integration**: Audiobookshelf is configured to use Authelia as an OpenID Connect (OIDC) provider. The `hostAliases` patch in each overlay pins the Authelia host to the Gateway VIP (prod `10.42.2.40`, staging `10.42.2.42`), and the CiliumNetworkPolicy (`apps/base/audiobookshelf/networkpolicy.yaml`) allows TCP 443 egress to those two VIPs. Both are needed: without the egress rule the Gateway's Envoy rejects the back-channel with `403 Forbidden`.

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
  - `OPError: expected 200 OK, got: 403 Forbidden` after a successful Authelia login means the network policy is blocking the back-channel, not Authelia. Confirm from the pod with `kubectl -n audiobookshelf-prod exec deploy/audiobookshelf -- wget -S -O- https://auth.burntbytes.com/.well-known/openid-configuration` and look for `http-request DROPPED` in `hubble observe --from-namespace audiobookshelf-prod --to-ip 10.42.2.40`. If the Gateway VIP changes, update both the overlay `hostAliases` and the policy CIDRs.
  - A redirect URI mismatch is logged by **Authelia**, not ABS: `kubectl -n authelia-prod logs deploy/authelia | grep redirect_uri`. The browser shows `invalid_request` with a hint about pre-registered `redirect_uris`.
  - Since 2.37.1, ABS serves from `ROUTER_BASE_PATH=/audiobookshelf` by default (startup log: `Serving from base path "/audiobookshelf"`), so its callback is `https://<host>/audiobookshelf/auth/openid/callback`. The Authelia `audiobookshelf` client must list that URI. If the base path changes again, update `apps/{production,staging}/authelia/configuration.yaml` to match.
  - Ensure the `hostAliases` patch is correctly resolving `auth.burntbytes.com` to the Gateway API IP.
- **Media Not Showing Up**:
  - Verify the media volume is mounted correctly and the pod has read permissions.
  - Trigger a manual library scan from the Audiobookshelf web UI.
