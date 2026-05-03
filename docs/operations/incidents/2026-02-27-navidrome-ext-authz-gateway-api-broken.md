# Incident: Navidrome ext-authz Not Intercepting Requests (Cilium 1.18 Gateway API Limitation)

**Date**: 2026-02-27  
**Severity**: Medium — Navidrome accessible without Authelia authentication  
**Status**: Ongoing — root cause confirmed as Cilium 1.18 architectural limitation; no resolution yet  

---

## Summary

Navidrome (production `music.burntbytes.com`) was configured to use Authelia via `ND_EXTAUTH_USERHEADER=Remote-User`, expecting Authelia's `ext-authz` Envoy filter to inject authenticated user headers before the request reaches Navidrome. However, the `CiliumClusterwideEnvoyConfig` (CCEC) responsible for injecting this filter was **never functioning** — Navidrome has been accessible without authentication since the CCEC was added on 2026-02-23.

---

## Timeline

| Date | Event |
|---|---|
| 2026-02-23 | CCEC `cilium-gateway-authelia-ext-authz-production` added (commit `d6ca09b`) intending to inject `ext_authz` into gateway listener |
| 2026-02-25 | PR #190 merged; `allowed_client_headers` updated to include `location` and `www-authenticate` — but the filter was never injecting |
| 2026-02-27 | User reports Navidrome still shows native login screen |
| 2026-02-27 | `curl https://music.burntbytes.com/app/` returns `200` — Authelia not intercepting |
| 2026-02-27 | Envoy config dump confirms: no `ext_authz` in gateway listener HTTP filter chain |
| 2026-02-27 | Cilium agent logs show: `"cache unmodified by transaction; aborting"` for every Listener transaction |
| 2026-02-27 | Investigation confirms: Cilium 1.18 Gateway API listener injection is broken regardless of `listener:` field value |

---

## Root Cause

### Background: Cilium CCEC Filter Injection

`CiliumClusterwideEnvoyConfig` can inject HTTP filters (like `ext_authz`) into existing Envoy listeners by specifying a `services[].listener` field. Cilium merges the CCEC resources into the matching listener's HTTP filter chain. This works correctly for Cilium-managed service listeners (e.g., for Kubernetes Services).

### What Failed

For **Gateway API** listeners in Cilium 1.18, the mechanism is broken:

1. **Single combined listener**: In Cilium 1.15+, the Gateway API controller creates a **single** Envoy listener named `listener` (registered in the xDS cache as `default/cilium-gateway-app-gateway-production/listener`) covering all `sectionNames` (HTTP + HTTPS). The old `listener: https` field value from the original CCEC matched a listener name that no longer exists.

2. **Cache transaction always unmodified**: Even after removing the `listener:` field or setting it to `listener`, Cilium's internal xDS cache records the merge attempt but always produces "cache unmodified by transaction" for the Listener type. The auto-generated CEC's listener is not modified by CCEC resources.

3. **CCEC creates its own listener instead**: When a `Listener` resource is defined in the CCEC, Cilium creates a *new* Envoy listener (named `/cilium-gateway-authelia-ext-authz-production/listener`) on a different port — it does *not* update the existing gateway listener.

### Evidence

```bash
# Envoy listener HTTP filter chain — NO ext_authz present:
kubectl exec -n kube-system cilium-pxts2 -- cilium-dbg envoy admin config listeners \
  | grep '"name".*listener"'
# default/cilium-gateway-app-gateway-production/listener   ← real gateway (port 11254)
# /cilium-gateway-authelia-ext-authz-production/listener   ← CCEC phantom (port 17311)

# Cilium agent log per ~100ms:
# msg="cache unmodified by transaction; aborting"
# xdsTypeURL=type.googleapis.com/envoy.config.listener.v3.Listener
```

### Approaches Attempted (All Failed)

| Approach | Result |
|---|---|
| `listener: https` (original) | No match — listener doesn't exist |
| `listener: listener` | "cache unmodified" — listener reference found but merge fails |
| No `listener:` field (like staging CCEC) | Filter goes into separate CCEC listener, not gateway listener |
| Standalone `ExtAuthz` resource + `listener: listener` | Same — "cache unmodified" |
| Full `Listener` resource in CCEC with `ext_authz` | Creates a new phantom listener on a different port |
| Namespaced `CiliumEnvoyConfig` (not CCEC) | Same behavior |

---

## Impact

- **Navidrome (prod)**: Accessible without Authelia authentication since 2026-02-23
- **Navidrome (staging)**: Same (staging CCEC also non-functional)
- The Authelia access control rule for `music.burntbytes.com` (`one_factor` policy) is being bypassed entirely
- `ND_EXTAUTH_USERHEADER` / `ND_EXTAUTH_TRUSTEDSOURCES` settings on Navidrome are ineffective since no headers are ever injected

---

## Current State

The CCEC files have been updated to remove the incorrect `listener: https` field and document the limitation. The filter injection does not work, but the configs are ready for when it is fixed.

---

## Mitigation Options

### Option 1: Navidrome native OIDC (Recommended)

Navidrome supports native OIDC via environment variables since v0.52. Configure Authelia as an OIDC provider directly:

```yaml
# Navidrome env vars
ND_SPOTIFY_ID: ""
ND_OIDC_ENABLED: "true"
ND_OIDC_CLIENTID: "navidrome"
ND_OIDC_CLIENTSECRET: "..."
ND_OIDC_DISCOVERYURL: "https://auth.burntbytes.com/.well-known/openid-configuration"
# Optional: auto-provision accounts on first OIDC login
ND_OIDC_AUTOCREATEONADMIN: "true"
```

This is independent of the Envoy ext-authz mechanism and would work reliably.

### Option 2: Wait for Cilium fix

Track [Cilium issue #32793](https://github.com/cilium/cilium/issues/32793) (or equivalent) for Gateway API ext-authz HTTP filter injection support. The current CCEC is ready to be activated without changes once the underlying mechanism works.

### Option 3: Use Authelia ForwardAuth with a sidecar

Deploy an nginx/caddy sidecar in the Navidrome pod that proxies requests and calls Authelia's forward-auth endpoint before forwarding to Navidrome. This avoids the Cilium limitation but adds operational complexity.

---

## Recommendations

1. **Implement Option 1 (native OIDC)** as the short-term fix. Add `navidrome` as an OIDC client in Authelia and configure `ND_OIDC_*` variables.
2. Keep the CCEC in place as documentation of intent — when Cilium fixes the injection mechanism, the config is ready.
3. Track the Cilium upstream issue and revisit once fixed.

---

## References

- [Cilium CiliumEnvoyConfig docs](https://docs.cilium.io/en/stable/network/servicemesh/envoy-config/)
- [Navidrome OIDC docs](https://www.navidrome.org/docs/usage/security/#openid-connect)
- [Authelia ext-authz integration](https://www.authelia.com/integration/proxies/envoy/)
- Commit `d6ca09b` — CCEC initially added
- PR #190 — attempted fixes
