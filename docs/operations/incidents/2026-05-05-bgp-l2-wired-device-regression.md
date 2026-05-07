# Incident: Wired Devices Lose Cluster Service Connectivity After BGP Migration

**Date:** 2026-05-05
**Status:** Resolved
**Severity:** High — all wired devices on `10.42.2.0/24` unable to reach cluster LoadBalancer IPs
**Duration:** ~1 day (BGP rollout completed 2026-05-05; regression detected same evening)
**Environments affected:** Physical LAN (`10.42.2.0/24`), wired hosts only
**Authors:** George Courtois

---

## Summary

Following the BGP rollout (Phase 4a/4b in `docs/plans/2026-03-08-bgp-rollout.md`),
wired devices on the `10.42.2.0/24` subnet lost the ability to reach cluster
LoadBalancer IPs (AdGuard DNS at `10.42.2.43`, production gateway at `10.42.2.40`,
etc.). The Apple TV at `10.42.2.19` was the trigger: it stopped working entirely
because it could no longer resolve DNS.

Wireless devices connected via UniFi WAPs were **unaffected** throughout.

**Root cause:** Cilium's L2 announcement subsystem (gratuitous ARP / ARP reply) was
removed as part of the BGP cutover (Phase 4a deleted the `CiliumL2AnnouncementPolicy`;
Phase 4b set `l2announcements.enabled: false`). This is correct for routed clients but
fatally wrong for any device on the **same `/24`** as the LB IP pool, which must resolve
LB IPs via ARP — not via routing table lookups. BGP only helps the UCGF's routing table;
it cannot answer ARP requests on behalf of a `/32` that is not locally resident on any
host.

**Fix:** Restored the `CiliumL2AnnouncementPolicy` and re-enabled
`l2announcements.enabled: true` (PR #536). L2 and BGP now run simultaneously, which is
the correct steady state for a topology where wired clients share a `/24` with LB IPs.

---

## Affected Services

| Service / Host | Impact |
|---|---|
| Apple TV (`10.42.2.19`) | No DNS → no connectivity to any cluster service |
| Any wired device on `10.42.2.0/24` | Unable to ARP for `10.42.2.40`–`10.42.2.254` |
| AdGuard DNS (`10.42.2.43`, `10.42.2.45`) | Unreachable via ARP from same-subnet clients |
| Production gateway (`10.42.2.40`) | Unreachable via ARP from same-subnet clients |
| Wireless (WAP) clients | **Not affected** (see explanation below) |

---

## Timeline

| Time | Event |
|------|-------|
| 2026-05-05 daytime | BGP rollout Phase 4a/4b completed; L2 removed; cluster traffic appeared normal |
| 2026-05-05 evening | Apple TV (`10.42.2.19`) stops working; DNS and cluster services unreachable |
| 2026-05-05 evening | Wireless devices (Mac, phones) confirmed working throughout the day |
| 2026-05-05 evening | Diagnosed: wired VLAN-2 hosts cannot ARP for LB IPs; BGP-only is insufficient |
| 2026-05-05 late | PR #536 opened: restore `CiliumL2AnnouncementPolicy` + `l2announcements.enabled: true` |
| 2026-05-06 ~04:00 UTC | PR #536 merged; `flux reconcile` run on `infra-configs` + `infra-controllers` |
| 2026-05-06 ~04:01 UTC | Cilium HelmRelease reconciled; configmap updated; agent pods NOT restarted (config hot-reload does not trigger DaemonSet rollout) |
| 2026-05-06 ~04:02 UTC | Diagnosed: `l2-announcer` module STOPPED; Cilium reads feature flags at startup, not on configmap change |
| 2026-05-06 ~04:05 UTC | `kubectl rollout restart ds/cilium -n kube-system` — 6-node rolling restart |
| 2026-05-06 ~04:06 UTC | L2 leases appear: `cilium-l2announce-*` in `kube-system` with elected speaker `talos-2mz-rfj` |
| 2026-05-06 ~04:06 UTC | Apple TV pingable and AirPlay port (7000) responding — incident resolved |

---

## Root Cause Analysis

### Why BGP alone cannot serve same-subnet clients

BGP advertises routes — it tells the UCGF "to reach `10.42.2.43/32`, forward traffic to
worker node `10.42.2.23`." The UCGF installs this in its routing table and uses it for
traffic that arrives from a **different subnet** and must be routed.

However, a device on the **same `/24`** (`10.42.2.19`) applying the standard IP
forwarding rule computes `10.42.2.43 & /24 == 10.42.2.0/24 == its own subnet` and sends
an ARP request directly onto the wire:

```
Apple TV: "Who has 10.42.2.43? Tell 10.42.2.19"
<silence — no Cilium agent responding>
Apple TV: ARP timeout → cannot build Ethernet frame → TCP/IP fails
```

The UCGF routing table entry is never consulted, because the host never sends the frame
to the gateway; it's trying to deliver it locally. Without a Cilium agent responding to
ARP for the LB IP, the address is unreachable to any same-subnet client, regardless of
BGP health.

### Why wireless clients were unaffected

UniFi WAPs place wireless clients on a separate VLAN/subnet from the wired management
and compute network:

```
Wireless client (e.g. 10.10.0.50/24, VLAN 10)
  │  "10.42.2.43 is NOT in my subnet (10.10.0.0/24)"
  └→ sends frame to default gateway (UCGF at 10.10.0.1)
       └→ UCGF routing table: BGP route 10.42.2.43/32 → next-hop 10.42.2.23
            └→ forwarded to cluster worker → reaches AdGuard
```

Wireless clients see `10.42.2.43` as a **remote** address, route through the UCGF, and
benefit from BGP routes. They never send an ARP for `10.42.2.43`. BGP alone is
sufficient for them.

The Apple TV (and any other wired device on `10.42.2.0/24`) has a different view:

```
Apple TV (10.42.2.19/24, VLAN 2)
  │  "10.42.2.43 IS in my subnet (10.42.2.0/24)"
  └→ sends ARP broadcast: "Who has 10.42.2.43?"
       └→ <nobody answers after L2 removal>
            └→ ARP fails → connectivity fails
```

### Why the Phase 4a test plan missed this

The test plan in `docs/plans/2026-03-08-bgp-rollout.md` specified running the "LAN client
`arp -d <ip>; curl …`" smoke test, but the test was executed from a Mac on VLAN 4 (or
any cross-subnet host). That device routes through the UCGF and gets a BGP-installed
route — the test passes trivially even with L2 removed. The test plan lacked coverage
for wired devices on the **same `/24`** as the LB pool.

There is also a secondary issue with the Phase 4b Helm change: setting
`l2announcements.enabled: false` updated the `cilium-config` ConfigMap but did **not**
trigger a Cilium DaemonSet rollout. This is a documented Cilium 1.19 gotcha (noted in
the plan's Phase 2a section for `bgpControlPlane.enabled`), but it was not applied to
Phase 4b. The DaemonSet continued running with the old runtime config, meaning when the
L2 policy was restored, agents still had the feature disabled and needed a manual
`kubectl rollout restart ds/cilium`.

---

## Resolution

**PR #536** — `fix: restore L2 announcements alongside BGP for same-subnet ARP`

1. Restored `infra/configs/cilium/l2-announcement-policy.yaml`
2. Re-added it to `infra/configs/cilium/kustomization.yaml`
3. Set `l2announcements.enabled: true` in `infra/controllers/cilium/values.yaml`
4. Merged, force-reconciled `infra-configs` + `infra-controllers` + `helmrelease/cilium`
5. `kubectl rollout restart ds/cilium` to pick up the runtime config change
6. Verified `cilium-l2announce-*` Leases appeared and `l2-announcer` module showed `[OK]`

---

## Action Items

| # | Action | Owner | Priority |
|---|--------|-------|----------|
| 1 | Update BGP rollout plan: Phase 4 is **invalid for the current topology** — see new plan doc | George | High |
| 2 | Add wired same-subnet device to the Phase 4a test matrix | George | High |
| 3 | Add a pre-Phase-4 gate: enumerate all devices sharing `/24` with the LB pool; get them off it first OR explicitly accept L2+BGP permanently | George | High |
| 4 | Document the Cilium config hot-reload gotcha in the rollout plan | George | Medium |
| 5 | Consider moving LB IP pool to a dedicated `/24` (e.g. `10.42.3.0/24`) that no client host shares — this is the only clean path to ever removing L2 | George | Low |

---

## Why L2 + BGP Together Is the Correct Steady State

Until the LB IP pool is on a subnet that no wired client shares, **both mechanisms must
run simultaneously**. They serve different traffic paths and do not conflict:

| Traffic type | Resolution mechanism |
|---|---|
| Wireless clients (different VLAN/subnet) | BGP routes on UCGF → routed delivery |
| Wired clients on `10.42.2.0/24` | L2 ARP → same-subnet direct delivery |
| Cross-cluster traffic (pod-to-LB) | Cilium in-kernel LB (neither BGP nor L2) |

Running both has no operational downside. BGP provides the ECMP, multi-path failover,
and observability benefits it was designed for. L2 covers same-subnet ARP without
interfering with BGP routes on the UCGF.

---

## Related

- `docs/plans/2026-03-08-bgp-rollout.md` — original rollout plan (Phase 4 needs correction)
- `docs/plans/2026-05-05-bgp-phase4-revision.md` — revised Phase 4 with safe L2 removal gate
- PR #536 — fix commit
