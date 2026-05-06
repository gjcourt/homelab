---
status: Stable
last_modified: 2026-05-06
---

# Networking Glossary

> **Scope.** Quick definitions of terms used across the networking docs.
> Definitions are written for the homelab's specific context — so e.g.
> "BGP" reads in terms of Cilium ↔ UCGF, not general internet routing.

## Concepts

**ARP (Address Resolution Protocol).** L2 mechanism for finding the MAC
address corresponding to an IP address. A host sends a broadcast frame
"Who has 10.42.2.43?" and waits for a reply with the target's MAC. Only
works **within a single broadcast domain** (same VLAN/subnet).

**Broadcast domain.** The set of devices that receive an L2 broadcast frame.
Bounded by router/L3 boundaries and VLAN tags. In this network, each VLAN
is one broadcast domain.

**BGP (Border Gateway Protocol).** A routing protocol used here for the
cluster to advertise LoadBalancer IP `/32` routes to the UCGF. Each worker
node speaks eBGP from AS 65010 to UCGF AS 65100. BGP is L3 only — it does
not help with same-subnet ARP.

**eBGP / iBGP.** External vs internal BGP. eBGP is between different ASNs
(our case: cluster AS 65010 → UCGF AS 65100). iBGP is within one ASN.

**ECMP (Equal-Cost Multi-Path).** When multiple equally-good routes exist
for a destination, the router installs all of them and picks one per flow
(usually 5-tuple hashed). On the UCGF, each LB IP `/32` has 3 ECMP next-hops
(one per worker).

**Edge port (PortFast).** STP setting that skips the listening/learning
states on link-up and **does not generate TCNs on link-down/up.** Should be
enabled on every port connected to an end device. See incident
`2026-03-10` for why this matters.

**EEE (Energy Efficient Ethernet, 802.3az).** PHY-level power saving where
the NIC enters Low Power Idle during traffic gaps. Can interact badly with
some switch firmwares (interpreted as link-down events). Disabled on RPi 4
hosts in this network — see incident `2026-03-10`.

**Gateway API.** Kubernetes API for advanced ingress (Gateway, HTTPRoute,
GRPCRoute, etc.). Cilium implements it; provisions Envoy proxies as the
data plane. See `docs/architecture/gateway-auth.md`.

**Gratuitous ARP.** An unsolicited ARP announcement: "I am at IP X, here's
my MAC." Sent by the L2 announcer when a service comes up or a lease
changes hands, so other hosts update their ARP caches without waiting for
their existing entries to expire.

**Hold time (BGP).** Negotiated session timer; if the peer doesn't send
keepalives or updates within this window, the session is considered dead.
Set to 90s on this network's BGP sessions.

**HTTPRoute.** Gateway API resource defining how requests to a hostname/path
are routed to backend services. Each `*.burntbytes.com` app has one.

**iSCSI (SCSI over TCP).** Block storage protocol. Cluster nodes connect to
hestia (TrueNAS) and Synology iSCSI targets to back PersistentVolumes.
Default port 3260. Both targets sit on the Lab VLAN with the cluster, so
delivery is single-hop L2.

**Kube-proxy replacement.** Cilium's eBPF data path that replaces the
`kube-proxy` component. Implements ClusterIP/NodePort/LoadBalancer service
load balancing in eBPF, bypassing iptables and conntrack overhead.

**LBIPAM.** Cilium's LoadBalancer IPAM — allocates LB IPs from
`CiliumLoadBalancerIPPool` resources and assigns them to Service objects.

**Lease (Kubernetes).** A resource type used for leader election. Cilium
creates one per L2-announced service IP (`cilium-l2announce-<ns>-<svc>`)
in `kube-system`; the holder is the elected speaker.

**listen range (BGP).** FRR config that accepts BGP from any host within
the given CIDR asserting the configured peer-group's AS. Used here so new
worker nodes can BGP-peer without explicit per-node config on the UCGF.

**Multicast / mDNS.** Multicast DNS (`*.local`) for service discovery —
AirPlay, Bonjour, HomeKit, Snapcast. Doesn't cross VLAN boundaries unless
explicitly reflected by the router (UniFi mDNS reflector). Relevant for
the Phase F.1 IoT VLAN segmentation plan.

**Next-hop.** The intermediate router/host a packet should be sent to in
order to reach its destination. BGP advertisements include the originating
peer's IP as the next-hop.

**NodePort.** A Kubernetes Service type that exposes a service on a
randomly-allocated port (default range 30000–32767) on every node. Not
used as primary ingress here; the gateway uses LoadBalancer instead.

**Pod CIDR.** The IP range from which Cilium allocates pod IPs. Set to
`10.244.0.0/16`, sliced into `/24` per node.

**Prefix-list.** FRR/BGP filter resource that matches a set of prefixes.
Used here to restrict inbound BGP announcements to `/32`s within the LB
pool subnet.

**Route-map.** FRR/BGP filter resource that combines prefix-lists and
attribute manipulations. Used here for inbound (`K8S-LB-IN` permits valid
LB `/32`s) and outbound (`DENY-ALL` denies everything — UCGF advertises
nothing back to the cluster).

**SNAT (Source NAT).** Rewriting the source IP of an outbound packet to
the egress interface's IP. Cilium does this for pod → external traffic
(masquerading) so external services see the node IP, not the pod IP.

**Split-horizon DNS.** Same hostname resolves to different IPs depending on
who's asking. Implemented here by AdGuard's wildcard rewrite intercepting
public Cloudflare DNS for LAN clients. See
`docs/architecture/dns-strategy.md`.

**STP (Spanning Tree Protocol).** L2 loop prevention. The Pro XG 48 PoE is
the configured root bridge. STP TCNs (Topology Change Notifications) cause
MAC table flushes — when emitted spuriously by a faulty switch, they cause
subnet-wide blackouts. See incident `2026-03-10`.

**TCN (Topology Change Notification).** STP message indicating "the
forwarding topology changed; flush MAC tables." Should be rare in steady
state.

**TLS termination.** Decrypting TLS at an intermediate proxy (here: the
Cilium gateway running Envoy) so the proxy can inspect the request. After
termination, the proxy may re-encrypt to the backend or send plaintext.

**Trunk port.** A switch port carrying frames for multiple VLANs, with
802.1Q tags identifying which VLAN each frame belongs to. Inter-switch
uplinks are typically trunks.

**Untagged port (access port).** A switch port assigned to one VLAN; frames
arrive without 802.1Q tags. End-device ports are typically untagged.

**VLAN (Virtual LAN).** L2 segmentation. Each VLAN is its own broadcast
domain with its own subnet. Tags are 12 bits (VLAN IDs 1–4094). The home
network has 6 VLANs (Core, Lab, Security, Family, Guest, IoT) — see
[addressing.md](addressing.md) for the full table.

**VXLAN.** L2-over-UDP encapsulation. Cilium uses it for pod-to-pod traffic
between nodes — pod IPs are encapsulated and tunneled over the LAN, so the
underlying network never sees pod CIDRs.

## Homelab-specific terms

**AdGuard (AdGuard Home).** The cluster's DNS server + ad/tracker filter.
Runs in the `adguard-prod` namespace as a 2-replica StatefulSet behind two
LoadBalancer service IPs: `10.42.2.43` (primary) and `10.42.2.45`
(secondary). Distinct sharing-key annotations force the two services onto
distinct IPs. UCGF distributes both as DHCP DNS option 6 to every LAN VLAN.

**`burntbytes.com`.** The public domain. Cloudflare-authoritative. AdGuard
intercepts `*.burntbytes.com` for LAN clients via wildcard rewrite.

**Cilium.** The CNI. Provides networking, BGP, L2 announcements, Gateway
API, NetworkPolicy enforcement, and observability via Hubble.

**Cloudflare Tunnel (`cloudflared`).** Persistent outbound QUIC tunnel
from a pod inside the cluster to Cloudflare's edge. Provides public
ingress without port-forwarding. Config in `apps/base/cloudflare-tunnel/`.

**hestia.** TrueNAS GPU server at `10.42.2.10`. Hosts vLLM/llama.cpp for
local LLM inference, signal-cli-rest-api for the Hermes Signal bot, and
serves iSCSI for non-Synology PVCs.

**`melodic-muse`.** The Talos cluster name.

**Synology.** NAS at `10.42.2.11`. iSCSI target backing CNPG PVCs.

**UCGF (UniFi Cloud Gateway Fiber).** The router at `10.42.2.1`. Runs
UniFi OS, FRRouting (FRR) for BGP, dnsmasq for DHCP. Single point of
failure for cross-subnet traffic.

**Lab VLAN (`br2`).** `10.42.2.0/24`. Cluster nodes, hestia, Synology,
wired client devices, and the LB IP pool all share this VLAN currently.

**Family VLAN (`br4`).** `10.42.4.0/24`. Wireless clients.

**Other VLANs.** Core (`br0`, `10.42.1.0/24` UniFi mgmt), Security (`br3`,
`10.42.3.0/24` cameras), Guest (`br6`), IoT (`br7`, `10.42.7.0/24`).

**`10.42.2.40` / `home-c-pool`.** The default LB IP pool for production
gateway and AdGuard (`.43`/`.45`).

## Acronyms quick-reference

| Term | Expansion |
|---|---|
| AS | Autonomous System (BGP) |
| ASN | AS Number |
| BGP | Border Gateway Protocol |
| BPF / eBPF | (extended) Berkeley Packet Filter |
| CIDR | Classless Inter-Domain Routing (IP prefix notation) |
| CNI | Container Network Interface |
| CRD | Custom Resource Definition (Kubernetes) |
| ECMP | Equal-Cost Multi-Path |
| FRR | FRRouting |
| iSCSI | Internet Small Computer Systems Interface |
| L2 / L3 / L4 | OSI layers 2, 3, 4 |
| LAG / LACP | Link Aggregation / LACP |
| LB | LoadBalancer |
| mDNS | Multicast DNS |
| NIC | Network Interface Controller |
| OIDC | OpenID Connect |
| PHY | Physical layer (NIC) |
| PVC | PersistentVolumeClaim |
| RSTP | Rapid Spanning Tree Protocol |
| SNI | Server Name Indication (TLS) |
| SPOF | Single Point of Failure |
| STP | Spanning Tree Protocol |
| TCN | Topology Change Notification |
| UCGF | UniFi Cloud Gateway Fiber |
| VIP | Virtual IP |
| VLAN | Virtual LAN |
| VXLAN | Virtual Extensible LAN |
| WAP | Wireless Access Point |
