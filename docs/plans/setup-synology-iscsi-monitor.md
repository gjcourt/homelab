# Setup Instructions: Synology iSCSI Monitor

A new custom application `synology-iscsi-monitor` has been scaffolded to monitor the number of iSCSI targets and LUNs on the Synology NAS. This is to prevent hitting the hard limit of 128 targets which causes provisioning failures.

## Components Created

*   **Custom Exporter**: Python script (`apps/base/synology-iscsi-monitor/script-cm.yaml`) that SSHs into the NAS and counts config entries.
*   **Alerting**: Prometheus Rule (`apps/base/synology-iscsi-monitor/prometheus-rule.yaml`) triggering at > 100 targets.
*   **Dashboard**: Grafana dashboard (`apps/base/synology-iscsi-monitor/dashboard.json`).
*   **Deployment**: Standard Kubernetes deployment.

## Action Required: Configure Secrets

The application requires the Synology admin password to SSH into the NAS. A placeholder secret has been created but needs to be updated and encrypted.

1.  **Open the secret file**:
    ```bash
    code apps/production/synology-iscsi-monitor/secret.yaml
    ```

2.  **Update the password**:
    Replace `"PLACEHOLDER_PASSWORD"` with the actual Synology admin password.

3.  **Encrypt the file**:
    Use SOPS to encrypt the file in place.
    ```bash
    sops --encrypt --in-place apps/production/synology-iscsi-monitor/secret.yaml
    ```

4.  **Commit and Push**:
    ```bash
    git add apps/
    git commit -m "feat: add synology-iscsi-monitor and encrypt secrets"
    git push origin <your-branch>
    ```

## Verification

Once Flux syncs the changes:

1.  **Check Pod Status**:
    ```bash
    kubectl get pods -n synology-iscsi-monitor
    ```
2.  **Verify Metrics**:
    Query Prometheus for `synology_iscsi_target_count`.
3.  **View Dashboard**:
    Open Grafana and search for the "Synology iSCSI Monitor" dashboard.
