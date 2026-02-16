# Making changes

This is the lightweight workflow used for most changes in this repo.

## Day-to-day loop

1. Make a change in Git (apps/infra/docs/scripts).
2. Validate locally when practical:
   - `kustomize build apps/staging` (or a specific app overlay)
   - `kustomize build infra/controllers`
3. Commit and push.
4. Reconcile Flux (or wait for the interval).

## Secrets

Secrets are encrypted with SOPS and decrypted in-cluster via the `sops-agekey` key referenced by Flux Kustomizations.

Encrypt a secret in place:

- `sops -e -i <secret-file.yaml>`

Decrypt a secret to inspect:

- `sops -d <secret-file.yaml>`

## Adding a new app

1. Create `apps/base/<app>/` with a `kustomization.yaml`.
2. Create overlays under `apps/staging/<app>/` and/or `apps/production/<app>/`.
3. Add the app folder to `apps/staging/kustomization.yaml` and/or `apps/production/kustomization.yaml`.
4. Update the apps list doc:
   - Run `./scripts/update-apps-readme.sh`

## Rollback

- Revert the commit and push.
- Reconcile the relevant Flux Kustomization.
