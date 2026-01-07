# Homelab

Kubernetes homelab, managed GitOps-style with Flux.

## Quick links

- Applications overview: [apps/README.md](apps/README.md)
- Infrastructure overview: [infra/README.md](infra/README.md)

- Documentation index: [docs/README.md](docs/README.md)

Key docs:

	- AdGuard HA plan: [docs/TODO-adguard-ha.md](docs/TODO-adguard-ha.md)
	- Flux and deployments: [docs/flux-and-deployments.md](docs/flux-and-deployments.md)
	- Overlay structure: [docs/overlays-and-structure.md](docs/overlays-and-structure.md)
	- Making changes: [docs/making-changes.md](docs/making-changes.md)

## Repository layout

- [apps/](apps/): app manifests (base + environment overlays)
	- [apps/base/](apps/base/): shared app definitions
	- [apps/staging/](apps/staging/): staging overlay
	- [apps/production/](apps/production/): production overlay
- [infra/](infra/): cluster infrastructure (controllers + configs)
	- [infra/controllers/](infra/controllers/): HelmReleases/Kustomizations for operators (cert-manager, monitoring, logging, etc.)
	- [infra/configs/](infra/configs/): supporting configs (Cilium, MetalLB, etc.)
- [clusters/](clusters/): Flux entrypoints per cluster/environment
	- [clusters/staging/](clusters/staging/)
	- [clusters/production/](clusters/production/)
- [docs/](docs/): durable notes and runbooks
- [scripts/](scripts/): helper scripts (e.g. README generation, secret tooling)
- [monitoring/](monitoring/): misc monitoring resources
- [kubecraft/](kubecraft/): scratch/experiments

## Keeping the apps list up to date

The “Current applications” section in [apps/README.md](apps/README.md) is auto-generated from [apps/base/](apps/base/) using:

- [scripts/update-apps-readme.sh](scripts/update-apps-readme.sh)