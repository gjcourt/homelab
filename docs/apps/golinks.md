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
  - The PostgreSQL database is backed up to S3 (MinIO/AWS) using CNPG's Barman integration (Note: currently pending S3 credentials configuration).
- **Restore Procedure**:
  1. Uncomment the `recovery` section in the `database.yaml` CNPG `Cluster` definition.
  2. Comment out the `initdb` section.
  3. Apply the changes to bootstrap a new cluster from the backup.

## 9. Troubleshooting
- **Database Connection Errors**:
  - Verify the CNPG cluster is running and healthy.
  - Check the GoLinks pod logs for database connection errors.
- **Image Pull Errors**:
  - Verify the `ghcr-secret` is valid and has permissions to pull the image.
