# Vitals

## 1. Overview
Vitals is a custom health and wellness tracking application for the homelab. It allows users to log and monitor various health metrics over time.

## 2. Architecture
Vitals is deployed as a Kubernetes `Deployment` with a single replica in the `vitals-prod` (and `vitals-stage`) namespace.
- **Image**: Uses a custom image hosted on GitHub Container Registry (`ghcr.io/gjcourt/vitals`).
- **Database**: Uses a CloudNativePG (CNPG) PostgreSQL cluster (`vitals-db-production-cnpg-v1`) for storing health data.
- **Storage**: The application itself is stateless, relying entirely on the PostgreSQL database.
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://vitals.stage.burntbytes.com
- **Production**: https://vitals.burntbytes.com

## 4. Configuration
- **Environment Variables**:
  - `PGHOST`: Provided via the `vitals-container-env` ConfigMap.
  - `PGPASSWORD`: Provided via the `vitals-db-credentials` Secret.
  - `PGUSER`, `PGDATABASE`, `PGSSLMODE`: Hardcoded in the deployment manifest.
- **ConfigMaps/Secrets**:
  - `vitals-container-env` (ConfigMap): Contains the database host.
  - `vitals-db-credentials` (Secret): Contains the PostgreSQL database credentials.
  - `ghcr-secret` (Secret): Used as an `imagePullSecret` to pull the custom image from GHCR.

## 5. Usage Instructions
- Navigate to the Vitals URL.
- Use the web interface to log new health metrics or view historical data.

## 6. Testing
To verify Vitals is working:
1. Navigate to the Vitals URL and ensure the UI loads.
2. Verify the `/api/health` endpoint returns a successful response.
3. Verify the pod is running: `kubectl get pods -n vitals-prod`
4. Verify the database cluster is healthy: `kubectl get cluster -n vitals-prod`

## 7. Monitoring & Alerting
- **Metrics**: The CNPG PostgreSQL cluster exposes metrics via a `PodMonitor`.
- **Logs**: Check the pod logs for application errors:
  ```bash
  kubectl logs -n vitals-prod deploy/vitals
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
  - Check the Vitals pod logs for database connection errors.
- **Image Pull Errors**:
  - Verify the `ghcr-secret` is valid and has permissions to pull the image.
