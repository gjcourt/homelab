# Homelab

GitOps-managed Kubernetes cluster, powered by [Flux](https://fluxcd.io/) and [Kustomize](https://kustomize.io/).

## 📖 Overview

This repository drives the state of the home infrastructure. It uses a **GitOps** workflow: changes are made in Git (via Pull Requests), and Flux reconciles the cluster to match this state.

**Cluster**: `melodic-muse` (Single physical cluster)
**Storage**: Synology NAS (iSCSI via CSI)
**Networking**: Cilium (CNI), MetalLB, ExternalDNS

## 🏗 Architecture

The repository follows a **dry (Don't Repeat Yourself)** structure using Kustomize bases and overlays:

- **`apps/base`**: The "source of truth" for application manifests. Agnostic of environment.
- **`apps/staging`**: Test environment. Applies specific patches (limited resources, staging ingress).
- **`apps/production`**: Live environment. Applies production patches (full resources, public ingress, persistent storage).
- **`infra/`**: Core system services (Cert Manager, Monitoring, CNI) that run cluster-wide.

> Read more: [Overlays and Structure Strategy](docs/overlays-and-structure.md)

## 🛠 Operations

### Common Tasks

| Task | Guide |
|------|-------|
| **Submit a Change** | [Workflow & PRs](docs/making-changes.md) |
| **Add a New App**   | [App Structure](docs/overlays-and-structure.md) |
| **Manage Secrets**  | [Encryption (SOPS)](scripts/README.md) |
| **Debug Deployment**| [Flux & Debugging](docs/flux-and-deployments.md) |
| **Storage Cleanup** | [Synology Maintenance](docs/synology-iscsi-cleanup.md) |
| **Update Apps List**| `scripts/update-apps-readme.sh` |

### Quick Commands

*   **List Apps**: `kubectl get kustomizations -n flux-system`
*   **Reconcile Now**: `flux reconcile ks apps-production`
*   **Check Alerts**: `kubectl get alerts -A`

## 📂 Repository Layout

*   [`apps/`](apps/) - Application definitions.
    *   [`base/`](apps/base/) - Shared configuration.
    *   [`production/`](apps/production/) - Live overlays.
    *   [`staging/`](apps/staging/) - Test overlays.
*   [`clusters/`](clusters/) - Flux entrypoints.
*   [`infra/`](infra/) - System-level controllers & configs.
*   [`docs/`](docs/) - Runbooks and architecture notes.
*   [`scripts/`](scripts/) - Automation & maintenance tools.

## 🔎 Status

- **Applications Index**: [See apps/README.md](apps/README.md)
- **Infrastructure Index**: [See infra/README.md](infra/README.md)
