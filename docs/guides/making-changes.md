# Making changes

All changes go through a branch and PR — never commit directly to `master` or `staging`.

## Day-to-day loop

1. Branch from `master`

   ```bash
   git checkout master && git pull
   git checkout -b <your-branch>
   ```

2. Make changes in `apps/`, `infra/`, `docs/`, or `scripts/`

3. Validate locally:

   ```bash
   kustomize build apps/staging          # or the specific overlay you changed
   kustomize build infra/controllers
   ```

4. Commit, push, and open a PR against `master`

   ```bash
   git push origin <your-branch>
   gh pr create --title "..." --body "..."
   ```

5. Once CI passes, the staging workflow automatically deploys the PR to staging namespaces — validate your change there

6. Merge to `master` after approval — Flux reconciles production within ~10 minutes

7. Force reconcile if you don't want to wait:

   ```bash
   flux reconcile kustomization apps-production -n flux-system
   ```

## Secrets

Secrets are encrypted with SOPS and decrypted in-cluster via the `sops-agekey` secret referenced by Flux Kustomizations. Never commit a plaintext secret.

```bash
# Encrypt in place before committing
sops -e -i <secret-file.yaml>

# Decrypt to inspect (do not commit the decrypted form)
sops -d <secret-file.yaml>
```

## Adding a new app

1. Create `apps/base/<app>/` with a `kustomization.yaml` and base manifests
2. Create overlays under `apps/staging/<app>/` and/or `apps/production/<app>/`
3. Add the app to `apps/staging/kustomization.yaml` and/or `apps/production/kustomization.yaml`
4. Update the apps list: `./scripts/update-apps-readme.sh`
5. Namespace convention: production uses plain name, staging uses `-stage` suffix

## Rollback

Revert on a branch and PR it in — do not force-push to `master`.

```bash
git revert <commit>
git push origin <revert-branch>
gh pr create ...
```
