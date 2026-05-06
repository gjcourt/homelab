---
status: Stable
last_modified: 2026-05-06
---

# Cluster Load Balancing: L2 + BGP

> **Scope.** How LoadBalancer service IPs become reachable from the LAN.
> Covers Cilium's BGP control plane, L2 announcements, ECMP behavior, and
> the rationale for running both simultaneously. For the IP map see
> [addressing.md](addressing.md). For per-client packet flow see
> [traffic-flows.md](traffic-flows.md).

## Why both L2 and BGP

The cluster runs **two parallel mechanisms** for advertising LoadBalancer
IPs to the LAN. They are not redundant alternatives — they cover **different
client populations**, and each is required for its respective set:

| Client type | Subnet relative to LB IP | Resolution mechanism |
|---|---|---|
| Wired client on VLAN 2 (Apple TV, HifiBerry, etc.) | Same `/24` as LB IP | **L2 ARP** — Cilium agent on an elected node responds with its own MAC |
| Wireless client on VLAN 4 | Different subnet | **BGP routing** — UCGF has a `/32` route, picks an ECMP next-hop |
| Cross-subnet wired (Mac on VLAN 4) | Different subnet | **BGP routing** (same as wireless) |
| In-cluster pod | Special — uses Cilium's BPF kube-proxy replacement | Neither L2 nor BGP — direct socket-level rewrite |

The mechanisms operate at different layers and don't conflict:

- **L2 (ARP) operates at Layer 2.** It only matters when the source and
  destination are in the same broadcast domain. The kernel's IP forwarding
  rule sends an ARP request directly onto the wire instead of forwarding to
  a gateway.
- **BGP advertises Layer 3 routes.** It only matters when the destination is
  outside the source's subnet, requiring routing-table lookup.

A client running through both paths (e.g. moving from wired VLAN 2 to
wireless VLAN 4 mid-session) silently transitions between mechanisms — the
LB IP is unchanged.

> **History.** The 2026-05-05 BGP rollout (Phase 4) tried to remove L2 on
> the assumption that BGP was sufficient for everything. It is not — same-
> subnet wired clients still need ARP. See
> `docs/operations/incidents/2026-05-05-bgp-l2-wired-device-regression.md`.

## L2 announcements (Cilium L2 announcer)

### Mechanism

1. A `CiliumL2AnnouncementPolicy` resource declares which services should be
   announced and from which nodes. Current policy: `l2-announcement-policy-staging`
   (cluster-wide, no `nodeSelector`) — to be replaced per the active plan.
2. The **cilium-operator** watches matching services and creates a
   Kubernetes `Lease` per service IP in `kube-system`, named
   `cilium-l2announce-<namespace>-<service>`.
3. Lease leader election picks one node per IP. That node's **cilium-agent**
   responds to ARP requests for the IP with its own NIC MAC and sends
   periodic gratuitous ARPs.
4. Same-subnet clients receive the ARP reply and send frames directly to
   the elected node. From there, Cilium's in-kernel kube-proxy replacement
   does service load balancing to a backend pod (which may live on any
   node).

### Current state (verify)

```bash
# All L2 leases
kubectl -n kube-system get leases | grep cilium-l2announce

# Module health on a cilium-agent
CILIUM_POD=$(kubectl -n kube-system get pod -l app.kubernetes.io/name=cilium-agent -o name | head -1 | sed 's|pod/||')
kubectl -n kube-system exec $CILIUM_POD -- cilium-dbg status --all-health 2>&1 | grep -A 2 "l2-announcer\|l2-responder"
```

### Failure modes

| Failure | Behavior | Recovery |
|---|---|---|
| Speaker node reboots | Lease expires; another node wins re-election | ~15s ARP outage on default timing (5s with the proposed lease tuning) |
| All eligible nodes drained | No speaker; ARP for LB IP fails | Same-subnet clients lose access until at least one node is back |
| Cilium agent crash on speaker | Lease GC removes the lease; another node wins | ~lease-duration outage (15s default) |
| Helm config change without DaemonSet restart | Configmap updates but agents keep old runtime config; L2 announcer module stays in stopped state | `kubectl rollout restart ds/cilium -n kube-system` |

### Operator gotcha

Cilium reads feature flags (`enable-l2-announcements`) at startup, not on
configmap hot-reload. After any Helm value change to the `l2announcements:`
block, **manually restart the DaemonSet:**

```bash
kubectl rollout restart ds/cilium -n kube-system
kubectl rollout status ds/cilium -n kube-system
```

Without this step, the configmap shows the new value but the agents
continue running with the old config — L2 silently doesn't work.

## BGP (Cilium BGP control plane)

### Mechanism

1. The Cilium BGP control plane is enabled cluster-wide via Helm:
   `bgpControlPlane.enabled: true`. This makes every cilium-agent capable
   of running BGP.
2. `CiliumBGPClusterConfig` (`infra/configs/cilium/bgp-cluster-config.yaml`)
   selects which nodes actually peer. Current selector: every node **except
   control-plane**, which selects all 3 workers.
3. `CiliumBGPPeerConfig` defines timers, address families, and which
   `CiliumBGPAdvertisement` resources feed each family.
4. `CiliumBGPAdvertisement` declares what to advertise. Current advertisement
   matches every LoadBalancer service (`matchLabels: {}`), advertising each
   service's LB IP as a `/32`.
5. Each worker initiates a TCP connection to UCGF `10.42.2.1:179`,
   negotiates an eBGP session (AS 65010 ↔ AS 65100), and announces its `/32`s
   with itself as the next-hop.

### UCGF side

The UCGF runs FRRouting (FRR). Authoritative config snapshot in
[`docs/reference/ucgf-bgp-frr.conf`](../../reference/ucgf-bgp-frr.conf).
Key elements:

- **`bgp listen range 10.42.2.0/24 peer-group K8S-NODES`** — accepts BGP
  from any LAN host asserting AS 65010. Lets new workers join automatically.
- **`maximum-paths 8`** — installs all 3 worker next-hops in the routing
  table for ECMP. Without this, FRR keeps only one path per prefix.
- **`ip prefix-list K8S-LB-IPS`** — restricts inbound to `/32`s in the LB
  pool subnet. A misconfigured cluster cannot push arbitrary routes
  (e.g. a default route).
- **`route-map DENY-ALL` outbound** — gateway never advertises its own
  routes back to the cluster.

### Result on the UCGF

```bash
ssh root@10.42.2.1 'vtysh -c "show ip route bgp"'
# Each LB IP /32 with 3 next-hops (one per worker), ECMP weighted equally:
# B>* 10.42.2.40/32 [20/0] via 10.42.2.23, eth0, weight 1
#                       via 10.42.2.24, eth0, weight 1
#                       via 10.42.2.25, eth0, weight 1
```

### Failure modes

| Failure | Behavior | Recovery |
|---|---|---|
| One worker's BGP session drops | UCGF removes that next-hop after hold-time (90s); ECMP collapses to 2 paths | Automatic on session re-establishment |
| Two workers' BGP sessions drop | One next-hop remains; no traffic loss but no HA | Automatic on session re-establishment |
| All 3 worker BGP sessions drop | UCGF has no route; cross-subnet clients lose access | Automatic on first session re-establishment |
| UCGF FRR daemon crashes | All BGP routes removed; cross-subnet access fails | `ssh root@10.42.2.1 systemctl restart frr` |
| UCGF firmware upgrade reverts `/etc/frr/frr.conf` and `/etc/frr/daemons` | BGP not running at all | Re-apply `docs/reference/ucgf-bgp-frr.conf`; re-set `bgpd=yes` in `/etc/frr/daemons`; `systemctl enable --now frr` |

### Graceful restart

The peer config sets `gracefulRestart.enabled: true` with `restartTimeSeconds: 120`.
This lets a Cilium agent restart without the UCGF dropping its routes
immediately — the UCGF holds the routes until either the BGP session
re-establishes or the 120s timer expires. End-to-end effect: a Cilium
DaemonSet rolling restart causes ~zero LB outage as long as each agent's
restart completes inside 120s.

## ECMP behavior

The UCGF installs 3 next-hops per LB IP. For each new flow, FRR picks one
next-hop (typically by 5-tuple hash). Existing flows are sticky to their
chosen next-hop until expiration.

### Once a packet reaches a worker

Both L2 and BGP paths converge on the same in-cluster behavior:

1. Frame arrives on the worker's NIC (via L2 ARP for same-subnet, via UCGF
   forwarding for routed).
2. Cilium's BPF kube-proxy replacement looks up the destination IP+port in
   the BPF service map.
3. It rewrites the packet's destination to a backend pod IP (which may be
   on any node) and forwards via VXLAN tunnel.
4. The backend pod responds; reply traverses the same VXLAN back to the
   ingress worker, which un-rewrites and sends the frame back to the LAN.

`externalTrafficPolicy: Cluster` (the current default for all LB services)
means **the ingress worker may not host the backend pod** — there's a
second hop. `Local` would skip the second hop but reduce ECMP path count
to "workers running the backing pod." The plan
`2026-05-06-network-resilience-and-bgp-completion.md` Phase F.3 covers
per-service review of this.

## L2-vs-BGP HA asymmetry

Because of the above, the HA story is asymmetric for the two client types:

| | L2 (wired VLAN 2) | BGP (cross-subnet) |
|---|---|---|
| Active speakers per IP | 1 (lease holder) | 3 (all workers) |
| Failover trigger | Lease expiry on speaker death | BGP hold-time on worker death |
| Failover time | ~5–15s (depends on lease tuning) | ~90s (hold-time) |
| Concurrent path count | 1 (no aggregate bandwidth across nodes) | 3 (ECMP) |
| Single point of failure | The elected speaker node | Any single worker (other 2 cover) |

Practical implication: when the elected L2 speaker reboots, wired VLAN-2
clients lose access for ~5–15s. Wireless clients are unaffected. When a
worker reboots, no one notices.

## Configuration files index

| File | Resource | Purpose |
|---|---|---|
| `infra/configs/cilium/load-balancer-ip-pool.yaml` | `CiliumLoadBalancerIPPool` × 2 | LB IP pools (`home-c-pool`, `home-compute-pool`) |
| `infra/configs/cilium/l2-announcement-policy.yaml` | `CiliumL2AnnouncementPolicy` | L2 announcement policy (cluster-wide, no selector currently) |
| `infra/configs/cilium/bgp-cluster-config.yaml` | `CiliumBGPClusterConfig` | Worker-only nodeSelector; AS 65010; peers with UCGF |
| `infra/configs/cilium/bgp-peer-config.yaml` | `CiliumBGPPeerConfig` | Timers; IPv4 family; advertisement matchLabels |
| `infra/configs/cilium/bgp-advertisement.yaml` | `CiliumBGPAdvertisement` | matchLabels {} → all LB IPs |
| `infra/controllers/cilium/values.yaml` | Helm values | `bgpControlPlane.enabled: true`, `l2announcements.enabled: true`, `kubeProxyReplacement: true` |
| `docs/reference/ucgf-bgp-frr.conf` | (snapshot) | UCGF FRR config; canonical reference |
