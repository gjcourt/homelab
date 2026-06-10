---
status: Stable
last_modified: 2026-05-06
---

# Traffic Flows

> **Scope.** Annotated walkthroughs of the most common request paths through
> the network. Each flow names the protocol-layer transitions, the
> infrastructure component handling each step, and the failure mode if that
> step breaks. For mechanism details see
> [cluster-load-balancing.md](cluster-load-balancing.md). For IP map see
> [addressing.md](addressing.md).

## Conventions used in the diagrams

```
[Component]              — a host or service
  ↓ (action / protocol)  — operation taking place
[Component]              — next host
```

Solid arrows: actual traffic. Side-comments: what's being decided at each
hop. "Path of least mystery" — every transition names the resolver,
forwarder, or LB doing the work.

---

## Flow 1: Wireless client → internal HTTPS service

**Scenario.** Mac on the Family VLAN (`br4`, `10.42.4.x`) opens
`https://grafana.burntbytes.com`.

The UCGF's DHCP server distributes AdGuard `[10.42.2.43, 10.42.2.45]`
directly as the resolvers for **every** LAN VLAN (verified from
`/run/dnsmasq.dhcp.conf.d/*.conf`). The UCGF is **not** in the DNS
forwarding path — clients send DNS queries directly to AdGuard via
routed delivery.

```
[Mac, 10.42.4.50]
  ↓ DNS query for grafana.burntbytes.com → 10.42.2.43 (DHCP-distributed)
  ↓ "10.42.2.43 is not on my /24 (10.42.4.0/24); send to default gateway"
  ↓ ARP for 10.42.4.1 (UCGF Family-VLAN interface br4)
  ↓ Frame to UCGF
[UCGF]
  ↓ Routing-table lookup for 10.42.2.43/32 → BGP next-hop on talos-X (worker)
  ↓ forwards frame to talos-X
[talos-X cilium-agent BPF]
  ↓ kube-proxy replacement: 10.42.2.43:53 → adguard-0 or adguard-1 pod
  ↓ (VXLAN if the pod is on another node)
[adguard pod]
  ↓ Wildcard rewrite *.burntbytes.com → 10.42.2.40 (see ../dns-strategy.md)
  ↓ DNS reply: 10.42.2.40
  → reverse path back to Mac
[Mac]
  ↓ Now opens TCP to 10.42.2.40:443
  ↓ "10.42.2.40 is not on my /24; send to default gateway"
  ↓ TCP SYN frame to UCGF
[UCGF]
  ↓ BGP route lookup: 10.42.2.40/32 → next-hops [.23, .24, .25] ECMP, picks one
  ↓ forwards frame to (e.g.) talos-lmh-kyf (.23), arriving on eno1
[talos-lmh-kyf cilium-agent BPF]
  ↓ kube-proxy replacement: dest 10.42.2.40:443 → cilium-envoy pod (any node)
  ↓ tunnels via VXLAN if envoy is on another node
[cilium-envoy on talos-X]
  ↓ TLS handshake (SNI = grafana.burntbytes.com)
  ↓ cert-manager certificate served (Let's Encrypt)
  ↓ HTTPRoute match: hostname grafana.burntbytes.com → grafana.monitoring.svc
  ↓ forwards plaintext (or re-encrypted) to grafana pod
[grafana pod]
  ↓ HTTP response
  → reverse path: VXLAN → cilium-envoy → BPF → eno1 → UCGF → Mac
```

> **Note on DNS path.** The DNS query and the HTTPS connection follow
> independent paths: both end at the cluster, but they hit different
> services (AdGuard vs gateway), and each has its own ECMP next-hop choice
> on the UCGF.

### What can break

| Step | Failure | Symptom |
|---|---|---|
| Mac → AdGuard | AdGuard pod not running or unreachable | DNS resolution fails; depending on fallback config (Phase C of the active plan), client may use 1.1.1.1 |
| UCGF → BGP route | All 3 worker BGP sessions down | UCGF has no route; SYN times out |
| UCGF → worker | One worker BGP session down | UCGF removes from ECMP set after hold-time; remaining workers handle traffic |
| Worker → cilium-envoy | No envoy pod ready | 503 from gateway |
| Envoy → backend | HTTPRoute misconfigured | 404 / 502 from envoy |

---

## Flow 2: Wired VLAN-2 client → DNS query

**Scenario.** Apple TV on VLAN 2 (`10.42.2.19`) needs to resolve
`example.com`.

```
[Apple TV, 10.42.2.19]
  ↓ Configured DNS: 10.42.2.43 (manually set in tvOS)
  ↓ "10.42.2.43 is on my /24 (10.42.2.0/24); ARP directly"
  ↓ ARP broadcast: "Who has 10.42.2.43?"
[Cilium L2 speaker — the elected node for this LB IP, e.g. talos-X]
  ↓ Cilium L2 announcer responds: "10.42.2.43 is at <my MAC>"
[Apple TV]
  ↓ Sends DNS query frame to talos-X's MAC
[talos-X BPF — the L2 speaker for this IP]
  ↓ kube-proxy replacement: 10.42.2.43:53 → adguard-0 or adguard-1 pod
  ↓ forwards (possibly via VXLAN if adguard pod is on another node)
[adguard pod]
  ↓ resolves example.com (via configured upstreams)
  → reverse path
```

### What can break

| Step | Failure | Symptom |
|---|---|---|
| Apple TV → ARP | L2 announcer disabled or speaker absent | ARP times out; Apple TV cannot reach cluster — this is exactly the 2026-05-05 incident |
| Speaker death | Lease re-elected to another worker | ~5–15s outage during failover (lease tuning dependent) |
| BPF lookup | adguard service has no ready endpoints | Connection refused |

### Why BGP doesn't help here

The Apple TV's kernel sees `10.42.2.43` as same-subnet and never consults
the routing table. The UCGF's BGP route for `10.42.2.43/32` is irrelevant —
the frame never reaches the UCGF. **L2 is the only mechanism that works for
this path.**

---

## Flow 3: External Internet client → public HTTPS service (Cloudflare Tunnel)

**Scenario.** Friend on the open Internet visits
`https://home.burntbytes.com`.

```
[Internet client]
  ↓ DNS lookup → Cloudflare authoritative for burntbytes.com
  ↓ returns CNAME to <tunnel-id>.cfargotunnel.com → Cloudflare edge IP
  ↓ TCP SYN to Cloudflare edge:443
[Cloudflare edge]
  ↓ TLS termination (Cloudflare-issued cert)
  ↓ matches tunnel routing rule for hostname
  ↓ sends request through persistent QUIC tunnel
[cloudflared pod inside cluster]
  ↓ originates request to internal service (config: ingress to local URL)
  ↓ may target the production gateway directly: http://10.42.2.40
[Cilium gateway path]
  → same as Flow 1 from "TCP SYN to 10.42.2.40:443" onward, except
    cloudflared talks to it from inside the pod network
[backend pod]
  ↓ response → reverse tunnel → Cloudflare edge → Internet client
```

### Key properties

- **No port-forward on UCGF for HTTP/HTTPS ingress.** Inbound web traffic
  is impossible at the router; only the persistent outbound Cloudflare
  tunnel carries it. (One narrow exception exists for the qBittorrent peer
  port on alcatraz — see invariant 3 in [README.md](README.md). Not a
  web-traffic path; doesn't affect the flows in this document.)
- **Public DNS is Cloudflare-managed.** `burntbytes.com` zone records live
  in Cloudflare, mostly as CNAMEs to the tunnel.
- **Internal split-horizon overrides public DNS for LAN clients.** AdGuard
  intercepts `*.burntbytes.com` and returns the internal LB IP, keeping
  traffic on-LAN and avoiding the tunnel hop for internal access.
- **Authentication can layer at Cloudflare (Access) or at the cluster
  (Authelia).** See [../gateway-auth.md](../gateway-auth.md) for how
  Authelia integrates as an `ext_authz` filter on the Cilium gateway.

---

## Flow 4: Pod-to-pod communication

**Scenario.** A pod running on `talos-lmh-kyf` (`.23`) needs to call a
service `grafana.monitoring.svc.cluster.local:80` whose backing pod is on
`talos-kot-7x7` (`.25`).

```
[Pod on .23, 10.244.3.42]
  ↓ DNS query: grafana.monitoring.svc.cluster.local (CoreDNS in-cluster)
  ↓ returns ClusterIP, e.g. 10.96.55.10
  ↓ TCP SYN to 10.96.55.10:80
[Cilium BPF on .23]
  ↓ kube-proxy replacement: ClusterIP → backend pod IP (10.244.5.7 on .25)
  ↓ direct socket-level rewrite (no DNAT in iptables — eBPF replaces it)
  ↓ encapsulate in VXLAN to peer node .25
[VXLAN tunnel: .23 → .25]
[Cilium BPF on .25]
  ↓ decapsulate; deliver to local pod 10.244.5.7
[grafana pod]
  ↓ response → reverse VXLAN → BPF → originating pod
```

### Properties

- **VXLAN is internal-only.** The encap/decap happens between Cilium agents
  on the cluster nodes' `cilium_vxlan` interface; nothing on the LAN sees
  VXLAN.
- **No L2 or BGP involved.** This path bypasses both. LB IP advertisement
  is irrelevant for in-cluster traffic.
- **Pod CIDR is `10.244.0.0/16`**, sliced into `/24` per node.

---

## Flow 5: Cluster pod → external Internet

**Scenario.** A pod on `talos-18u-ski` (`.24`) calls
`https://api.openai.com`.

```
[Pod on .24, 10.244.4.55]
  ↓ TCP SYN to <openai IP>:443
[Cilium BPF on .24]
  ↓ no service IP match; egress rule
  ↓ Masquerading IPTables: SNAT source to .24 (the node's IP)
[Node eno1 → switch → UCGF]
  ↓ UCGF default route → ISP
[Internet]
```

### Properties

- **Egress source IP is the node's IP** (Masquerading enabled in Cilium
  values: `Masquerading: IPTables [IPv4: Enabled]`).
- **No special egress gateway.** Pods leave from whichever node they're
  scheduled on. Egress IP rotation = node selection.
- **Reverse DNS will not resolve to a pod hostname.** External services
  see one of `10.42.2.20–.25`, mapped (if at all) to nothing meaningful.

---

## Flow 6: iSCSI from cluster to storage

The cluster has **two** iSCSI providers, each via its own CSI driver and
StorageClass. Both targets sit on the Lab VLAN (`br2`, `10.42.2.0/24`)
alongside the cluster nodes, so the iSCSI path is single-hop L2 in both
cases — no UCGF, no BGP, no Cilium service IPs involved.

### 6a — Synology iSCSI (CNPG PVCs and most stateful workloads)

```
[CNPG pod on .23]
  ↓ block I/O → kernel iSCSI initiator on the node
[talos-lmh-kyf kernel]
  ↓ TCP to Synology at 10.42.2.11:3260
[switch L2 forwarding — same VLAN]
[Synology]
  ↓ serves block; btrfs-backed
```

CSI driver: `csi.san.synology.com` (Synology official CSI). StorageClasses:
`synology-iscsi`, `synology-iscsi-ephemeral`, `synology-nfs`.

### 6b — hestia (TrueNAS) iSCSI via democratic-csi

```
[Pod on .24]
  ↓ block I/O → kernel iSCSI initiator
[talos-18u-ski kernel]
  ↓ TCP to hestia at 10.42.2.10:3260
[switch L2 forwarding — same VLAN]
[hestia (TrueNAS)]
  ↓ serves block; ZFS-backed
```

CSI driver: `org.democratic-csi.truenas-iscsi`. StorageClasses:
`truenas-iscsi`, `truenas-iscsi-ephemeral`, `truenas-iscsi-ssd` (the SSD
variant uses a separate ZFS pool).

### Properties (both)

- **L2-direct delivery.** Cluster nodes and storage targets share the Lab
  VLAN broadcast domain. Single-hop frame from the node to the target.
- **No Cilium service IP.** CSI drivers target the storage device's mgmt
  IP directly; Cilium doesn't proxy iSCSI traffic.
- **PVC churn = iSCSI session churn.** Mass PVC re-attach (e.g. after a
  node reboot) creates many concurrent sessions on the target. Both
  Synology and TrueNAS handle ~32 concurrent sessions cleanly; beyond that,
  expect login retries.
- **Storage outage = pod outage.** No multi-target failover; if the
  Synology or hestia is offline, every PVC backed by it goes read-only or
  fails. See `docs/operations/incidents/2026-02-28-iscsi-mass-readonly-cnpg-loki-immich.md`.

---

## Quick "where am I in the path" lookup

When debugging "X can't reach Y," start by asking:

1. **What's the source's subnet?** Check `ip a` on the client.
2. **What's the destination IP?** Resolve via the actual resolver the
   client uses, not your own.
3. **Are source and destination on the same `/24`?**
   - **Same** → L2 ARP path (Flow 2). Test: `arp -n <dest>` should resolve;
     if not, L2 announcer is broken.
   - **Different** → BGP routing path (Flow 1). Test: traceroute the path;
     UCGF should be hop 1; cluster worker should be hop 2.
4. **Does the client use AdGuard?** Test: `dig @<adguard-ip> example.com`.
5. **Does the gateway terminate TLS for the requested hostname?** Test:
   `curl -kv https://<lb-ip>` and check the cert SAN.
