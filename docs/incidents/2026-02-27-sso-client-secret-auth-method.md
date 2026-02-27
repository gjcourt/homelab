# Incident: SSO Broken for Memos and Linkding (client_secret_basic vs client_secret_post)

**Date**: 2026-02-27
**Severity**: Medium — SSO login broken for two apps; fallback local login still worked
**Status**: Resolved

---

## Summary

Single sign-on (SSO) via Authelia OIDC was broken for **memos** (production + staging) and **linkding** (production + staging). Users could not log in via the "Sign in with Authelia" button. Memos production also experienced a concurrent database read-only filesystem failure that prevented the identity provider API from responding at all.

---

## Timeline

| Time (approx.) | Event |
|---|---|
| 2026-02-23 | PR #190 opened to fix memos SSO (`client_secret_post`) and other unrelated fixes |
| 2026-02-25 | PR #190 merged (SHA `6c14977`), Flux reconciled |
| 2026-02-27 | User reports: memos still broken (staging no SSO button), linkding SSO still broken |
| 2026-02-27 | Investigation: confirmed linkding still has `client_secret_basic` in Authelia config (not fixed in PR #190) |
| 2026-02-27 | Investigation: memos prod DB read-only FS (`pq: could not open file "base/16385/16479": Read-only file system`) preventing API responses |
| 2026-02-27 | Fix: `token_endpoint_auth_method: client_secret_post` applied to linkding in both overlay configs |
| 2026-02-27 | Fix: memos production primary CNPG pod deleted to force fresh btrfs mount; all 3 DB pods recovered |

---

## Root Cause

### 1. Authelia `token_endpoint_auth_method` mismatch

Authelia requires each OIDC client to declare how it sends its credentials to the token endpoint:

- `client_secret_basic` — credentials sent in HTTP Basic Auth header
- `client_secret_post` — credentials sent in the request body (POST parameters)

**Memos** (`gitea-oauth2-helper` / native Go OAuth2 client) sends credentials via `client_secret_post`. This was fixed in PR #190.

**Linkding** uses [`mozilla-django-oidc`](https://github.com/mozilla/mozilla-django-oidc), which also defaults to `client_secret_post`. However, PR #190 only fixed memos, leaving linkding with `client_secret_basic`.

When Authelia receives a token request from linkding with credentials in the body but the config says `client_secret_basic`, it rejects with:

```
The registered client with id 'linkding' is configured to only support
'token_endpoint_auth_method' method 'client_secret_basic'
```

### 2. Memos production database read-only filesystem

A separate btrfs I/O fault caused the memos production CNPG primary pod's PV to remount read-only. This manifested as:

```
pq: could not open file "base/16385/16479": Read-only file system
```

This is the same class of failure documented in previous incidents. See [2026-02-08-pv-recovery.md](2026-02-08-pv-recovery.md) and [synology-iscsi-operations.md](../guides/synology-iscsi-operations.md).

---

## Impact

- **Linkding (prod + staging)**: OIDC login completely broken since initial SSO configuration. Local account login still worked.
- **Memos (prod)**: OIDC identity provider API returned 500 errors (read-only DB). Login page loaded but identity provider list failed. DB operations degraded.
- **Memos (staging)**: SSO button appeared missing from UI. The OIDC identity provider _was_ configured in the DB (confirmed via `/api/v1/identity-providers`); UI state was likely stale/cached. `token_endpoint_auth_method` was already correctly set to `client_secret_post` for staging.

---

## Fix

### Config change (GitOps)

In both `apps/production/authelia/configuration.yaml` and `apps/staging/authelia/configuration.yaml`, for the `linkding` client:

```yaml
# Before
token_endpoint_auth_method: client_secret_basic

# After
token_endpoint_auth_method: client_secret_post
```

### Memos prod DB recovery (imperative)

The btrfs volume on the primary pod had remounted read-only due to I/O errors on the iSCSI path. Fix: delete the primary pod so CNPG fails over to a replica and the primary gets recreated with a fresh mount:

```bash
# Confirm read-only btrfs
kubectl exec -n memos-prod memos-db-production-cnpg-v1-3 -- mount | grep postgresql/data
# → /dev/sdab on /var/lib/postgresql/data type btrfs (ro,...) ← read-only

# Delete primary pod — CNPG will fail over to a replica and recreate the primary
kubectl delete pod -n memos-prod memos-db-production-cnpg-v1-3

# Wait for recovery (~30s), then fix replicas that also had I/O errors
kubectl delete pod -n memos-prod memos-db-production-cnpg-v1-1 memos-db-production-cnpg-v1-2

# Verify all pods Running
kubectl get pods -n memos-prod
```

---

## Lessons Learned

1. **When fixing `token_endpoint_auth_method` for one app, audit ALL OIDC clients** to check for others with the same misconfiguration. Memos was fixed in PR #190 but linkding was overlooked.

2. **`client_secret_post` should be the default for new OIDC client registrations** in Authelia. Most Django-based apps (`mozilla-django-oidc`) and Go OAuth2 libraries use `POST` by default. Only apps that explicitly use HTTP Basic Auth (e.g., curl-style clients) need `client_secret_basic`.

3. **Btrfs I/O errors on iSCSI are recurring**. The same failure mode has now occurred for: HomeAssistant (Feb 2026), memos production (Feb 2026), HomeAssistant staging (Feb 2026). Consider investigating whether an iSCSI session stability improvement (e.g., TCP keepalives, multipath) reduces frequency.

---

## Affected Files

- `apps/production/authelia/configuration.yaml` — linkding `token_endpoint_auth_method` changed to `client_secret_post`
- `apps/staging/authelia/configuration.yaml` — same

## References

- [Authelia OIDC clients — token_endpoint_auth_method](https://www.authelia.com/configuration/identity-providers/openid-connect/clients/)
- [mozilla-django-oidc](https://github.com/mozilla/mozilla-django-oidc)
- PR #190 (partial fix for memos)
- [synology-iscsi-operations.md](../guides/synology-iscsi-operations.md)
