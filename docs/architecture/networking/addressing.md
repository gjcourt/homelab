---
status: Stable
last_modified: 2026-05-06
---

# Addressing: VLANs, Subnets, IP Allocation

> **Scope.** Authoritative IP/VLAN map. For physical equipment see
> [physical-topology.md](physical-topology.md). For LB IP advertisement
> mechanics see [cluster-load-balancing.md](cluster-load-balancing.md).

## VLAN reference

| VLAN ID | Subnet | Purpose | Tag boundary |
|---|---|---|---|
| 2 | `10.42.2.0/24` | **Cluster + storage + wired clients.** Talos nodes, hestia, Synology, Apple TV, HifiBerry, RPi, etc. Also currently the LB pool. | UCGF wired access ports |
| 4 | `10.42.4.0/24` | Wireless clients (laptops, phones) | WAP SSIDs |

> **As-of caveat.** This table reflects the VLAN structure I have direct
> evidence for. Other VLANs may exist on the UCGF (guest WiFi, IoT, work) —
> verify with the UniFi UI (Settings → Networks). When confirmed, add rows
> here and update the `homelab.io/<x>` lookups in the plan docs.

## Subnet allocation map

| Subnet | Use | Notes |
|---|---|---|
| `10.42.2.0/24` | VLAN 2 — physical hosts + LB pool overlap | Sharing this `/24` with the LB pool is the root cause of the 2026-05-05 incident. Migration target: `10.42.3.0/24` for LB. |
| `10.42.4.0/24` | VLAN 4 — wireless clients | |
| `10.42.3.0/24` | (planned) dedicated LB pool | Plan `2026-05-06-network-resilience-and-bgp-completion.md` Phase D |
| `10.42.20.0/24` | (planned) IoT VLAN | Plan Phase F.1 |
| `10.244.0.0/16` | Cluster pod CIDR | Cilium IPAM (kubernetes mode); each node gets a `/24` slice (`.0/24`, `.1/24`, … `.5/24` for the 6 nodes) |
| `10.96.0.0/12` | Kubernetes service CIDR | ClusterIP allocation; not visible outside the cluster |

## Static IP allocation on `10.42.2.0/24`

Sorted numerically. Numbers below are mgmt IPs (the device itself), not
service VIPs.

| IP | Device / Role | Notes |
|---|---|---|
| `10.42.2.1` | UCGF (router/gateway) | DHCP server, DNS forwarder upstream, BGP AS 65100 |
| `10.42.2.10` | hestia (TrueNAS GPU server) | LLM inference + iSCSI provider |
| `10.42.2.11` | Synology NAS | iSCSI block storage |
| `10.42.2.19` | Apple TV | Wired |
| `10.42.2.20` | `talos-ykb-uir` (CP) | `k8sServiceHost` SPOF |
| `10.42.2.21` | `talos-2mz-rfj` (CP) | |
| `10.42.2.22` | `talos-v2l-hng` (CP) | |
| `10.42.2.23` | `talos-lmh-kyf` (worker) | BGP peer |
| `10.42.2.24` | `talos-18u-ski` (worker) | BGP peer |
| `10.42.2.25` | `talos-kot-7x7` (worker) | BGP peer |
| `10.42.2.30–37` | LB pool: `home-compute-pool` | Currently used by snapcast-prod (`.37`) |
| `10.42.2.38` | HifiBerry kitchen | |
| `10.42.2.39` | HifiBerry living-room | |
| `10.42.2.40–254` | LB pool: `home-c-pool` | Default gateways and AdGuard |
| `10.42.2.143` | `kitchen-pi` (RPi 4) | EEE disabled (see incident `2026-03-10`) |

> **Note on overlap.** `home-compute-pool` (`.30–.37`) and `home-c-pool`
> (`.40–.254`) sandwich the static range `.38–.39` (HifiBerry devices) and
> overlap conceptually with mgmt range `.20–.25`. The `kitchen-pi` at `.143`
> sits inside the LB pool range — Cilium's LBIPAM avoids it because it
> tracks allocations, but a static device that conflicts with a future LB IP
> would silently break.

## Active LoadBalancer service IPs (as of 2026-05-06)

These are the IPs **announced to the LAN** via BGP and L2:

| Service | IP | Pool |
|---|---|---|
| `default/cilium-gateway-app-gateway-production` | `10.42.2.40` | `home-c-pool` |
| `default/cilium-gateway-app-gateway-staging` | `10.42.2.42` | `home-c-pool` |
| `adguard-prod/adguard` | `10.42.2.43` | `home-c-pool` (pinned via `lbipam.cilium.io/sharing-key`) |
| `adguard-stage/adguard` | `10.42.2.44` | `home-c-pool` |
| `adguard-prod/adguard-dns-secondary` | `10.42.2.45` | `home-c-pool` (pinned) |
| `snapcast-stage/snapcast` | `10.42.2.41` | `home-c-pool` |
| `snapcast-prod/snapcast` | `10.42.2.37` | `home-compute-pool` |

Refresh:

```bash
kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace}{"/"}{.metadata.name}{": "}{.status.loadBalancer.ingress[*].ip}{"\n"}{end}' | sort
```

## ASN reference

| Entity | AS | Notes |
|---|---|---|
| UCGF | 65100 | Private 2-byte AS |
| Cluster | 65010 | Private 2-byte AS; advertised from each worker node |

Private 2-byte ASN range: `64512–65534`.

## DNS authority chain

| Layer | Resolver | Use |
|---|---|---|
| House-internal | AdGuard at `10.42.2.43` (primary), `10.42.2.45` (secondary) | Wildcard rewrites for `*.burntbytes.com`; ad/tracker filtering |
| Public authority | Cloudflare (manages `burntbytes.com`) | Authoritative for the public zone (mostly Cloudflare Tunnel CNAMEs) |
| Upstream from AdGuard | Cloudflare DNS, Quad9, etc. | AdGuard's configured upstream resolvers |

The split-horizon — same hostname resolves to different IPs from inside vs
outside the LAN — is implemented entirely by AdGuard's wildcard rewrite. Per
[../dns-strategy.md](../dns-strategy.md) for the full mechanism.

## DHCP

UCGF runs the DHCP server. The lease range and reservations are configured
via the UniFi UI; verify with:

```bash
ssh root@10.42.2.1 'cat /run/dnsmasq.leases 2>/dev/null'
```

DHCP-distributed DNS is currently `[10.42.2.43, 10.42.2.45]`. Plan
`2026-05-06-network-resilience-and-bgp-completion.md` Phase C adds `1.1.1.1`
as a fallback resolver.
