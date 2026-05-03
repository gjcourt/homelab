---
title: Adding a new app
status: Stable
created: 2026-05-02
updated: 2026-05-02
updated_by: gjcourt
tags: [operations, kustomize, apps]
---

# Adding a new app

1. Create `apps/base/<app>/kustomization.yaml` and the base manifests.
2. Create environment overlays under `apps/staging/<app>/` and/or `apps/production/<app>/`.
3. Add the app to `apps/staging/kustomization.yaml` and/or `apps/production/kustomization.yaml`.
4. Update the auto-generated apps list:
   ```bash
   ./scripts/update-apps-readme.sh
   ```
5. Namespace convention: production uses the plain name (`<app>`); staging uses a `-stage` suffix (`<app>-stage`).
6. Validate before pushing:
   ```bash
   kustomize build apps/staging/<app>
   kustomize build apps/production/<app>
   ```
7. Open a PR against `master`. CI will build into the `staging` branch automatically; Flux will deploy to the `<app>-stage` namespace. Validate there before merging.

## Image naming

CI tags images as `YYYY-MM-DD` (first build of the day) then `YYYY-MM-DD-N` for subsequent builds. Image bumps in `apps/{staging,production}/<app>/deployment.yaml` must be strictly greater than the currently deployed tag.

To list published tags:

```bash
gh api /users/gjcourt/packages/container/<app>/versions --jq '.[0].metadata.container.tags[]'
```

## Per-app runbook

After the app is deployed, add an entry under `docs/operations/apps/<app>.md` with the standard runbook structure (overview, architecture, URLs, configuration, usage, monitoring, disaster recovery, troubleshooting). The template is documented in `docs/plans/2026-02-21-documentation-rewrite-plan.md`.
