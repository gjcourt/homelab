# GoLinks

## 1. Overview
GoLinks is a custom URL shortener for the homelab. It allows you to create memorable, short links (e.g., `go.burntbytes.com/router`) that redirect to longer, more complex URLs.

## 2. Architecture
GoLinks is deployed as a Kubernetes `Deployment` with a single replica in the `golinks-prod` (and `golinks-stage`) namespace.
- **Image**: Uses a custom image hosted on GitHub Container Registry (`ghcr.io/gjcourt/golinks`).
- **Database**: Uses a CloudNativePG (CNPG) PostgreSQL cluster (`golinks-db-production-cnpg-v1`) for storing the link mappings.
- **Storage**: The application itself is stateless, relying entirely on the PostgreSQL database.
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://go.stage.burntbytes.com
- **Production**: https://go.burntbytes.com
- **Intranet shortcut**: `go/<link>` — http://go/ always; https://go/ on devices that trust the intranet CA (below)

### HTTPS for bare `go`
No public CA issues certificates for single-label names, so `https://go/` uses a
private root: `intranet-root-ca` (`infra/configs/cert-manager-issuers/intranet-ca.yaml`),
name-constrained to `go` so it can't vouch for any other site. cert-manager issues
`go-intranet-tls` from it (90 days, auto-renewed); the gateway's `https-go` listener
serves it. A device must trust the root once — until then `https://go/` shows a
certificate error and `http://go/` keeps working.

Export the root (public certificate only; the key stays in the cluster):

```bash
kubectl -n security get secret intranet-root-ca -o jsonpath='{.data.tls\.crt}' | base64 -d > intranet-root-ca.crt
openssl x509 -in intranet-root-ca.crt -noout -subject -ext nameConstraints
```

Trust it:

- **macOS** (Safari, Chrome): `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain intranet-root-ca.crt`
- **iOS / iPadOS**: AirDrop the `.crt`, install the profile in Settings, then enable it under
  Settings → General → About → Certificate Trust Settings.

Verify from the LAN:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' --cacert intranet-root-ca.crt https://go/
openssl s_client -connect go:443 -servername go </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -ext subjectAltName
```

The root lasts 10 years and its key is never rotated, so renewals don't need re-trusting.
Once every device you use trusts it, `golinks-http-intranet` can redirect to HTTPS like
`golinks-http` does.

## 4. Configuration
- **Environment Variables**:
  - `DB_HOST`: Provided via the `golinks-container-env` ConfigMap.
  - `DB_PASSWORD`: Provided via the `golinks-db-credentials` Secret.
  - `DATABASE_URL`: Constructed dynamically in the deployment manifest using the host and password.
- **ConfigMaps/Secrets**:
  - `golinks-container-env` (ConfigMap): Contains the database host.
  - `golinks-db-credentials` (Secret): Contains the PostgreSQL database credentials.
  - `ghcr-secret` (Secret): Used as an `imagePullSecret` to pull the custom image from GHCR.

## 5. Usage Instructions
- Navigate to the GoLinks URL to view and manage existing links.
- Use the web interface to create new short links.
- To use a link, simply navigate to `go.burntbytes.com/<your-link-name>`.

## 6. Testing
To verify GoLinks is working:
1. Navigate to the GoLinks URL and ensure the UI loads.
2. Create a test link and verify it redirects correctly.
3. Verify the pod is running: `kubectl get pods -n golinks-prod`
4. Verify the database cluster is healthy: `kubectl get cluster -n golinks-prod`

## 7. Monitoring & Alerting
- **Metrics**: The CNPG PostgreSQL cluster exposes metrics via a `PodMonitor`.
- **Logs**: Check the pod logs for application errors:
  ```bash
  kubectl logs -n golinks-prod deploy/golinks
  ```

## 8. Disaster Recovery
- **Backup Strategy**:
  - The PostgreSQL database is backed up continuously to `s3://gjcourt-homelab-backup/production/golinks` via the Barman Cloud Plugin (WAL archiving + daily base backups, gzip-compressed, 30-day retention).
- **Restore Procedure**:
  1. Uncomment the `recovery` section in `apps/production/golinks/database.yaml`.
  2. Comment out the `initdb` section.
  3. Apply the changes; CNPG will bootstrap a new cluster from the S3 backup via PITR.

## 9. Troubleshooting
- **Database Connection Errors**:
  - Verify the CNPG cluster is running and healthy.
  - Check the GoLinks pod logs for database connection errors.
- **Image Pull Errors**:
  - Verify the `ghcr-secret` is valid and has permissions to pull the image.
