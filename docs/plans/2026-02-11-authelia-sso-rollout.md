---
status: complete
last_modified: 2026-05-03
---

# Authelia SSO Rollout Plan

## Objective
Enable Single Sign-On (SSO) across the homelab using Authelia as the OpenID Connect (OIDC) Provider. This moves apps from "No Auth" or "Basic Auth" to a unified, secure login flow.

## Current State
All candidate applications have been configured. SSO is live in both staging and production.

## Candidate Applications

| Application | Auth Method | Priority | Status |
| :--- | :--- | :--- | :--- |
| **Immich** | OIDC | High | ✅ Staging + Production |
| **Mealie** | OIDC (PKCE) | High | ✅ Staging + Production |
| **Memos** | OAuth2 | Medium | ✅ Staging + Production (UI-configured) |
| **Audiobookshelf** | OIDC | Medium | ✅ Staging + Production (UI-configured) |
| **Linkding** | OIDC / Proxy | Low | ✅ Staging + Production |
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

### Phase 3: Fast Followers ✅

#### Mealie ✅
*   **Client type**: Public (PKCE, no client_secret needed).
*   **Config**: Environment variables via `configmap-oidc.yaml` + SOPS secret. `hostAliases` for DNS.
*   **Both staging and production**: fully wired via GitOps.

#### Memos ✅
*   **Client type**: Confidential (`token_endpoint_auth_method: client_secret_post`).
*   **Config**: SSO configured through the Memos **admin UI** (Settings → SSO) — Memos does not support env-var SSO configuration.
*   **Endpoints used**:
    *   Authorization: `https://auth.burntbytes.com/api/oidc/authorization`
    *   Token: `https://auth.burntbytes.com/api/oidc/token`
    *   User Info: `https://auth.burntbytes.com/api/oidc/userinfo`
    *   Scopes: `openid profile email`, Identifier: `email`
*   **Secret**: stored in SOPS-encrypted `secret-sso.yaml` per environment for reference.

#### Audiobookshelf ✅
*   **Client type**: Confidential (`token_endpoint_auth_method: client_secret_basic`).
*   **Config**: SSO configured through the ABS **admin UI** (Settings → Auth → OpenID Connect).
*   **Endpoints used**:
    *   Issuer URL: `https://auth.burntbytes.com`
    *   Auth URL: `https://auth.burntbytes.com/api/oidc/authorization`
    *   Token URL: `https://auth.burntbytes.com/api/oidc/token`
    *   User Info URL: `https://auth.burntbytes.com/api/oidc/userinfo`
    *   JWKS URL: `https://auth.burntbytes.com/jwks.json`
*   **Note**: ABS requires the JWKS URL explicitly (unlike most apps that derive it from the discovery document).
*   **Secret**: stored in SOPS-encrypted `secret-sso.yaml` per environment.

#### Linkding ✅
*   **Client type**: Confidential (`token_endpoint_auth_method: client_secret_post`).
*   **Config**: Environment variables via `configmap-oidc.yaml` + SOPS secret. `hostAliases` for DNS.
*   **Both staging and production**: fully wired via GitOps.

### Phase 4: Complex Integrations
*   **Home Assistant**: Not pursued — HA has its own auth model.
*   **Jellyfin**: Not pursued — requires third-party plugin, low value.

## Known Issues
*   **subPath ConfigMap mounts**: Authelia uses `subPath` to mount `configuration.yml`. ConfigMap updates via Flux do NOT propagate to pods using `subPath`. A pod restart is required after config changes. Consider adding a configmap hash annotation to automate restarts.
*   **CoreDNS + AdGuard**: Internal pods cannot resolve `*.burntbytes.com` or `*.stage.burntbytes.com`. All OIDC apps need `hostAliases` pointing to the gateway IP.

## Verification Steps
*   [x] User is redirected to `auth.burntbytes.com` (or `auth.stage.burntbytes.com`).
*   [x] Login is successful (2FA if configured).
*   [x] User is redirected back to the app and logged in as the correct user.
*   [x] Mealie staging + production OIDC login flow tested.
*   [x] Memos staging + production OIDC login flow tested.
*   [x] Linkding staging + production OIDC login flow tested.
*   [x] Audiobookshelf staging + production OIDC login flow tested.
