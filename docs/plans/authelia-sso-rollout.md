---
status: in-progress
last_modified: 2026-02-27
---

# Authelia SSO Rollout Plan

## Objective
Enable Single Sign-On (SSO) across the homelab using Authelia as the OpenID Connect (OIDC) Provider. This moves apps from "No Auth" or "Basic Auth" to a unified, secure login flow.

## Current State
*   **Authelia**: Installed and running (Staging/Production) with OIDC identity_providers configured.
*   **Phase 1 & 2**: Complete. Immich OIDC working in both staging and production.
*   **Phase 3**: In progress. Mealie and Memos staging OIDC configured.

## Candidate Applications

| Application | Auth Method | Priority | Status |
| :--- | :--- | :--- | :--- |
| **Immich** | OIDC | High | ✅ Staging + Production |
| **Mealie** | OIDC (PKCE) | High | 🔄 Staging configured |
| **Memos** | OAuth2 | Medium | 🔄 Staging configured (UI setup required) |
| **Audiobookshelf** | OIDC | Medium | Not started |
| **Linkding** | OIDC / Proxy | Low | Not started |
| **Homepage** | OIDC / Header | Low | Not started |

## Implementation Plan

### Phase 1: Preparation ✅
1.  **Configure Authelia**: OIDC `identity_providers` block configured per-environment in staging/production overlay kustomizations.
2.  **Secrets**: Client secrets hashed with pbkdf2-sha512 and stored inline in Authelia config. Plaintext secrets in SOPS-encrypted secret files.

### Phase 2: First Mover (Immich) ✅
1.  **Authelia client**: Registered as confidential client with `token_endpoint_auth_method: client_secret_post`.
2.  **Immich config**: Uses `IMMICH_CONFIG_FILE` with init container to inject client_secret from SOPS secret.
3.  **DNS**: `hostAliases` added to resolve `auth.stage.burntbytes.com` / `auth.burntbytes.com` (CoreDNS can't resolve AdGuard DNS rewrites).
4.  **Tested**: Web + mobile app login flow working.

### Phase 3: Fast Followers (Mealie, Memos, Audiobookshelf, Linkding) — In Progress

#### Mealie (Staging & Production)
*   **Client type**: Public (PKCE, no client_secret needed).
*   **Authelia client**: `client_id: mealie`, `public: true`, scopes: `openid profile email groups`.
*   **Redirect URIs**: `https://mealie.stage.burntbytes.com/login`, `https://mealie.stage.burntbytes.com/login?direct=1`.
*   **Mealie config**: Environment variables via `configmap-oidc.yaml`:
    *   `OIDC_AUTH_ENABLED=true`
    *   `OIDC_CONFIGURATION_URL=https://auth.stage.burntbytes.com/.well-known/openid-configuration`
    *   `OIDC_CLIENT_ID=mealie`
    *   `OIDC_PROVIDER_NAME=Authelia`
    *   `OIDC_SIGNING_ALGORITHM=RS256`
    *   `OIDC_USER_CLAIM=email`
*   **DNS**: `hostAliases` for `auth.stage.burntbytes.com` → `192.168.5.30` (Staging) / `.33` (Prod).

#### Memos (v0.26.0)
*   **Client type**: Confidential (`token_endpoint_auth_method: client_secret_basic`).
*   **Authelia client**: `client_id: memos`, hashed secret in config.
*   **Redirect URI**: `https://memos.stage.burntbytes.com/auth/callback`.
*   **Memos config**: SSO is configured through the **admin UI** (Settings → SSO).
*   **DNS**: `hostAliases` for `auth.stage.burntbytes.com` → `192.168.5.30` (Staging) / `.33` (Prod).
*   **Secret**: `memos-sso-secret` contains the plaintext `client_secret`.
*   **Manual setup**: After deployment, configure identity provider in Memos admin UI:
    *   Name: `Authelia`
    *   Type: `OAuth2`
    *   Client ID: `memos`
    *   Client Secret: *(Retrieve from `memos-sso-secret` via `kubectl get secret memos-sso-secret -o go-template='{{.data.OIDC_CLIENT_SECRET | base64decode}}'`)*
    *   Authorization URL: `https://auth.stage.burntbytes.com/api/oidc/authorization`
    *   Token URL: `https://auth.stage.burntbytes.com/api/oidc/token`
    *   User Info URL: `https://auth.stage.burntbytes.com/api/oidc/userinfo`
    *   Scopes: `openid profile email`
    *   Identifier: `email`

#### Audiobookshelf
*   **Client type**: Confidential (`token_endpoint_auth_method: client_secret_post`).
*   **Authelia client**: `client_id: audiobookshelf`.
*   **Redirect URI**: `https://audiobooks.stage.burntbytes.com/auth/openid/callback`.
*   **ABS Config**: Configured via Admin UI.
*   **Manual setup**:
    1.  Log in as Admin.
    2.  Go to **Settings** -> **Auth** -> **OpenID Connect**.
    3.  Click **Add Provider**.
    4.  Issuer URL: `https://auth.stage.burntbytes.com` (Staging) or `https://auth.burntbytes.com` (Prod).
    5.  Client ID: `audiobookshelf`.
    6.  Client Secret: *(Retrieve from `audiobookshelf-sso-secret`)*.
    7.  Button Text: "Login with Authelia".
    8.  Auto Register: ON.

#### Linkding
*   **Client type**: Confidential (`token_endpoint_auth_method: client_secret_basic`).
*   **Authelia client**: `client_id: linkding`.
*   **Redirect URI**: `https://links.stage.burntbytes.com/oidc/callback/`.
*   **Linkding Config**: Configured via Environment Variables.
    *   `LD_OIDC_ENABLED=True`
    *   `LD_OIDC_PROVIDER_URL=https://auth.stage.burntbytes.com`
    *   `LD_OIDC_CLIENT_ID=linkding`
    *   `LD_OIDC_CLIENT_SECRET` (from `linkding-oidc-secret`)

### Phase 4: Complex Integrations
*   **Home Assistant**: Validating if OIDC or Trusted Header is better.
*   **Jellyfin**: Requires `jellyfin-plugin-sso`.
*   **Audiobookshelf**: Native OIDC support. Configuration via admin UI.

## Known Issues
*   **subPath ConfigMap mounts**: Authelia uses `subPath` to mount `configuration.yml`. ConfigMap updates via Flux do NOT propagate to pods using `subPath`. A pod restart is required after config changes. Consider adding a configmap hash annotation to automate restarts.
*   **CoreDNS + AdGuard**: Internal pods cannot resolve `*.burntbytes.com` or `*.stage.burntbytes.com`. All OIDC apps need `hostAliases` pointing to the gateway IP.

## Verification Steps
*   [x] User is redirected to `auth.burntbytes.com` (or `auth.stage.burntbytes.com`).
*   [x] Login is successful (2FA if configured).
*   [x] User is redirected back to the app and logged in as the correct user.
*   [ ] Mealie staging OIDC login flow tested.
*   [ ] Memos staging OIDC login flow tested.
*   [ ] Production parity for Mealie and Memos.
