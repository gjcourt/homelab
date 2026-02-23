# Mealie

## 1. Overview
Mealie is a self-hosted recipe manager and meal planner with a REST API backend and a reactive frontend application built in Vue. It allows you to easily add recipes into your database by providing the URL and Mealie will automatically import the relevant data.

## 2. Architecture
Mealie is deployed as a Kubernetes `Deployment` with a single replica in the `mealie-prod` (and `mealie-stage`) namespace.
- **Database**: Uses SQLite, stored within the application's data directory.
- **Storage**: Uses a PersistentVolumeClaim (`mealie-data-pvc`) backed by the `synology-iscsi` storage class for storing the SQLite database, recipes, and images.
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://mealie.stage.burntbytes.com
- **Production**: https://mealie.burntbytes.com

## 4. Configuration
- **Environment Variables**:
  - OIDC configuration is provided via the `mealie-oidc-config` ConfigMap.
- **ConfigMaps/Secrets**:
  - `mealie-oidc-secret` (Secret): Contains the OIDC client secret for Authelia integration (SOPS encrypted).
- **SSO Integration**: Uses `hostAliases` to resolve `auth.burntbytes.com` to the Gateway API IP (`192.168.5.33`) from within the pod, allowing it to communicate with Authelia for OIDC authentication.

## 5. Usage Instructions
- Navigate to the Mealie URL.
- Log in using your Authelia SSO credentials (if configured) or the local admin account.
- Use the web interface to import recipes, plan meals, and generate shopping lists.

## 6. Testing
To verify Mealie is working:
1. Navigate to the Mealie URL and ensure the login page loads.
2. Log in and import a test recipe from a URL.
3. Verify the pod is running: `kubectl get pods -n mealie-prod`

## 7. Monitoring & Alerting
- **Metrics**: Mealie does not expose Prometheus metrics natively.
- **Logs**: Check the pod logs for application errors:
  ```bash
  kubectl logs -n mealie-prod deploy/mealie
  ```

## 8. Disaster Recovery
- **Backup Strategy**:
  - The `mealie-data-pvc` (which contains the SQLite database and all media) is backed up via Synology Snapshot Replication.
- **Restore Procedure**:
  1. Restore the `mealie-data-pvc` LUN via Synology DSM if necessary.
  2. Re-deploy the Mealie manifests.

## 9. Troubleshooting
- **SSO Login Failing**:
  - Verify the `mealie-oidc-secret` contains the correct client secret.
  - Ensure the `hostAliases` patch is correctly resolving `auth.burntbytes.com` to the Gateway API IP.
  - Check the Mealie pod logs for OIDC redirect URI mismatches or connection errors to Authelia.
- **Recipe Import Failing**:
  - Check the pod logs for errors parsing the provided URL. Some websites may block automated scraping.
