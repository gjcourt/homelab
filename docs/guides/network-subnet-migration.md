# Homelab Network Subnet Migration Plan

Date: 2026-02-28
Author: GitHub Copilot Chat Assistant for @gjcourt

## Summary

This document describes the plan to migrate the homelab LAN from the current 192.168.5.0/24 addressing to 10.0.3.0/24. It lists repository locations that reference existing IP addresses, concrete changes to make in the repo, verification steps, a rollout strategy, and a rollback plan.

> NOTE: The repository search used to generate this plan may be incomplete. After this PR is created, run a final repository-wide search for any remaining hardcoded IPs and update accordingly.

## Goals

- Replace hardcoded 192.168.5.* references with the new 10.0.3.* equivalents where appropriate.
- Prefer hostnames over IPs where possible to make future network changes easier.
- Update Cilium load-balancer pool, DNS rewrites, Talos logging, Home Assistant trust lists, Synology settings, and any hostAliases or manifest entries that rely on old IPs.

## Concrete occurrences found (examples)

The following files in this repo were identified as referencing the 192.168.5.* network. Update these to reference 10.0.3.* (or hostnames) as described.

- infra/configs/cilium/load-balancer-ip-pool.yaml
  - Current: IP pool blocks start: `192.168.5.30` stop: `192.168.5.255`
  - Change to: `10.0.3.30` - `10.0.3.255` (or the block you plan to assign for LB IPs)

- docs/architecture/dns-strategy.md
  - References cilium gateway IPs `192.168.5.30` and `192.168.5.31` and AdGuard rewrite examples.
  - Update examples and instructions to the new gateway IPs (e.g. `10.0.3.30`, `10.0.3.31`) and note any DNS rewrite UI steps.

- apps/staging/memos/deployment-patch.yaml
  - hostAliases entry: `ip: "192.168.5.30"` pointing `auth.stage.burntbytes.com`
  - Change hostAliases IP to the new staging gateway IP (e.g. `10.0.3.30`) or convert to DNS name if resolvable inside cluster.

- docs/apps/memos.md
  - Mentions `192.168.5.33` as Gateway API IP for Authelia — update accordingly.

- apps/staging/homepage/services.yaml
  - Synology UI widget references `https://192.168.5.8:5001` and widget url points to `192.168.5.8`.
  - Update to Synology's new address on 10.0.3.0/24 (e.g. `10.0.3.8`) or switch to a hostname.

- docs/infra/storage.md, docs/guides/synology-iscsi-operations.md, scripts/synology/README.md, scripts/synology/lun-manager/README.md
  - Examples set `SYNOLOGY_HOST="192.168.5.8"`. Update all examples and any code comments to the new host/IP.

- docs/infra/kernel-log-shipping.md
  - Talos machine logging destination uses `tcp://192.168.5.1:30600` in examples.
  - Update Talos example and NodePort/Vector mapping to new central logging host (e.g. `10.0.3.1:30600`) or a hostname reachable from Talos nodes.

- apps/base/homeassistant/configmap.yaml
  - `trusted_proxies` contains `- 192.168.5.0/24`
  - Change to `- 10.0.3.0/24` (or appropriate CIDR) so Home Assistant trusts the new proxy/network.

- apps/base/navidrome/deployment.yaml and docs/apps/navidrome.md
  - Environment variable `ND_EXTAUTH_TRUSTEDSOURCES` includes `192.168.0.0/16` and broader ranges. Ensure `10.0.3.0/24` is included where required for trusted proxies and gateway trust lists.

- infra/controllers/synology-csi/secret-client-info.yaml
  - While credentials are encrypted, verify the synology CSI configuration does not hardcode the IP; update the endpoint or example envs if necessary.

- Various docs and incident notes reference 192.168.5.* addresses (examples/diagrams). Update wording and examples for accuracy after migration.

## Files to change in this PR

This PR should add the migration plan document only. Subsequent PRs will apply the actual infra/config changes.

- Add: `docs/guides/network-subnet-migration.md` — contains this plan and the checklist for concrete changes.

Rationale: keep the plan and approvals visible and separate from infra changes that need staged, tested application.

## Checklist for follow-up PRs (one PR per area recommended)

- [ ] infra/configs/cilium/load-balancer-ip-pool.yaml — update pool to 10.0.3.30–10.0.3.255 and apply via Flux.
- [ ] AdGuard DNS rewrites — update rewrites for `*.stage.burntbytes.com` and `*.burntbytes.com` to the new gateway IPs.
- [ ] apps/staging/memos/deployment-patch.yaml — update hostAliases to new gateway IP or remove in favor of DNS.
- [ ] apps/staging/homepage/services.yaml — update Synology widget URLs to new IP or hostnames.
- [ ] scripts/synology/* and docs — update SYNOLOGY_HOST examples to new address or hostname.
- [ ] docs/infra/kernel-log-shipping.md and Talos machine configs — update `machine.logging.destinations` with new address.
- [ ] apps/base/homeassistant/configmap.yaml — change trusted_proxies to include 10.0.3.0/24.
- [ ] Update any other hostAliases, NodePort/hostNetwork entries, or hardcoded IPs discovered.

## Validation steps

1. Apply Cilium pool change, run a test Service of type LoadBalancer and confirm EXTERNAL-IP is in 10.0.3.30–255 range.
2. Update AdGuard rewrites, then from a LAN client verify DNS resolves stage and production hostnames to new gateway IPs.
3. Update memos hostAliases and redeploy; confirm the app can reach Authelia issuer via hostname or new gateway IP.
4. Update Talos machineConfig on nodes to point `machine.logging.destinations` to new address and confirm logs reach Vector.
5. Update Synology environment variables for any scripts and confirm lun-manager can SSH to the NAS and CSI can provision LUNs.
6. Update Home Assistant configmap (trusted_proxies) and reload config; ensure proxied requests are still accepted.

## Rollout strategy

1. Create a feature branch `chore/network-migration-plan-10.0.3` and create this document as a PR for approval.
2. Lower DHCP lease times ahead of cutover.
3. Apply infra changes in the following order, monitoring after each step:
   - Cilium load-balancer pool (so new LB IPs come from 10.0.3.30+).
   - AdGuard DNS rewrites.
   - Update hostAliases or DNS mappings used by Pods (memos, memos docs, etc.).
   - Talos machine logging destinations and NodePort adjustments.
   - Synology scripts / env updates.
   - Home Assistant trusted proxy change.
4. Reboot or renew DHCP on devices to pick up new IPs, or change router DHCP scope and allow reboots.

## Rollback plan

- Keep old router/DHCP config handy. If major issues occur, revert DHCP/router to the 192.168.5.0/24 configuration to restore previous addressing.
- Re-apply previous Cilium IP pool and AdGuard rewrites if needed.

## Notes & recommendations

- Replace IPs with DNS names where possible. Add internal DNS records or AdGuard rewrites for stable hostnames (e.g. synology.local or synology.homelab).
- Limit the number of kube hostAliases that reference a specific IP — prefer DNS resolvable names.
- Audit repository for remaining hardcoded IPs (search for "192.168.5.", "192.168.", and any specific hosts) before changing live systems.
