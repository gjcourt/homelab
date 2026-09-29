# Homelab

GitOps repo for a 6-node Talos Kubernetes cluster (`melodic-muse`) running a set
of self-hosted apps, reconciled by [Flux CD](https://fluxcd.io/) from this
repository. Cluster state lives entirely in Git: a merge to `master` is picked
up by Flux on its next reconcile (10 minutes by default, or forced with
`flux reconcile kustomization apps-production -n flux-system`).

For what's actually running right now — node health, in-flight work, known
issues — see [docs/STATUS.md](docs/STATUS.md), not this file.

## Layout

- `apps/base/<app>/` — environment-agnostic Kustomize base per app (current list: [apps/README.md](apps/README.md))
- `apps/staging/<app>/`, `apps/production/<app>/` — overlays; production namespaces are unsuffixed, staging uses a `-stage` suffix
- `infra/controllers/` — HelmReleases for cluster-wide services: Cilium, cert-manager, CNPG, democratic-csi, monitoring, etc. ([infra/README.md](infra/README.md))
- `infra/configs/` — configuration the controllers depend on (LB IP pools, cert issuers, alert rules)
- `clusters/melodic-muse/` — Flux Kustomization entrypoints
- `hosts/` — docker-compose services running directly on hestia (TrueNAS) and alcatraz (Synology), outside Kubernetes
- `images/` — Dockerfiles for the container images this repo builds and publishes to ghcr.io
- `firmware/` — ESPHome configs for IR blasters and sensors
- `docs/` — architecture, operations runbooks, plans, and incident postmortems ([docs/README.md](docs/README.md))
- `scripts/` — repo maintenance and host-ops tooling

## Making a change

Every change goes through a branch and a PR — nothing is committed directly to
`master` or `staging`.

```bash
git checkout master && git pull
git checkout -b <type>/<description>

# edit apps/, infra/, docs/, or scripts/

kubectl kustomize apps/staging/<app>   # validate the overlay you touched
make test-kustomize                    # or render staging + production + infra in one shot

git push origin <type>/<description>
gh pr create
```

Opening a PR triggers CI to rebuild the `staging` branch (`master` plus every
open PR) and deploy it to the `-stage` namespaces, so you can check the change
against the real cluster before it merges. Merging to `master` deploys to
production on Flux's next reconcile. Full walkthrough, including adding a new
app and rolling back: [docs/operations/making-changes.md](docs/operations/making-changes.md).

Secrets are SOPS-encrypted before commit and decrypted in-cluster by Flux; never
commit a plaintext secret.

## Validating locally

```bash
make test-kustomize    # render apps/staging, apps/production, infra/configs, infra/controllers
make test-kubeconform  # schema-validate the rendered output (requires docker)
make lint              # yamllint + shellcheck
```

## Storage and networking

Persistent volumes are backed by hestia (TrueNAS) via `democratic-csi` iSCSI
(`truenas-iscsi*` StorageClasses). Synology/alcatraz no longer serves cluster
volumes; it now handles photo storage and off-box backups. Cilium provides the
CNI and Gateway API ingress, with L2 + BGP advertisement of load balancer IPs.

## Documentation

- [docs/STATUS.md](docs/STATUS.md) — current state, in-flight work, known issues
- [docs/architecture/](docs/architecture/README.md) — how the cluster is built today
- [docs/operations/](docs/operations/README.md) — runbooks, per-app guides, incident postmortems
- [docs/plans/](docs/plans/README.md) — phased migrations and rollouts
- [AGENTS.md](AGENTS.md) — full repo conventions and invariants
