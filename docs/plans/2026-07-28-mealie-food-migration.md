---
status: in-progress
last_modified: 2026-07-28
summary: "Rename Mealie mealie.burntbytes.com -> food.burntbytes.com (301 old->new, LAN-only) and fix Site Settings: v3.22.0, BASE_URL, SMTP email, OIDC redirect URIs"
---

# Migrate Mealie to `food.burntbytes.com` + fix Site Settings

## Context

Mealie (recipe manager) is served at `mealie.burntbytes.com`; we're moving it to a cleaner
`food.burntbytes.com` and clearing the health warnings on its Site Settings page: out-of-date
version (v3.19.2 → v3.22.0), unset `BASE_URL` (breaks server-generated email/notification links),
and no SMTP config ("Email Not Ready"). LDAP is unused (ignorable); OIDC is healthy but its redirect
URIs embed the old hostname and must move with the rename.

Decisions: old host gets a **301 redirect** to the new one; app stays **LAN-only** (already covered by
the `*.burntbytes.com` wildcard gateway listener, wildcard TLS cert, and AdGuard wildcard — no
gateway/cert/DNS/tunnel changes). Internal names (`mealie-prod` namespace, PVC, repo dirs) stay
`mealie`; only the public hostname changes, so there is **no data/namespace migration**.

## Changes

Split into two PRs so the version bump can be validated on staging independently of the rename.

**PR A — version bump** (`chore/mealie-v3.22.0`): `apps/base/mealie/deployment.yaml` image
v3.19.2 → v3.22.0 (digest-pinned). Alembic auto-migrates SQLite on startup; `Recreate` = brief downtime.

**PR B — hostname + config** (`feat/mealie-food-migration`):
- `apps/production/mealie/httproute.yaml` — serve `food.burntbytes.com`; new `mealie-redirect` route
  301s `mealie.burntbytes.com` → `food` (Gateway API `RequestRedirect` with `hostname:`).
- `apps/production/mealie/configmap-env.yaml` (new) — `BASE_URL=https://food.burntbytes.com` + non-secret
  SMTP (`smtp.gmail.com:587`, `SMTP_AUTH_STRATEGY=TLS`, `SMTP_FROM_*`), mirroring the fleet Gmail pattern.
- `apps/production/mealie/secret-smtp.yaml` (SOPS, operator-created from `.example`) — `SMTP_USER` +
  `SMTP_PASSWORD` (the shared Gmail app password). Wired via `deployment-patch.yaml` `envFrom` +
  `kustomization.yaml`; CI fails until encrypted — the intended gate.
- `apps/production/authelia/configuration.yaml` — add `food.burntbytes.com/login[?direct=1]` to the mealie
  client `redirect_uris` (keep the old two during transition; requires an Authelia rollout restart).
- `apps/production/homepage/config/services.yaml` — tile → `food.burntbytes.com`.
- Staging parity: `apps/staging/mealie/{configmap-env,secret-smtp.yaml.example,deployment-patch,kustomization}`
  get `BASE_URL` (stage host) + SMTP; staging hostname is **not** renamed.
- `docs/operations/apps/mealie.md` updated (URLs, `truenas-iscsi`, new config/secrets, backup notes).

## Operator steps (SOPS is operator-only)
1. Before merging PR A: Mealie Admin → Backups export + a TrueNAS ZFS snapshot of `mealie-data-pvc`.
2. For PR B: `cp secret-smtp.yaml.example secret-smtp.yaml`, paste the Gmail app password into
   `SMTP_PASSWORD`, `sops -e -i secret-smtp.yaml`, commit — for **both** `apps/production/mealie/` and
   `apps/staging/mealie/`.
3. After merge: `flux reconcile kustomization apps-production --with-source -n flux-system`;
   `kubectl -n authelia rollout restart deploy/authelia` (load new redirect URIs).

## Verification
- `kustomize build apps/production` and `apps/staging` pass once the secrets are encrypted.
- Staging on v3.22.0 healthy first; then prod pod Running, `/api/app/about` = v3.22.0.
- `https://food.burntbytes.com` loads on-LAN; **OIDC login via Authelia succeeds on the new host**.
- `https://mealie.burntbytes.com/...` → **301 → food** (path preserved).
- Site Settings: Server Side Base URL ✓, Email ✓ Ready, in-app **Test** email delivers; version ✓.
- Homepage tile → `food.burntbytes.com`, uptime monitor green.

## Cleanup (follow-up PR, after cutover confirmed)
Remove the old `mealie.burntbytes.com` `redirect_uris` from Authelia. Keep the 301 route as long as desired.
