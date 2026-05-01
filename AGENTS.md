> Claude Code users: this file is also referenced from [CLAUDE.md](./CLAUDE.md).

# Homelab GitOps Guidelines

## Source of truth

| Topic | Location |
|---|---|
| App base manifests | `apps/base/<app>/` |
| Staging overlays | `apps/staging/<app>/` |
| Production overlays | `apps/production/<app>/` |
| Cluster Flux entrypoints | `clusters/melodic-muse/` |
| Infra operators (HelmReleases) | `infra/controllers/` |
| Infra config (networking, storage) | `infra/configs/` |
| App runbooks | `docs/apps/` |
| Architecture decisions | `docs/architecture/` |
| Incident reports | `docs/incidents/` |
| Operational guides | `docs/guides/` |
| Active plans | `docs/plans/` |

## Architecture

Flux CD (GitOps) cluster for a single-node Talos Kubernetes cluster (`melodic-muse`). All cluster state is driven from Git — changes take effect when merged to `master` and reconciled by Flux.

Directory layout:
- `apps/base/` — base Kustomize resources for each app (env-agnostic)
- `apps/staging/` / `apps/production/` — environment overlays (namespace, resource patches, env-specific config)
- `infra/controllers/` — HelmReleases and supporting config (monitoring, CNI, CSI, etc.)
- `infra/configs/` — cluster configuration that controllers depend on (IP pools, cert issuers, etc.)
- `clusters/melodic-muse/` — Flux Kustomization entrypoints

Flux reconciliation order: `infra-crds` → `infra-controllers` → `infra-configs` → `apps-production` / `apps-staging`

## Non-negotiables

- **Never commit directly to `master` or `staging`** — all changes go through a branch and PR
- **Never push to `staging` manually** — the `staging` branch is rebuilt from scratch by CI on every trigger; a manual push will be overwritten and may break in-flight deployments
- **Never commit plaintext secrets** — all secrets must be encrypted with SOPS before committing; use `sops -e -i <file>` in place
- **Never bypass the staging environment for production changes** — open a PR, let CI merge it to staging, validate, then merge to `master`

## Branch and PR workflow

**You must use a branch and PR for every change — never commit directly to `master`.**

1. Check for open PRs first — unmerged changes on a stale branch will not be reconciled

   ```bash
   gh pr list --repo gjcourt/homelab
   ```

2. Branch from `master` (not from another feature or fix branch)

3. Validate locally before pushing:

   ```bash
   kustomize build apps/staging          # or a specific overlay
   kustomize build infra/controllers
   ```

4. Commit, push, and open a PR against `master`

5. Once CI passes, the staging workflow automatically merges the PR into the `staging` branch and Flux deploys it to staging namespaces — validate your change there

6. Get explicit approval, then merge to `master` — Flux reconciles production within the `interval` on each Kustomization (default: 10m)

7. To force immediate reconciliation after merge:

   ```bash
   flux reconcile kustomization apps-production -n flux-system
   ```

## PR checklist

Before opening a PR:

- [ ] `kustomize build` passes for all affected overlays
- [ ] No plaintext secrets committed (`git diff HEAD | grep -i "password\|secret\|key"`)
- [ ] Image tags are strictly increasing (never rolling back without explicit intent)
- [ ] New apps are added to the correct env `kustomization.yaml` entrypoints
- [ ] Namespace follows the convention: unsuffixed for production, `-stage` suffix for staging
- [ ] If adding a new CNPG cluster: iSCSI PVC provisioned and StorageClass correct
- [ ] Docs updated if the change affects a runbook or architecture doc

## Staging environment

Staging is an automatic preview environment. CI rebuilds the `staging` branch from `master` + all open PRs with passing checks on every trigger (PR events, check completions, cron every 5 min).

```
Feature branch → PR → CI passes → auto-merged into staging → Flux deploys to staging namespaces
                                   PR merged to master → Flux deploys to production
```

Key commands:

```bash
# See what PRs are currently merged into staging
git log --oneline origin/master..origin/staging

# Force a staging rebuild
gh workflow run staging-deploy.yaml

# Staging reconciliation
flux reconcile source git flux-system-staging
flux reconcile kustomization apps-staging -n flux-system
```

See [docs/guides/staging-workflow.md](docs/guides/staging-workflow.md) for full details.

## Image versioning

CI tags images as `YYYY-MM-DD` for the first build of the day, then `YYYY-MM-DD-N` (N=1,2,…) for subsequent builds. **Every push to `main` in the app repo triggers a build**, so tag numbers are not sequential relative to deploys — docs-only commits consume slots too.

When bumping an image tag in `deployment.yaml`, the new tag must be strictly greater than the currently deployed one. Never roll back to an earlier tag without an explicit rollback intent. To look up the latest published tag:

```bash
gh api /users/gjcourt/packages/container/overture/versions --jq '.[0].metadata.container.tags[]'
```

## Adding a new app

1. Create `apps/base/<app>/kustomization.yaml` and base manifests
2. Create overlays under `apps/staging/<app>/` and/or `apps/production/<app>/`
3. Add the app to `apps/staging/kustomization.yaml` and/or `apps/production/kustomization.yaml`
4. Update the apps list: `./scripts/update-apps-readme.sh`
5. Namespace convention: production uses plain name, staging uses `-stage` suffix

## Rollback

Revert the commit on a branch, open a PR, and merge. Do not force-push to `master`.

```bash
git revert <commit>
git push origin <branch>
gh pr create ...
```

## Debugging Flux

```bash
# Top-level kustomization health
flux get kustomizations -A

# HelmRelease failures
kubectl describe helmrelease <name> -n <namespace>

# Force reconcile after a stalled HelmRelease
flux reconcile helmrelease <name> -n <namespace> --reset

# Recent events in a namespace
kubectl -n <ns> get events --sort-by=.lastTimestamp | tail -n 50
```

### Common failure patterns

| Symptom | Cause | Fix |
|---|---|---|
| `HelmRelease status: 'Failed'` blocking kustomization | Immutable StatefulSet field in chart upgrade | Delete the StatefulSet, `flux reconcile helmrelease --reset`. Add `upgrade.remediation.remediationStrategy: uninstall` to the HelmRelease. |
| `dependency 'X' is not ready` | Upstream kustomization stalled | Fix the upstream kustomization first |
| HA enters recovery mode | 0-byte include file (automations.yaml etc.) | Init container must write `[]`, not `touch` |
| PR not appearing in staging | CI checks pending or failing | Fix CI failures; staging rebuilds automatically |
| Staging has stale code | Staging workflow run failed | `gh workflow run staging-deploy.yaml` |

## Secrets

SOPS + age encryption. Key ref: `.sops.yaml`. Encrypt secrets before committing — never commit plaintext secrets.

```bash
# Encrypt in place
sops -e -i <secret-file.yaml>

# Decrypt to inspect (do not commit the decrypted form)
sops -d <secret-file.yaml>
```
