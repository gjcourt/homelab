# Staging workflow

How the staging environment works in this repo, from PR creation through deployment.

## Overview

Staging is an **automatic preview environment**. Every open PR that passes CI is merged into the `staging` branch and deployed to the staging namespaces in the cluster. This lets you validate changes in a real cluster before merging to `master` (production).

```
Feature branch → PR → CI passes → auto-merged into staging branch → Flux deploys to staging namespaces
                                   PR merged to master → Flux deploys to production namespaces
```

## How it works

### 1. The staging branch

The `staging` branch is **not a normal long-lived branch**. It is rebuilt from scratch by CI on every trigger:

1. Start from `master` (clean slate)
2. Find all open PRs targeting `master`
3. For each PR where all CI checks pass (no failures, no pending): merge it in
4. Force-push the result as the new `staging` branch

This means the `staging` branch is always `master` + all passing PRs combined. **Never push to `staging` manually** — CI will overwrite it.

### 2. CI workflow

The rebuild is handled by `.github/workflows/staging-deploy.yaml`:

| Trigger | Purpose |
|---|---|
| `check_suite` completed | Pick up newly-passing PRs |
| `pull_request` opened/updated/closed | React to PR changes immediately |
| Every 5 minutes (cron) | Safety net for missed events |
| `workflow_dispatch` | Manual rebuild |

The workflow uses `concurrency: staging-deploy` to prevent parallel runs from racing.

**Conflict handling**: If a PR conflicts with another during the merge, it is skipped (not failed). The workflow logs which PRs were merged and which were skipped in the GitHub Actions summary.

**Self-exclusion**: The `sync-staging` check filters itself out of PR status checks to avoid a chicken-and-egg problem where `staging-deploy` would always be "pending".

### 3. Flux deployment

Two `GitRepository` resources exist in the cluster:

| Resource | Branch | Used by |
|---|---|---|
| `flux-system` | `master` | `apps-production`, `infra-controllers`, `infra-configs` |
| `flux-system-staging` | `staging` | `apps-staging` |

The staging GitRepository polls every 1 minute (`spec.interval: 1m0s`), defined in `clusters/melodic-muse/repo-staging.yaml`.

When the staging branch is force-pushed, Flux detects the new commit and reconciles `apps-staging`, deploying the combined changes to staging namespaces.

### 4. Namespace separation

Staging and production apps run in separate namespaces on the same cluster. Staging namespaces use a `-stage` suffix (e.g., `authelia-stage`, `immich-stage`). Each environment's Kustomize overlay patches the namespace accordingly.

## Lifecycle of a change

1. **Create a feature branch** from `master`
2. **Push and open a PR** against `master`
3. **CI runs** (lint, flux-test, etc.)
4. **Once CI passes**, the staging workflow merges your PR into the `staging` branch automatically
5. **Flux deploys** the staging branch to staging namespaces (within ~1-2 minutes)
6. **Validate** your change in the staging environment
7. **Merge PR to `master`** — Flux deploys to production
8. **Staging rebuilds** — your PR is removed from staging (it's now in master, so it's included by default)

## Debugging staging

### Check what's on the staging branch

```bash
# See which PRs are merged into staging
git log --oneline origin/master..origin/staging

# Check Flux's view of the staging source
kubectl get gitrepository -n flux-system flux-system-staging
```

### Force a staging rebuild

Trigger the workflow manually:

```bash
gh workflow run staging-deploy.yaml
```

Or wait up to 5 minutes for the cron trigger.

### Check Flux reconciliation

```bash
# Staging kustomization status
flux get kustomization apps-staging

# Force reconcile
flux reconcile source git flux-system-staging
flux reconcile kustomization apps-staging
```

### Common issues

| Symptom | Cause | Fix |
|---|---|---|
| PR not in staging | CI checks still pending or failing | Fix CI failures; staging rebuilds automatically |
| Staging has stale code | Workflow run failed or was skipped | Trigger manual rebuild via `gh workflow run` |
| Merge conflict in staging build | Two PRs modify the same files | One PR will be skipped; resolve conflicts in the PRs |
| Staging namespace not updating | Flux hasn't reconciled yet | `flux reconcile source git flux-system-staging` |

## Key files

| File | Purpose |
|---|---|
| `.github/workflows/staging-deploy.yaml` | CI workflow that rebuilds the staging branch |
| `clusters/melodic-muse/repo-staging.yaml` | `GitRepository` for the staging branch |
| `clusters/melodic-muse/apps-staging.yaml` | Flux `Kustomization` applying `./apps/staging` from the staging GitRepository |
| `apps/staging/kustomization.yaml` | Kustomize entrypoint listing which apps are deployed to staging |
