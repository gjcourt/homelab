# Homelab

GitOps-managed Kubernetes cluster, powered by [Flux](https://fluxcd.io/) and [Kustomize](https://kustomize.io/).

## Cluster

| | |
|---|---|
| **Name** | `melodic-muse` |
| **Nodes** | Single-node (Talos Linux) |
| **CNI** | Cilium (+ Gateway API, LB IPAM) |
| **Storage** | Synology NAS via iSCSI (synology-csi) |
| **Secrets** | SOPS + age, decrypted in-cluster by Flux |
| **DNS** | AdGuard Home with split-horizon wildcard rewrites |
| **Certificates** | cert-manager + Let's Encrypt |
| **Monitoring** | Prometheus + Grafana (kube-prometheus-stack), Loki + Promtail |
| **Databases** | CloudNativePG (PostgreSQL operator) |

## Applications

All apps run in both staging and production environments via Kustomize overlays.
See [apps/README.md](apps/README.md) for the full auto-generated list.

| App | Description |
|-----|-------------|
| [AdGuard Home](apps/base/adguard/) | DNS ad-blocking and filtering |
| [Audiobookshelf](apps/base/audiobookshelf/) | Audiobook and podcast server |
| [Authelia](apps/base/authelia/) | SSO / OIDC identity provider |
| [Excalidraw](apps/base/excalidraw/) | Collaborative whiteboard |
| [Golinks](apps/base/golinks/) | Short link service (`go/` links) |
| [Home Assistant](apps/base/homeassistant/) | Home automation |
| [Homepage](apps/base/homepage/) | Dashboard / service overview |
| [Immich](apps/base/immich/) | Photo and video management |
| [Jellyfin](apps/base/jellyfin/) | Media server |
| [Linkding](apps/base/linkding/) | Bookmark manager |
| [Mealie](apps/base/mealie/) | Recipe management |
| [Memos](apps/base/memos/) | Note-taking / micro-blog |
| [Snapcast](apps/base/snapcast/) | Multi-room audio streaming |

## Infrastructure

Core services that run cluster-wide. See [infra/README.md](infra/README.md) for details.

cert-manager · Cilium · CloudNativePG · kube-prometheus-stack · Loki · Promtail ·
Mosquitto · Renovate · Snapshot controller · Synology CSI · Zigbee2MQTT

## Quick Start

```bash
# Check cluster connectivity
make kubectl-context

# Lint manifests
make lint

# Render and validate
make test

# Force Flux to reconcile
flux reconcile ks apps-production
flux reconcile ks apps-staging
```

## Documentation

All docs are in [`docs/`](docs/README.md):

| Topic | Link |
|-------|------|
| **Making changes** | [docs/runbooks/making-changes.md](docs/runbooks/making-changes.md) |
| **Flux & debugging** | [docs/runbooks/flux-and-deployments.md](docs/runbooks/flux-and-deployments.md) |
| **iSCSI storage ops** | [docs/runbooks/synology-iscsi-operations.md](docs/runbooks/synology-iscsi-operations.md) |
| **Repo structure** | [docs/overlays-and-structure.md](docs/overlays-and-structure.md) |
| **DNS strategy** | [docs/dns-strategy.md](docs/dns-strategy.md) |
| **Authelia SSO** | [docs/authelia.md](docs/authelia.md) |
| **Incident history** | [docs/incidents/](docs/incidents/) |

## Repository Layout

```
apps/
  base/          # Shared app manifests (environment-agnostic)
  staging/       # Staging overlays (limited resources, staging ingress)
  production/    # Production overlays (full resources, public ingress)
clusters/
  melodic-muse/  # Flux entry points (apps-production, apps-staging, infra)
docs/
  runbooks/      # Operational procedures
  incidents/     # Post-mortems
  plans/         # Future work
infra/
  controllers/   # System operators (cert-manager, Cilium, CNPG, monitoring, etc.)
  configs/       # Cluster-wide config (gateway, certificates, LB pools)
images/          # Custom container image Dockerfiles
scripts/
  synology/      # iSCSI storage management tools
```
