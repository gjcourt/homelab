> Claude Code users: this file is also referenced from [CLAUDE.md](./CLAUDE.md).

# Homelab GitOps Guidelines

## Repository Overview

Flux CD (GitOps) cluster for a single-node Talos Kubernetes cluster (`melodic-muse`). All cluster state is driven from Git — changes take effect when merged to `master` and reconciled by Flux.

## Architecture

- `apps/base/` — base Kustomize resources for each app
- `apps/production/` / `apps/staging/` — environment overlays (patch namespace, labels, etc.)
- `infra/controllers/` — HelmReleases and supporting config (monitoring, CNI, CSI, etc.)
- `clusters/melodic-muse/` — Flux Kustomization entrypoints

Flux kustomizations: `infra-crds` → `infra-controllers` → `infra-configs` → `apps-production` / `apps-staging`

## Before Making Changes

**Always check if there is an open PR before starting new work.** If one exists, verify whether it has been merged before creating a new branch or adding commits. Unmerged changes on a stale branch will not be reconciled by Flux.

```bash
gh pr list --repo gjcourt/homelab
```

## Image versioning

CI tags images as `YYYY-MM-DD` for the first build of the day, then `YYYY-MM-DD-N` (N=1,2,…) for subsequent builds. **Every push to `main` in the app repo triggers a build**, so tag numbers are not sequential relative to deploys — docs-only commits consume slots too.

When bumping an image tag in `deployment.yaml`, always verify the new tag is strictly greater than the currently deployed tag. Never roll back to an earlier tag without an explicit rollback intent. If unsure of the latest published tag, check:

```bash
gh api /users/gjcourt/packages/container/overture/versions --jq '.[0].metadata.container.tags[]'
```

## Workflow

1. Branch from `master` (not from a previous fix branch)
2. Commit changes, push, open a PR
3. Merge to `master` — Flux reconciles within the `interval` defined on each Kustomization (default: 10m)
4. To force immediate reconciliation: `flux reconcile kustomization <name> -n flux-system`

## Debugging Flux

```bash
# Top-level kustomization health
flux get kustomizations -A

# HelmRelease failures
kubectl describe helmrelease <name> -n <namespace>

# Force reconcile after a stalled HelmRelease
flux reconcile helmrelease <name> -n <namespace> --reset
```

### Common failure patterns

| Symptom | Cause | Fix |
|---------|-------|-----|
| `HelmRelease status: 'Failed'` blocking kustomization | Immutable StatefulSet field in chart upgrade | Delete the StatefulSet, `flux reconcile helmrelease --reset`. Add `upgrade.remediation.remediationStrategy: uninstall` to the HelmRelease. |
| `dependency 'X' is not ready` | Upstream kustomization stalled | Fix the upstream kustomization first |
| HA enters recovery mode | 0-byte include file (automations.yaml etc.) | Init container must write `[]`, not `touch` |

## Secrets

SOPS + age encryption. Key ref: `.sops.yaml`. Encrypt secrets before committing — never commit plaintext secrets.
