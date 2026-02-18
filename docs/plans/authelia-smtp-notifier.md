# Plan: Authelia SMTP notifier

## Status: Not started

## Problem

Authelia currently uses `notifier.filesystem`, which writes verification codes
to a file on disk instead of sending real emails. This requires manually running
`kubectl exec` to retrieve codes for TOTP enrollment, password resets, etc.

## Goal

Replace `notifier.filesystem` with `notifier.smtp` so Authelia sends real emails.

## Approach

### 1. Choose an SMTP provider

Options:

| Provider | Cost | Notes |
|---|---|---|
| Gmail app password | Free | Use a Google account with an [app-specific password](https://myaccount.google.com/apppasswords). Sender appears as your Gmail address. 500 emails/day limit. |
| Mailgun | Free tier (100/day) | Dedicated sending domain, better deliverability. |
| Sendgrid | Free tier (100/day) | Similar to Mailgun. |
| Self-hosted (Postfix) | Free | Full control but requires DNS (SPF/DKIM/DMARC) setup and maintenance. |

**Recommended for homelab**: Gmail app password. Simplest, no extra infra.

### 2. Create SMTP secret

Add SMTP credentials to the existing `authelia-secrets` in both staging and
production overlays. Required keys:

```yaml
AUTHELIA_NOTIFIER_SMTP_PASSWORD: "<app-password-or-smtp-password>"
```

Encrypt with SOPS as usual.

### 3. Update configuration

Replace in base (or overlay patches):

```yaml
# Before
notifier:
  filesystem:
    filename: /config/notification.txt

# After (example for Gmail)
notifier:
  smtp:
    address: submissions://smtp.gmail.com:465
    username: yourname@gmail.com
    sender: "Authelia <yourname@gmail.com>"
    subject: "[Authelia] {title}"
    disable_require_tls: false
```

The password is injected via the `AUTHELIA_NOTIFIER_SMTP_PASSWORD` environment
variable from the secret.

### 4. Test

1. Deploy to staging first
2. Trigger a TOTP enrollment or password reset
3. Confirm email arrives in inbox
4. Roll out to production

## Files to change

- `apps/base/authelia/configmap.yaml` (or overlay patches) — notifier block
- `apps/staging/authelia/secret-authelia.yaml` — add SMTP password
- `apps/production/authelia/secret-authelia.yaml` — add SMTP password
- `apps/staging/authelia/kustomization.yaml` — update notifier in config patch
- `apps/production/authelia/kustomization.yaml` — update notifier in config patch
