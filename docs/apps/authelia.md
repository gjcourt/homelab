# Authelia (SSO / OIDC)

This repo deploys **Authelia** as a self-hosted SSO provider (OIDC issuer) that you can use with apps that support OIDC/OAuth2.

## URLs

- Staging: `https://auth.stage.burntbytes.com`
- Production: `https://auth.burntbytes.com`

## What gets deployed

- Base app: [apps/base/authelia/](../apps/base/authelia/)
- Overlays:
  - Staging: [apps/staging/authelia/](../apps/staging/authelia/)
  - Production: [apps/production/authelia/](../apps/production/authelia/)

Authelia uses sqlite storage on a PVC (`authelia-data`) for now.

## Required secrets

Authelia won’t be usable until you provide:

- `authelia-secrets` (session/storage/OIDC HMAC secrets)
- `authelia-users` (file-based users database)

These are committed as SOPS-managed secrets in the overlays:

- Staging:
  - [apps/staging/authelia/secret-authelia.yaml](../apps/staging/authelia/secret-authelia.yaml)
  - [apps/staging/authelia/secret-users.yaml](../apps/staging/authelia/secret-users.yaml)
- Production:
  - [apps/production/authelia/secret-authelia.yaml](../apps/production/authelia/secret-authelia.yaml)
  - [apps/production/authelia/secret-users.yaml](../apps/production/authelia/secret-users.yaml)

### Generating secrets

Generate strong random values (examples):

- `openssl rand -hex 32` (JWT / session / OIDC HMAC)
- `openssl rand -hex 32` or longer (storage encryption key)

Then edit the relevant secret YAML and re-encrypt:

- `sops -e -i apps/staging/authelia/secret-authelia.yaml`
- `sops -e -i apps/production/authelia/secret-authelia.yaml`

### Creating a user

Authelia’s file backend needs password hashes (recommended: argon2id).

One way to generate a hash locally:

- `docker run --rm -it authelia/authelia:4.38.19 authelia crypto hash generate argon2 --password 'CHOOSE_A_PASSWORD'`

Put the resulting hash into `users.yml` in the appropriate secret file (staging/prod) and re-encrypt with SOPS.

## Adding OIDC clients

OIDC clients live in the Authelia configuration (currently `clients: []`). To add a client, update the env overlay patch in:

- [apps/staging/authelia/kustomization.yaml](../apps/staging/authelia/kustomization.yaml)
- [apps/production/authelia/kustomization.yaml](../apps/production/authelia/kustomization.yaml)

and re-encrypt any client secrets you store in Kubernetes Secrets.

## Future: forward-auth

For apps that do **not** support OIDC, Authelia can also be used as a reverse-proxy “forward auth” provider. If you want that next, we can add Gateway API auth filters / middleware patterns per app.
## Notifications (2FA codes, password resets)

Authelia uses a **notifier** to send verification codes needed for TOTP enrollment, password resets, and other identity verification flows.

### Current: filesystem notifier (development mode)

Both staging and production use `notifier.filesystem`, which writes "emails" to a file inside the pod instead of sending real emails. This is a development convenience — **no actual email is sent**.

To retrieve a one-time code:

```bash
# Staging
kubectl exec -n authelia-stage deploy/authelia -- cat /config/notification.txt

# Production
kubectl exec -n authelia-prod deploy/authelia -- cat /config/notification.txt
```

The file contains the most recent notification only. Trigger the action in the browser, then run the command above to get the code.

### Future: SMTP notifier

To enable real email delivery, replace the `notifier.filesystem` block in the configuration with `notifier.smtp`. See [SMTP setup plan](../plans/authelia-smtp-notifier.md) for details.