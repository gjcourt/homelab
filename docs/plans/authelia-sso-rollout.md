# Authelia SSO Rollout Plan

## Objective
Enable Single Sign-On (SSO) across the homelab using Authelia as the OpenID Connect (OIDC) Provider. This moves apps from "No Auth" or "Basic Auth" to a unified, secure login flow.

## Current State
*   **Authelia**: Installed and running (Staging/Production).
*   **OIDC Config**: Currently empty (`identity_providers` block missing or minimal).
*   **Apps**: Most apps (Immich, Mealie, etc.) are running without SSO integration.

## Candidate Applications

| Application | Auth Method | Priority | Notes |
| :--- | :--- | :--- | :--- |
| **Immich** | OIDC | High | Native support, mobile app compatible. |
| **Mealie** | OIDC | High | Good native support. |
| **Memos** | OIDC | Medium | Native support. |
| **Audiobookshelf** | OIDC | Medium | Native support. |
| **Linkding** | OIDC / Proxy | Low | Can use proxy header or OIDC. |
| **Homepage** | OIDC / Header | Low | Dashboard authentication. |

## Implementation Plan

### Phase 1: Preparation
1.  **Configure Authelia**: Update `apps/base/authelia/configmap.yaml` to include the `identity_providers` block.
2.  **Generate Secrets**: Create random client secrets for each app. Store them in the secret manager (SOPS) and inject them into Authelia as env vars.
    *   Example Secret Key: `AUTHELIA_IDENTITY_PROVIDERS_OIDC_CLIENTS_0_CLIENT_SECRET` (Depending on how we inject config).
    *   *Alternative*: Mount a separate secret file for OIDC clients if env vars are too cumbersome.

### Phase 2: First Mover (Immich)
1.  **Register Client (Authelia)**:
    ```yaml
    - id: immich
      description: Immich
      secret: '$pbkdf2-sha512$...' # or env var
      redirect_uris:
        - https://photos.burntbytes.com/auth/login
        - app.immich:///oauth-callback
      scopes:
        - openid
        - profile
        - email
    ```
2.  **Configure Immich**:
    *   `OAUTH_ENABLED=true`
    *   `OAUTH_ISSUER_URL=https://auth.burntbytes.com`
    *   `OAUTH_CLIENT_ID=immich`
    *   `OAUTH_CLIENT_SECRET=...`
3.  **Test**: Verify login flow via web and mobile app.

### Phase 3: Fast Followers (Mealie, Memos)
*   **Mealie**: Set `OIDC_AUTHORITY`, `OIDC_CLIENT_ID`.
*   **Memos**: Configure via UI or Env vars.

### Phase 4: Complex Integrations
*   **Home Assistant**: Validating if OIDC or Trusted Header is better.
*   **Jellyfin**: Requires `jellyfin-plugin-sso`.

## Verification Steps
*   [ ] User is redirected to `auth.burntbytes.com`.
*   [ ] Login is successful (2FA if configured).
*   [ ] User is redirected back to the app and logged in as the correct user.
