---
status: planned
last_modified: 2026-11-04
summary: "Upgrade Cilium 1.19.1 → 1.19.10 → 1.20.4 and gate one_factor hosts with Gateway API ExternalAuth (Authelia), replacing the CiliumClusterwideEnvoyConfig ext_authz filter that never injected"
---

# Cilium 1.20 and Gateway ExternalAuth — plan

**Plane:** HOMELAB-90 · **Scope:** `infra/controllers/cilium/`, `infra/crds/cilium/`, `infra/crds/gateway-api/`,
`infra/configs/gateway/`, the HTTPRoutes of the `one_factor` hosts, `docs/architecture/gateway-auth.md`

## Problem

The cluster runs Cilium 1.19.1, and Authelia's `one_factor` rules are not enforced on it. The
`ext_authz` filter in `infra/configs/gateway/cilium-envoy-config-{production,staging}.yaml` is never
injected into the Gateway listener. The file's own comment says so. The audit in HOMELAB-90
(2026-11-03) found four hosts with no login of their own that are served to anyone on the LAN or VPN:

- prometheus.
- alerts. (its silence API is open)
- ladder.
- finance., which was pulled off the gateway in #1801 as a stopgap.

None of them are on the Cloudflare tunnel.

Two corrections to what the repo currently says:

- The CCEC comment's `TODO` points at cilium/cilium#32793. That is an unrelated v1.15 `x/net` backport,
  not a tracking issue. As far as I could find, no upstream fix makes CCEC filter injection into
  Gateway API listeners work.
- `docs/architecture/gateway-auth.md` describes a global filter that gates these hosts. It does not.

## Decision

George, 2026-11-04: "let's upgrade cilium to get the gateway check working".

**Cilium 1.20 adds native Gateway API ExternalAuth (GEP-1494)**, so the upgrade is the fix. The auth
check becomes an `HTTPRoute` filter of `type: ExternalAuth` pointing at Authelia's
`/api/authz/ext-authz`. It no longer depends on a hand-written Envoy patch. It also inverts the old
design:

- Before: one global filter on every request, with Authelia defaulting to `bypass`.
- After: the filter is attached only to routes that need it.

So an Authelia outage only affects those routes.

## Versions

Current: chart and agent `1.19.1`.

Targets:
- Cilium **1.19.10** first, the latest patch on the current minor (released 2026-10-23).
- Then Cilium **1.20.4**, the latest 1.20 patch (2026-10-23).
- Gateway API CRDs go from **v1.5.0** to **v1.6.1 or later**, which Cilium 1.20 requires.

Upstream says to upgrade one minor at a time, and to take the latest patch of the current minor
first. 1.21 is still a pre-release (`1.21.0-pre.4`), so it is out of scope.

## Exposure check against the 1.20 upgrade notes

Checked on 2026-11-04 by grepping `apps/` and `infra/` and querying the live cluster:

| 1.20 action item | Us |
|---|---|
| Gateway API must be ≥ v1.6.1. In v1.6, `TLSRoute` moves to `v1`, and the Standard-channel CRD can make old objects unreadable | **Applies.** CRDs are v1.5.0 standard. There are 0 `TLSRoute` objects in the cluster (`kubectl get tlsroutes -A`), so the etcd-readability trap does not bite |
| Proxylib removed (`kafka` / `l7` / `l7proto` rules in CNPs) | None outside the CRD schemas |
| `CiliumNodeConfig` v2alpha1 → v2 | None |
| `toServices` + `toPorts` egress now denies when the Service is missing or has no backends | None outside the CRD schemas and default values |
| Mutual auth deprecated, Cluster Mesh / IPsec value renames | Not used |
| `cec.cilium.io/*` annotations no longer propagate to generated CECs | Not used |
| `upgradeCompatibility` | Unset today. Set it to the cluster's original install version for the minor bump if the chart asks for it. Check `helm history` before the PR |

## Changes

Each step is its own PR. Flux applies each one, and I watch it before the next goes up.

1. **Patch bump: 1.19.1 → 1.19.10.**
   - `release.yaml` `version`, plus `infra/crds/cilium/crds.yaml` regenerated from the v1.19.10 tag
     (the procedure in the kustomization header). Update the header pin.
   - Low risk, but it restarts the agents node by node.
   - Watch:
     - every `cilium` DaemonSet pod is Ready;
     - `cilium status` on each node;
     - the Gateway LB IPs answer;
     - BGP sessions are up;
     - CoreDNS is healthy.
2. **Gateway API CRDs v1.5.0 → v1.6.1.**
   - Do this before the minor bump, because 1.20 requires it.
   - **Open question:** GEP-1494 `ExternalAuth` has been an experimental-channel feature. If it is still
     experimental in v1.6, the `HTTPRoute` CRD must come from the experimental channel. Check
     `filters[].type` enum values in the v1.6.1 standard CRD before choosing. If the experimental
     channel is needed, install it for HTTPRoute only, and record why in `infra/crds/gateway-api/`.
3. **Minor bump: 1.19.10 → 1.20.4.**
   - `release.yaml`, regenerated CRDs, and a values diff against the 1.20 `default-values.yaml`
     (refresh our copy).
   - Run the upstream pre-flight DaemonSet first (`preflight.enabled=true`, `agent=false`,
     `operator.enabled=false`) and wait for it to be Ready on every node.
   - Run it as a one-off from a rendered manifest, not committed.
   - Rollback is `flux suspend` plus `helm rollback cilium`. 1.19↔1.20 is a tested pair, as long as no
     1.20-only feature is in use yet. That is why step 4 is a separate PR.
4. **ExternalAuth on staging routes first.**
   - Add the `ExternalAuth` filter to the staging route of one `one_factor` host. Use Authelia stage
     (`authelia.authelia-stage.svc:9091`, path `/api/authz/ext-authz`).
   - allowedHeaders: cookie, plus the `x-forwarded-*` set. Authelia needs them to build the redirect.
   - allowedResponseHeaders: `remote-user`, `remote-groups`, `remote-name`, `remote-email`.
   - The ReferenceGrant / CiliumNetworkPolicy must let the gateway reach Authelia on 9091. See
     [[feedback_cilium_gateway_netpol]]: `fromEntities: [host, remote-node, ingress]`.
   - Prove:
     - an unauthenticated GET gets a 302 to auth.stage;
     - a logged-in session gets 200;
     - an unrelated staging route is untouched.
5. **ExternalAuth on production.**
   - Add the filter to prometheus., alerts. and ladder.
   - Restore finance.: re-enable `httproute.yaml`, the homepage tile and the sitemonitor egress
     removed in #1801, with the filter on.
   - Re-run the HOMELAB-90 audit and record it on the ticket.
6. **Remove the dead CCEC.**
   - Delete `cilium-envoy-config-{production,staging}.yaml`.
   - Rewrite `docs/architecture/gateway-auth.md` for the per-route model.
   - Change Authelia's `default_policy` only if it no longer matters. With per-route auth it only sees
     requests from gated routes, so leave it alone unless there's a reason.

## Known upstream issue

cilium/cilium#49132 (open, affects v1.20) reports two problems:
- Removing an HTTPRoute that has an ExternalAuth filter makes unrelated routes answer 403 on existing
  connections.
- The order of ext_authz filters in the generated listener isn't stable.

Mitigations:
- Avoid deleting gated routes while the bug is open. Move or rename them in a quiet window, then check
  a handful of ungated hosts.
- Add a Blackbox / sitemonitor probe on two ungated hosts (e.g. home., links.) for a 403 alert, so this
  shows up as an alert rather than a user report.

## Risks

- **Cilium is the network.** A bad agent rollout takes everything down. See
  docs/operations/incidents/2026-06-24-cp-node-hang-cilium-k8sservicehost-spof.md. Mitigations:
  - one step at a time, with Flux watched live;
  - a working `helm rollback` path that is tested before the minor bump;
  - do this when George is reachable, not overnight.
- **Shared Talos shelves** ([[feedback_talos_shelf_correlated_failure]]). Don't start an agent rollout
  while any node is NotReady.
- **Failing closed.** With the filter on, an Authelia outage makes the four gated hosts 5xx, which is the
  intended trade. Ungated hosts are unaffected, which is better than the old global design.
- **No staging cluster.** Steps 1–3 hit production directly. Step 4 is the first step with a staging
  rehearsal.

## Not changing

- Hosts with their own login (chat., navidrome / music.). They stay ungated.
- chat.stage's open onboarding. That is George's housekeeping item: create the admin.
- The Cloudflare tunnel. None of these hosts go on it.

## Verification

- After each upgrade step:
  - `cilium status` is OK on all nodes;
  - `kubectl -n kube-system get ds cilium` shows desired = ready;
  - the Gateway answers on its LB IPs from VLAN 2 and VLAN 4;
  - BGP peers are Established;
  - no new Cilium alerts.
- After step 5: the HOMELAB-90 audit, re-run unauthenticated from the LAN.
  - finance., prometheus., alerts. and ladder. each get a 302 to `auth.burntbytes.com`.
  - With a session, they get 200.
  - Ungated hosts are unchanged.
  - The result is posted on HOMELAB-90.
