# Authelia

## 1. Overview
Authelia is an open-source authentication and authorization server providing two-factor authentication and single sign-on (SSO) for applications via a web portal. In this homelab, it acts as the primary OpenID Connect (OIDC) provider for services like Mealie, Memos, Audiobookshelf, and Linkding.

## 2. Architecture
Authelia is deployed as a standard Kubernetes `Deployment` with a single replica.
- **Storage**: Uses a PersistentVolumeClaim (`authelia-data`) backed by the `synology-iscsi` storage class to store its SQLite database (which holds user sessions and OIDC state).
- **Users**: User accounts are currently managed via a static file (`users.yml`) mounted from a Kubernetes Secret.
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://auth.stage.burntbytes.com
- **Production**: https://auth.burntbytes.com

## 4. Configuration
- **Environment Variables**: Loaded from the `authelia-secrets` Secret (contains JWT, session, and storage encryption keys).
- **Command Line Options**: `--config /config/configuration.yaml --config.experimental.filters template`
- **ConfigMaps/Secrets**:
  - `authelia-config` (ConfigMap): Contains the main `configuration.yaml` defining OIDC clients, access control rules, and the SQLite database path.
  - `authelia-users` (Secret): Contains the `users.yml` file with usernames and Argon2id password hashes. Managed via SOPS.
  - `authelia-secrets` (Secret): Contains sensitive keys (e.g., `AUTHELIA_JWT_SECRET`, `AUTHELIA_SESSION_SECRET`). Managed via SOPS.

### Adding an OIDC Client
To add a new application to Authelia:
1. Generate a client secret: `openssl rand -hex 32`
2. Add the client definition to `apps/production/authelia/configuration.yaml` (and staging).
3. Add the client secret to the application's secret file (e.g., `apps/production/mealie/secret-app.yaml`).

## 5. Usage Instructions
Users navigate to an application (e.g., Mealie) and click "Login with Authelia". They are redirected to the Authelia portal, authenticate, and are redirected back to the application.

### Retrieving 2FA/Verification Codes (Development Mode)
Currently, Authelia is configured to use a filesystem notifier instead of SMTP. To retrieve a one-time code (e.g., for device registration):
```bash
# Production
kubectl exec -n authelia-prod deploy/authelia -- cat /config/notification.txt

# Staging
kubectl exec -n authelia-stage deploy/authelia -- cat /config/notification.txt
```

## 6. Testing
To verify Authelia is working:
1. Navigate to https://auth.burntbytes.com.
2. Attempt to log in with a valid user account.
3. Verify the `authelia` pod is running: `kubectl get pods -n authelia-prod`

## 7. Monitoring & Alerting
- **Metrics**: Authelia exposes Prometheus metrics at `/metrics`.
- **Logs**: Check the pod logs for authentication failures or OIDC errors:
  ```bash
  kubectl logs -n authelia-prod deploy/authelia
  ```

## 8. Disaster Recovery
- **Backup Strategy**: 
  - The `users.yml` and `configuration.yaml` are stored in Git.
  - The SQLite database on the `authelia-data` PVC contains active sessions and OIDC consent state. Losing this database will force all users to re-authenticate and re-consent to OIDC clients, but no permanent configuration is lost.
- **Restore Procedure**: 
  - If the PVC is lost, simply delete the PVC and Pod. A new empty SQLite database will be created automatically on startup. Users will need to log in again.

## 9. Troubleshooting
- **OIDC Redirect URI Mismatch**: Ensure the `redirect_uris` in `configuration.yaml` exactly match the callback URL configured in the client application.
- **Invalid Client Secret**: Ensure the client secret configured in the application matches the plaintext secret (or the hashed secret if using hashed secrets in Authelia).
- **User Cannot Log In**: Verify the password hash in `users.yml` is correct. You can generate a new hash using:
  ```bash
  docker run --rm -it authelia/authelia:4.38.19 authelia crypto hash generate argon2 --password 'NEW_PASSWORD'
  ```
