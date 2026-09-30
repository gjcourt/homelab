<!-- readme-type: infra -->
# Homelab

GitOps for a 6-node Talos Kubernetes homelab cluster, reconciled by Flux CD

Running a cluster by hand invites config drift and leaves no record of what
changed or why. This repo is the single source of truth for the
`melodic-muse` Talos cluster: every app, controller, and cluster config lives
here as a Kustomize base, overlay, or Helm release. Flux CD reconciles
`master` into the cluster on a 10-minute interval (or on demand), so a merged
PR is the only way anything reaches production.

**Status:** in daily use — this is the live source of truth for the
`melodic-muse` cluster; Flux reconciles `master` continuously, with commits
landing as recently as 2026-09-29.

## Layout

```text
apps/       Kustomize bases + staging/production overlays, one directory per app
infra/      controllers (HelmReleases) and the configs they depend on
clusters/   Flux Kustomization entrypoints
hosts/      docker-compose services running outside Kubernetes (hestia, alcatraz)
images/     Dockerfiles for the images this repo builds and publishes to ghcr.io
firmware/   ESPHome configs for IR blasters and sensors
docs/       architecture, runbooks, plans, incident postmortems
scripts/    repo maintenance and host-ops tooling
renovate/   Renovate dependency-update config
```

Current app and infra inventory: [apps/README.md](apps/README.md),
[infra/README.md](infra/README.md). Current cluster state, in-flight work, and
known issues: [docs/STATUS.md](docs/STATUS.md).

## Making a change

1. Branch from `master`: `git checkout master && git pull && git checkout -b <type>/<description>`.
2. Edit `apps/`, `infra/`, `docs/`, or `scripts/`, and validate locally (see Development below).
3. Push and open a PR. CI rebuilds the `staging` branch (`master` plus every
   open PR) and deploys it to the `-stage` namespaces, so you can check the
   change against the real cluster before it merges.
4. Merging to `master` deploys to production on Flux's next reconcile, or
   force it with `flux reconcile kustomization apps-production -n flux-system`.

Secrets are SOPS-encrypted before commit and decrypted in-cluster by Flux;
never commit a plaintext secret. Full walkthrough, including adding a new app
and rolling back:
[docs/operations/making-changes.md](docs/operations/making-changes.md).

## Development

```bash
make test                                    # kustomize build + kubeconform: apps/staging, apps/production, infra/configs, infra/controllers
make lint                                    # yamllint (.yamllint) + shellcheck on scripts/*.sh
(cd scripts/plans-index && go run . -check)  # verify docs/plans frontmatter matches the generated index
```

Conventions, invariants, and the full command reference:
[AGENTS.md](AGENTS.md).

## License

No licence file yet.
