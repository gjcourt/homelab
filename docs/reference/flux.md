# Flux CD

## 1. Overview
Flux CD is the GitOps continuous delivery tool used to manage the homelab cluster. It automatically reconciles the state of the Kubernetes cluster with the configuration defined in this Git repository.

## 2. Architecture
Flux is deployed in the `flux-system` namespace. It uses a set of controllers (Source Controller, Kustomize Controller, Helm Controller, Notification Controller) to pull manifests from Git and apply them to the cluster.
The cluster `melodic-muse` runs both staging and production from a single set of Flux Kustomizations:
- `clusters/melodic-muse/apps-staging.yaml` (applies `./apps/staging`)
- `clusters/melodic-muse/apps-production.yaml` (applies `./apps/production`)
- `clusters/melodic-muse/infra.yaml` (applies `./infra/controllers` + `./infra/configs`)

Production (`apps-production`, `infra`) references the `GitRepository` named `flux-system` which tracks the `master` branch. Staging (`apps-staging`) references `flux-system-staging` which tracks the `staging` branch. All Kustomizations use SOPS decryption via the `sops-agekey` secret.

## 3. URLs
- **GitHub Repository**: https://github.com/gjcourt/homelab

## 4. Configuration
- **Environment Variables**: N/A (Managed by Flux CLI during bootstrap)
- **Command Line Options**: N/A
- **ConfigMaps/Secrets**:
  - `sops-agekey`: Secret containing the Age private key used by Flux to decrypt SOPS-encrypted secrets in the repository.

## 5. Usage Instructions
Flux operates automatically. However, you can manually trigger reconciliations:

**Staging:**
```bash
flux reconcile source git flux-system-staging
flux reconcile kustomization apps-staging -n flux-system
```

**Production:**
```bash
flux reconcile source git flux-system
flux reconcile kustomization infra-controllers -n flux-system
flux reconcile kustomization infra-configs -n flux-system
flux reconcile kustomization apps-production -n flux-system
```

## 6. Testing
To verify Flux is working correctly:
```bash
flux get kustomizations -A
flux get sources git -A
```
All resources should report a `Ready` status.

## 7. Monitoring & Alerting
- **Metrics**: Flux exposes Prometheus metrics. Key metrics include `gotk_reconcile_condition` and `gotk_reconcile_duration_seconds`.
- **Logs**: Check controller logs using `kubectl logs -n flux-system deploy/kustomize-controller`.
- **Alerts**: Alertmanager is configured to send notifications for Flux reconciliation failures.

## 8. Disaster Recovery
- **Backup Strategy**: The entire cluster state is stored in this Git repository. The `sops-agekey` must be backed up securely outside the cluster (e.g., in a password manager).
- **Restore Procedure**:
  1. Recreate the cluster.
  2. Apply the `sops-agekey` secret to the `flux-system` namespace.
  3. Run `flux bootstrap github ...` to reinstall Flux and point it to this repository. Flux will automatically restore the cluster state.

## 9. Troubleshooting
- If a reconcile times out, check the failing controller/webhook first (e.g., cert-manager webhook availability).
- Check Flux resource status: `kubectl -n flux-system get kustomizations,gitrepositories`
- Check cluster events: `kubectl -n flux-system get events --sort-by=.lastTimestamp | tail -n 50`
