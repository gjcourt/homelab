---
title: Flux debugging — common patterns
status: Stable
created: 2026-05-02
updated: 2026-05-04
updated_by: gjcourt
tags: [operations, flux, debugging, runbook]
---

# Flux debugging — common patterns

## Inspecting state

```bash
# Top-level kustomization health
flux get kustomizations -A

# HelmRelease failures
kubectl describe helmrelease <name> -n <namespace>

# Force reconcile after a stalled HelmRelease
flux reconcile helmrelease <name> -n <namespace> --reset

# Recent events in a namespace
kubectl -n <ns> get events --sort-by=.lastTimestamp | tail -n 50

# Force production reconcile after merging to master
flux reconcile kustomization apps-production -n flux-system

# Force staging git source refresh
flux reconcile source git flux-system-staging
flux reconcile kustomization apps-staging -n flux-system
```

## Common failure patterns

| Symptom | Cause | Fix |
|---|---|---|
| `HelmRelease status: 'Failed'` blocking kustomization | Immutable StatefulSet field in chart upgrade | Delete the StatefulSet, `flux reconcile helmrelease --reset`. Add `upgrade.remediation.remediationStrategy: uninstall` to the HelmRelease. |
| `timeout waiting for: [StatefulSet/X status: 'InProgress']` | Helm install/upgrade timed out waiting for StatefulSet rollout | `flux reconcile helmrelease <name> -n <namespace> --reset` — clears the failure state and retries. |
| `StatefulSet/X dry-run failed (Invalid): spec: Forbidden: updates to statefulset spec for fields other than…` | Immutable field changed on a Flux-managed StatefulSet — most commonly `volumeClaimTemplates` storage size | `kubectl delete statefulset <name> -n <ns> --cascade=orphan` (pods keep running), then `flux reconcile kustomization <name> -n flux-system`. Flux recreates the StatefulSet with the new spec; existing PVCs are unaffected and retain their original size. |
| `dependency 'X' is not ready` | Upstream kustomization stalled | Fix the upstream kustomization first. Note: `infra-controllers` failure cascades to `infra-configs` → `apps-production` → `apps-staging`. |
| HA enters recovery mode | 0-byte include file (`automations.yaml` etc.) | Init container must write `[]`, not `touch`. |
| PR not appearing in staging | CI checks pending or failing | Fix CI failures; staging rebuilds automatically once CI is green. |
| Staging has stale code | Staging workflow run failed | `gh workflow run staging-deploy.yaml`. |

## Staging environment

Staging is an automatic preview environment. CI rebuilds the `staging` branch from `master` + all open PRs with passing checks on every trigger (PR events, check completions, cron every 5 min).

```
Feature branch → PR → CI passes → auto-merged into staging → Flux deploys to staging namespaces
                                   PR merged to master → Flux deploys to production
```

Useful commands:

```bash
# See what PRs are currently merged into staging
git log --oneline origin/master..origin/staging

# Force a staging rebuild
gh workflow run staging-deploy.yaml
```

See [`staging-workflow.md`](staging-workflow.md) for the full workflow detail.

## Rollback

Revert the commit on a branch, open a PR, and merge. Do not force-push to `master`.

```bash
git revert <commit>
git push origin <branch>
gh pr create ...
```
