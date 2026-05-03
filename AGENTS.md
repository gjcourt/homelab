# AGENTS.md

> GitOps repo for a single-node Talos Kubernetes cluster (`melodic-muse`) running self-hosted apps via Flux CD. — https://github.com/gjcourt/homelab

## Commands

| Command | Use |
|---------|-----|
| `kustomize build apps/staging/<overlay>` | Validate a staging overlay |
| `kustomize build apps/production/<overlay>` | Validate a production overlay |
| `kustomize build infra/controllers` | Validate infra controllers |
| `flux get kustomizations -A` | Top-level Kustomization health |
| `flux reconcile kustomization apps-production -n flux-system` | Force production reconcile after merge |
| `flux reconcile source git flux-system-staging` | Force staging git source refresh |
| `kubectl describe helmrelease <name> -n <namespace>` | Inspect a HelmRelease failure |
| `gh workflow run staging-deploy.yaml` | Force a staging branch rebuild |
| `sops -e -i <file>` / `sops -d <file>` | Encrypt / inspect secrets |

Pre-PR: `kustomize build` for every affected overlay must pass.

## Architecture

Flux CD (GitOps) cluster — all cluster state is driven from Git; changes take effect when merged to `master` and reconciled by Flux on each Kustomization's `interval` (default 10m).

- `apps/base/<app>/` — base Kustomize resources (env-agnostic).
- `apps/staging/<app>/`, `apps/production/<app>/` — environment overlays.
- `infra/controllers/` — HelmReleases (monitoring, CNI, CSI, etc.).
- `infra/configs/` — cluster configuration controllers depend on (IP pools, cert issuers).
- `clusters/melodic-muse/` — Flux Kustomization entrypoints.

Reconciliation order: `infra-crds` → `infra-controllers` → `infra-configs` → `apps-production` / `apps-staging`.

See `docs/architecture/` for component-level architecture (DNS strategy, gateway auth, overlays-and-structure).

## Conventions

- **Branch + PR for every change** — never commit directly to `master` or `staging`.
- **Image tags are strictly increasing** — never roll back to an earlier tag without explicit intent. CI tags as `YYYY-MM-DD` (first build of day) then `YYYY-MM-DD-N`.
- **Namespace convention**: production uses the plain name (`overture`); staging uses a `-stage` suffix (`overture-stage`).
- **Secrets are SOPS-encrypted** before commit (key ref: `.sops.yaml`); never commit plaintext.
- **Adding a new app**: see `docs/operations/2026-05-02-adding-an-app.md`.
- **Conventional Commits** for every commit (`feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`, `ci:`, `deploy:`).
- **Branch names** follow `<type>/<description>`.

## Invariants

- Never commit directly to `master` — Flux deploys from `master`, all changes go through PR.
- Never push to `staging` manually — CI rebuilds it from `master + open PRs` on every trigger; manual pushes are overwritten.
- Never commit plaintext secrets — encrypt with SOPS first.
- Never bypass staging for production changes — open a PR, let CI merge to staging, validate, then merge to `master`.
- Image tags must be strictly greater than the currently deployed tag.

## What NOT to Do

- Do not branch from another feature/fix branch — always branch from `master`.
- Do not skip `kustomize build` validation before pushing.
- Do not roll back an image tag silently — explicit rollback PRs only.
- Do not put plaintext secrets anywhere in the tree, even in dev/test overlays.
- Do not edit the staging branch directly; CI owns it.

## Domain

Single-node Talos Kubernetes cluster running ~14 self-hosted apps (Audiobookshelf, Authelia, Excalidraw, Golinks, Homepage, Immich, Jellyfin, Linkding, Mealie, Memos, Navidrome, Pingo, Snapcast, Vitals + Adguard / Vitals etc.) plus infrastructure (Cilium, cert-manager, CNPG, monitoring, Authelia SSO). GitOps via Flux CD with an automatic preview environment (`staging` branch) rebuilt by CI from `master + open PRs`.

## Cross-service dependencies

| Service | Purpose |
|---|---|
| Talos Linux | Single-node Kubernetes substrate |
| Flux CD | GitOps reconciliation |
| Cilium + Gateway API | CNI + ingress |
| cert-manager | TLS certificate issuance |
| CNPG (Cloudnative-PG) | PostgreSQL operator |
| Authelia | SSO / OAuth2 / OIDC |
| Synology iSCSI | Block storage backing PVCs |
| GitHub Actions | CI for kustomize build + staging branch rebuild |
| ghcr.io | Container image registry (`gjcourt/<app>`) |

## Quality gate before push

1. `kustomize build` passes for every affected overlay
2. `git diff HEAD | grep -i "password\|secret\|key"` returns no plaintext
3. Image tags are strictly increasing (never silently rolled back)
4. New apps wired into the right `apps/{staging,production}/kustomization.yaml`
5. Namespace follows the convention (production unsuffixed, staging `-stage`)
6. New CNPG clusters: iSCSI PVC provisioned and StorageClass correct
7. Docs updated if the change affects a runbook or architecture doc

## Documentation

`docs/` taxonomy: `architecture/` · `design/` · `operations/` · `plans/` · `reference/` · `research/`. Each folder's `README.md` describes scope. Index: `docs/README.md`.

Per-app runbooks live under `docs/operations/apps/<app>.md`. Incident postmortems live under `docs/operations/incidents/<yyyy-mm-dd>-<topic>.md`. Per-doc content rewrites are tracked in `docs/plans/2026-02-21-documentation-rewrite-plan.md`.

## Observability

- `flux get kustomizations -A` — top-level Kustomization health.
- `kubectl describe helmrelease <name> -n <namespace>` — HelmRelease failures.
- `flux reconcile helmrelease <name> -n <namespace> --reset` — force reconcile after a stalled HelmRelease.
- `kubectl -n <ns> get events --sort-by=.lastTimestamp | tail -n 50` — recent events.
- Common Flux failure patterns and recovery: `docs/operations/2026-05-02-flux-debugging.md`.
- Per-app observability dashboards: see each `docs/operations/apps/<app>.md` runbook.

When you learn a new convention or invariant in this repo, update this file.
