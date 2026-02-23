# AdGuard Home

## 1. Overview
AdGuard Home is a network-wide software for blocking ads and tracking. In this homelab, it serves as the primary DNS resolver for all devices on the network, providing ad-blocking, custom DNS overrides, and upstream DNS-over-HTTPS (DoH) resolution.

## 2. Architecture
AdGuard Home is deployed as a `StatefulSet` in the `adguard` namespace.
- **Storage**: Uses two PersistentVolumeClaims (`config` and `work`) backed by the `synology-iscsi` storage class to store its configuration (`AdGuardHome.yaml`) and query logs/statistics.
- **Networking**: 
  - Exposes UDP/TCP port 53 for DNS queries.
  - Exposes HTTP/HTTPS for the admin interface.
  - Uses Cilium L2 Announcements (via `LoadBalancer` service) to announce its IP address to the local network.
- **High Availability (HA)**: The `StatefulSet` can be scaled to multiple replicas. A `CronJob` (`adguard-sync`) runs periodically to synchronize configuration (filters, custom rules) from the primary pod (`adguard-0`) to the replicas.

## 3. URLs
- **Admin Interface**: https://adguard.burntbytes.com

## 4. Configuration
- **Environment Variables**: Loaded from `adguard-container-env` (ConfigMap) and `adguard-container-env-secret` (Secret).
- **ConfigMaps/Secrets**:
  - `adguard-sync-credentials` (Secret): Contains the usernames and passwords for the primary and replica instances, used by the `adguard-sync` CronJob. Managed via SOPS.
- **DNS Configuration**:
  - Upstream DNS servers are configured within the AdGuard Home UI (e.g., Cloudflare, Quad9).
  - Custom DNS rewrites are used to point internal domains (e.g., `*.burntbytes.com`) to the Cilium Gateway API LoadBalancer IP.

## 5. Usage Instructions
- **Admin Access**: Log in to the web interface to manage blocklists, view query logs, and configure custom DNS rewrites.
- **Client Configuration**: Configure your router (e.g., UniFi) to hand out the AdGuard Home LoadBalancer IP as the primary DNS server via DHCP.

## 6. Testing
To verify AdGuard Home is working:
1. Perform a DNS lookup against the AdGuard IP:
   ```bash
   dig @<adguard-ip> google.com
   ```
2. Verify the admin interface is accessible.
3. Check the `adguard-sync` CronJob logs to ensure configuration is syncing correctly (if running multiple replicas).

## 7. Monitoring & Alerting
- **Metrics**: AdGuard Home does not expose Prometheus metrics natively by default, but community exporters exist if needed. Currently, monitoring is done via the built-in dashboard.
- **Logs**: Check the pod logs for startup errors or DNS resolution issues:
  ```bash
  kubectl logs -n adguard statefulset/adguard
  ```

## 8. Disaster Recovery
- **Backup Strategy**: 
  - The `config` PVC contains the `AdGuardHome.yaml` configuration file. This should be backed up periodically.
  - The `work` PVC contains query logs and statistics, which are less critical.
- **Restore Procedure**: 
  - If the PVCs are lost, AdGuard Home will start with a default configuration. You will need to run through the initial setup wizard and manually restore the configuration (or restore the `config` PVC from a backup).

## 9. Troubleshooting
- **DNS Resolution Failing**: 
  - Check the AdGuard Home query log in the UI to see if queries are being blocked or if upstream resolution is failing.
  - Verify the pod is running and the `LoadBalancer` service has an IP assigned.
- **Sync Job Failing**: 
  - Check the logs of the `adguard-sync` CronJob: `kubectl logs -n adguard -l job-name=adguard-sync`
  - Verify the credentials in the `adguard-sync-credentials` secret are correct.
  - Ensure the replica pods are reachable from the sync job pod.
