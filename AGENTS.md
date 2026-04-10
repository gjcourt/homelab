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

## Branching and PR Workflow — ALWAYS REQUIRED

**Never commit or push directly to `master`.** All changes — including single-line fixes, hotfixes, and doc edits — must go through a branch and PR, unless the user explicitly says otherwise in the current message.

Before creating a branch, check for open PRs. If one exists, check whether it has been merged before adding new commits:

```bash
gh pr list --repo gjcourt/homelab
```

**Workflow:**
1. `git checkout master && git pull` — start from latest master
2. `git checkout -b <type>/<short-description>` — new branch, never reuse old fix branches
3. Commit changes and push the branch
4. Open a PR via `gh pr create` or the GitHub MCP tool
5. Merge the PR — Flux reconciles within the `interval` on each Kustomization (default: 10m)
6. To force immediate reconciliation after merge: `flux reconcile kustomization <name> -n flux-system --with-source`

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
| `HelmRelease status: 'Failed'` blocking kustomization | Immutable StatefulSet field in chart upgrade | Delete the StatefulSet, `flux reconcile helmrelease --reset`. Add `upgrade.remediation.strategy: uninstall` to the HelmRelease (`strategy`, not `remediationStrategy`). |
| `dependency 'X' is not ready` | Upstream kustomization stalled | Fix the upstream kustomization first |
| HA enters recovery mode | 0-byte include file (automations.yaml etc.) | Init container must write `[]`, not `touch` |

## Secrets

SOPS + age encryption. Key ref: `.sops.yaml`. Encrypt secrets before committing — never commit plaintext secrets.
