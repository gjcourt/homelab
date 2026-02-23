# Homepage

## 1. Overview
Homepage is a modern, fully static, fast, secure, fully proxied, highly customizable application dashboard with integrations for over 100 services and translations into multiple languages. It serves as the primary landing page for the homelab, providing quick access to all deployed applications and infrastructure components.

## 2. Architecture
Homepage is deployed as a stateless Kubernetes `Deployment` with a single replica.
- **Storage**: It does not require persistent storage. All configuration is provided via a Kubernetes `ConfigMap`.
- **Service Account**: It uses a dedicated `ServiceAccount` (`homepage`) with a `ClusterRole` to query the Kubernetes API for cluster metrics and node status (used by the Kubernetes widget).
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://home.stage.burntbytes.com
- **Production**: https://home.burntbytes.com

## 4. Configuration
- **Environment Variables**: 
  - `HOMEPAGE_ALLOWED_HOSTS`: Set to `gethomepage.dev` (required by the application).
  - Additional variables can be injected via secrets for widget authentication (e.g., `HOMEPAGE_VAR_SYNOLOGY_USER`).
- **ConfigMaps/Secrets**:
  - `homepage` (ConfigMap): Contains all the YAML configuration files required by Homepage.
    - `settings.yaml`: Global settings, layout, theme, and background image.
    - `services.yaml`: Defines the application links and their associated widgets.
    - `bookmarks.yaml`: Defines static links (e.g., GitHub repos, documentation).
    - `widgets.yaml`: Defines global widgets (e.g., search bar, Kubernetes cluster status).
    - `kubernetes.yaml`, `docker.yaml`, `proxmox.yaml`: Empty files required by Homepage to prevent startup crashes.

### Updating the Dashboard
To add a new service or change the layout, edit the `configmap-patch.yaml` in the respective environment overlay (`apps/production/homepage/configmap-patch.yaml` or `apps/staging/homepage/configmap-patch.yaml`).
The `services.yaml` is often shared or patched depending on the environment.

## 5. Usage Instructions
Navigate to the Homepage URL. The dashboard is read-only. Clicking on a service icon will open that service in a new tab.

## 6. Testing
To verify Homepage is working:
1. Navigate to https://home.burntbytes.com.
2. Verify the page loads and the widgets (e.g., Kubernetes cluster status, Synology NAS status) are displaying data.
3. Verify the `homepage` pod is running: `kubectl get pods -n homepage`

## 7. Monitoring & Alerting
- **Metrics**: Homepage does not expose Prometheus metrics natively.
- **Logs**: Check the pod logs for configuration errors or widget connection issues:
  ```bash
  kubectl logs -n homepage deploy/homepage
  ```

## 8. Disaster Recovery
- **Backup Strategy**: All configuration is stored declaratively in this Git repository. No data backup is required.
- **Restore Procedure**: Re-apply the Flux Kustomization. The dashboard will be recreated exactly as defined in Git.

## 9. Troubleshooting
- **Widgets Not Loading**: 
  - Check the pod logs for API connection errors.
  - Ensure the target service is reachable from the `homepage` pod.
  - Verify any required authentication credentials (e.g., API keys, usernames/passwords) are correctly injected via environment variables or secrets.
- **Kubernetes Widget Errors**: Ensure the `homepage` ServiceAccount has the correct RBAC permissions to read nodes and pods.
- **Configuration Changes Not Applying**: Homepage hot-reloads configuration changes. If changes don't appear, verify the ConfigMap was updated in the cluster (`kubectl describe cm homepage -n homepage`) and check the pod logs for YAML parsing errors.
