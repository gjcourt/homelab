---
status: superseded
last_modified: 2026-05-06
superseded_by: docs/plans/2026-05-06-network-resilience-and-bgp-completion.md
summary: "Revised BGP phase 4 (safe L2 removal gate) after wired-client ARP regression"
---

# BGP Phase 4 Revision — Safe L2 Removal Gate

> **Superseded 2026-05-06 by `docs/plans/2026-05-06-network-resilience-and-bgp-completion.md`.**
> The unified plan folds this revision into its Phase D (LB pool migration) and
> Phase E (pure BGP), and adds Phases A–C/F for the broader resilience and security
> findings surfaced in the 2026-05-06 critique. Read this doc for historical context;
> follow the unified plan for execution.
>
> **Context:** The original BGP rollout plan (`docs/plans/2026-03-08-bgp-rollout.md`)
> completed Phases 1–4b on 2026-05-05 and removed L2 announcements entirely. This
> caused a regression for wired devices on `10.42.2.0/24` that share a subnet with the
> LB IP pool and rely on ARP. See incident postmortem:
> `docs/operations/incidents/2026-05-05-bgp-l2-wired-device-regression.md`.
>
> **Current state (post-fix):** L2 announcements are restored. BGP + L2 run together.
> This document defines the correct conditions and procedure for safely removing L2 in
> the future, and explains why that is not yet possible.

---

## The Problem with Phase 4 As Executed

Phase 4 of the BGP rollout assumed that once BGP was established, L2 announcements were
redundant. This assumption is **false** when any client device shares a `/24` with the
LB IP pool.

```
LB IP pool: 10.42.2.40 – 10.42.2.254
Wired clients on same subnet: 10.42.2.0/24

Apple TV (10.42.2.19) → ARP for 10.42.2.43
                       ↑
               NO BGP ROUTE HELPS HERE
             ARP operates at L2, below routing
```

BGP installs routes in the UCGF's forwarding table. Those routes are consulted only for
**inter-subnet** traffic. A device on `10.42.2.x` will always ARP directly for any
destination in `10.42.2.0/24`, including LB IPs — the routing table is bypassed.

The test plan executed after Phase 4a used a Mac on VLAN 4, which routes through the
UCGF and benefits from BGP routes. It passed. The Apple TV on VLAN 2 was never tested.

---

## L2 + BGP Together Is Correct Steady State (Until Topology Changes)

Run both mechanisms permanently until the preconditions for safe L2 removal (below) are
met. They do not conflict:

| Traffic path | Mechanism |
|---|---|
| Wireless clients (separate VLAN) | BGP → routed by UCGF |
| Wired clients on `10.42.2.0/24` | L2 ARP → Cilium l2-announcer |
| Pod-to-LB (in-cluster) | Cilium kube-proxy replacement |

There is no operational cost to running both. The L2 announcer adds negligible overhead
(a few Leases and periodic gratuitous ARPs).

---

## Preconditions for Safe L2 Removal

L2 announcements can only be safely removed when **all** of the following are true:

### Gate 1 — No client host shares a `/24` with any LB IP

Verify:

```bash
# List all LB IPs
kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.status.loadBalancer.ingress[0].ip}{"\n"}{end}' | sort -u

# For each LB IP, determine its /24: 10.42.2.x → subnet 10.42.2.0/24
# Then enumerate all wired client hosts on that /24 (DHCP leases, static assignments)
# from the UCGF:
ssh root@10.42.2.1 'cat /etc/dhcp/dhcpd.leases 2>/dev/null || ip neighbor show'
```

**Action required if gate fails:** Move the LB pool to a dedicated `/24` with no client
hosts (see "Topology change path" below) before proceeding.

### Gate 2 — LB IP pool is on a subnet that can be moved

The current pool (`10.42.2.40`–`10.42.2.254`) shares `/24` with all cluster nodes
(`10.42.2.20`–`10.42.2.25`), the UCGF (`10.42.2.1`), and wired client devices. This
subnet cannot be vacated of clients without a full network migration.

**Status:** Gate 2 **fails** with the current topology. Do not proceed to Phase 4 until
resolved.

### Gate 3 — Wired client device inventory is complete

Before executing Phase 4a, enumerate every wired device connected to the network and
confirm none are on the LB pool's `/24`:

| Device category | VLAN | Shares /24 with LB pool? |
|---|---|---|
| Cluster nodes (`.20`–`.25`) | VLAN 2 | Yes — but they are the speakers, not clients |
| Wireless clients | VLAN 10+ | No — routed via UCGF |
| Apple TV (`.19`) | VLAN 2 | **Yes** |
| HifiBerry nodes (`.38`, `.39`) | VLAN 2 | **Yes** |
| `kitchen-pi` (`.143`) | VLAN 2 | **Yes** |
| hestia/TrueNAS (`.10`) | VLAN 2 | **Yes** |
| Synology (`10.42.2.11`) | VLAN 2 | **Yes** |

**Status:** Gate 3 **fails**. Multiple wired devices on `10.42.2.0/24`.

---

## Topology Change Path (If You Want Pure BGP Someday)

Move the LB IP pool to a dedicated `/24` with no client or infrastructure hosts. The
recommended candidate is `10.42.3.0/24` (currently unused).

### Steps

1. **Add a new LB pool** for `10.42.3.0/24` in `infra/configs/cilium/load-balancer-ip-pool.yaml`.
2. **Migrate pinned services** (AdGuard `.43`/`.45`, gateway `.40`) to new IPs in the
   new pool. Update all client DNS configs (devices, AdGuard upstream forwards, etc.)
   to use the new IPs.
3. **Update UCGF prefix-list** to accept `/32`s from the new pool:
   ```
   ip prefix-list K8S-LB-IPS seq 20 permit 10.42.3.0/24 ge 32 le 32
   ```
4. **Soak** with both pools active. Confirm BGP announces new pool IPs and all clients
   resolve correctly.
5. **Remove old pool** (the `10.42.2.40`–`10.42.2.254` range).
6. **Now execute Phase 4a/4b** — gates 1–3 are satisfied for the new pool.

> **Note:** Step 2 requires updating DNS configs on every wired device that has
> `10.42.2.43` hardcoded. The AdGuard AdGuard HA failover config, network DHCP DNS
> option, and any static device configs all need updating. Budget a maintenance window.

---

## Revised Phase 4 Checklist

When the topology change above is complete, re-execute Phase 4 with these additional
checks:

### Pre-Phase-4a gates (new)

```bash
# Gate 1: confirm no wired client shares /24 with the LB pool
# Expected output: no 10.42.3.x addresses appear in DHCP client list
ssh root@10.42.2.1 'vtysh -c "show ip bgp neighbors"'  # BGP still healthy

# Gate 3: manual verification of wired device inventory
# Walk through each device in the table above and confirm subnet assignment
```

### Phase 4a test matrix (updated)

The original test matrix only tested from VLAN-4 clients (cross-subnet). Add:

| Test | Expected |
|---|---|
| `arp -d 10.42.3.43; dig @10.42.3.43 example.com` from Apple TV | DNS resolves via BGP route |
| `arp -d 10.42.3.43; ping 10.42.3.43` from wired device on VLAN 2 | Reachable (BGP route via UCGF) |
| `arp -n 10.42.3.43` on a VLAN-2 host | No ARP entry — traffic goes through gateway |

> Interpreting the last test: `arp -n` returning no entry for a **remote-subnet** LB IP
> is **correct** — the host's kernel routes to the gateway, not directly. The prior test
> (`arp -d <same-subnet-ip>; curl …`) was misleading because a cross-subnet host caches
> the gateway's MAC, not the LB IP's MAC.

### Phase 4b additional note

When running `l2announcements.enabled: false`, the Cilium configmap updates but the
DaemonSet pods **do not restart**. Cilium reads feature flags at startup, not on
configmap hot-reload. After Phase 4b Helm reconcile, manually verify the agents see
the change:

```bash
kubectl -n kube-system get configmap cilium-config -o jsonpath='{.data.enable-l2-announcements}'
# Expected: false

# Confirm l2-announcer module is stopped (expected once policy is removed)
CILIUM_POD=$(kubectl -n kube-system get pod -l app.kubernetes.io/name=cilium-agent -o name | head -1 | sed 's|pod/||')
kubectl -n kube-system exec $CILIUM_POD -- cilium-dbg status --all-health 2>&1 | grep l2-announcer
```

If the configmap shows `false` but agents still show `l2-announcer [OK]`, rolling restart
is required:

```bash
kubectl rollout restart ds/cilium -n kube-system
kubectl rollout status ds/cilium -n kube-system
```

---

## Current State Summary

| Component | State | Correct? |
|---|---|---|
| `CiliumL2AnnouncementPolicy` | Present (`l2-announcement-policy-staging`) | Yes |
| `l2announcements.enabled` | `true` | Yes |
| `bgpControlPlane.enabled` | `true` | Yes |
| BGP peers | 3 workers Established | Yes |
| L2 leases | All LB IPs leased to `talos-2mz-rfj` | Yes |
| Phase 4 gate status | **BLOCKED** — wired clients on same /24 as LB pool | N/A |

**Do not re-execute Phase 4 without clearing the topology gates above.**
