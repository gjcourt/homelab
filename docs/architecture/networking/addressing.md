---
status: Stable
last_modified: 2026-05-06
---

# Addressing: VLANs, Subnets, IP Allocation

> **Scope.** Authoritative IP/VLAN map for the present-day network. For
> physical equipment see [physical-topology.md](physical-topology.md). For
> LB IP advertisement mechanics see
> [cluster-load-balancing.md](cluster-load-balancing.md). Forward state
> (planned subnets, planned VLAN moves) lives in `docs/plans/` — see
> [Forward links](#forward-links) at the end of this doc.

## VLAN reference

The UCGF terminates 6 VLANs, each on its own bridge interface. Every VLAN
has its own `/24`. UniFi's display name is shown in the "Name" column.

| Bridge | VLAN name | Subnet | Purpose | DHCP range |
|---|---|---|---|---|
| `br0` | Core | `10.42.1.0/24` | UniFi infrastructure (switches, APs, gateway mgmt) | `.100–.199` |
| `br2` | Lab | `10.42.2.0/24` | Cluster nodes, storage, wired clients, **and currently the LB pool** | `.6–.254` |
| `br3` | Security | `10.42.3.0/24` | Cameras and security devices | `.6–.254` |
| `br4` | Family | `10.42.4.0/24` | Wireless clients (laptops, phones, tablets) | `.6–.254` |
| `br6` | Guest | `10.42.6.0/24` | Guest WiFi | `.6–.254` |
| `br7` | IoT | `10.42.7.0/24` | IoT devices (existing — currently lightly populated) | `.6–.254` |

Refresh the live VLAN list:

```bash
ssh root@10.42.2.1 'ip -br addr show | grep "br[0-9]"'
```

## Subnet allocation map

| Subnet | Use | Notes |
|---|---|---|
| `10.42.1.0/24` | Core VLAN — UniFi infrastructure | Switches, APs; locked-down management plane |
| `10.42.2.0/24` | Lab VLAN — cluster + storage + LB pool | Sharing the `/24` between cluster mgmt and LB pool is the root cause of the 2026-05-05 wired-device incident |
| `10.42.3.0/24` | Security VLAN — cameras | |
| `10.42.4.0/24` | Family VLAN — wireless clients | Cross-subnet to LB IPs; routes via UCGF/BGP |
| `10.42.5.0/24` | (unallocated; reserved for future LB pool migration) | Plan: `2026-05-06-network-resilience-and-bgp-completion.md` Phase D |
| `10.42.6.0/24` | Guest VLAN | Guest WiFi |
| `10.42.7.0/24` | IoT VLAN | Currently lightly populated; the migration target for client devices currently on the Lab VLAN |
| `10.244.0.0/16` | Cluster pod CIDR | Cilium IPAM (Kubernetes mode); each node gets a `/24` slice |
| `10.96.0.0/12` | Kubernetes service CIDR | ClusterIP allocation; not visible outside the cluster |

## DNS resolution by VLAN

Verified from `/run/dnsmasq.dhcp.conf.d/*.conf` on the UCGF — every VLAN's
DHCP option 6 (DNS server) distributes AdGuard directly:

| VLAN | DHCP-distributed resolvers |
|---|---|
| All 6 LAN VLANs (Core, Lab, Security, Family, Guest, IoT) | `[10.42.2.43, 10.42.2.45]` |

This means **every DHCP-managed client on the LAN points at AdGuard
directly.** The UCGF is not in the DNS-forwarding path for client queries;
clients send DNS queries to AdGuard and reach it via L2 (if same-subnet
with the LB IP) or via BGP-installed routes through the UCGF (if
cross-subnet). See `traffic-flows.md` Flow 1 and Flow 2 for the two paths.

## Static IP allocation on `10.42.2.0/24` (Lab VLAN)

Sorted numerically. Numbers below are mgmt IPs (the device itself), not
service VIPs. Refresh from the UCGF's DHCP reservations:

```bash
ssh root@10.42.2.1 'cat /run/dnsmasq.dhcp.conf.d/* 2>/dev/null | grep "dhcp-host=set:net_Lab" | awk -F"," "{print \$5}" | sort -V'
```

| IP | Device / Role | Notes |
|---|---|---|
| `10.42.2.1` | UCGF (router/gateway) | DHCP server, FRR for BGP (AS 65100), bridge `br2` |
| `10.42.2.10` | hestia (TrueNAS storage + compute) | iSCSI/NFS provider via `democratic-csi`, GHA deploy runner, Immich photo-backup, qBittorrent. **No GPUs since 2026-05-16.** See [hosts/hestia/](../../../hosts/hestia/README.md). |
| `10.42.2.11` | Synology NAS | iSCSI target backing CNPG PVCs (`csi.san.synology.com`) |
| `10.42.2.13` | hestia IPMI (ASRock Rack BMC) | Out-of-band management; switch port 48, DHCP on the Lab VLAN |
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
| `10.42.2.40–254` | LB pool: `home-c-pool` | Default — gateways and AdGuard |
| `10.42.2.143` | `kitchen-pi` (RPi 4) | EEE disabled (see incident `2026-03-10`) |

> **Note on overlap.** `home-compute-pool` (`.30–.37`) and `home-c-pool`
> (`.40–.254`) sandwich the static range `.38–.39` (HifiBerry devices).
> The `kitchen-pi` at `.143` sits inside the `home-c-pool` range — Cilium's
> LBIPAM avoids it because it tracks DHCP-reserved IPs as unavailable, but
> a hand-allocated IP that conflicts with a future LB IP would silently
> break.

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
outside the LAN — is implemented entirely by AdGuard's wildcard rewrite. See
[../dns-strategy.md](../dns-strategy.md) for the full mechanism.

## Forward links

The following subnet/VLAN allocations are **not yet live** but are
referenced by active plans. Tracking them here so the plan and the
addressing map don't drift apart.

| Allocation | Status | Plan |
|---|---|---|
| `10.42.5.0/24` reserved as dedicated LB-only subnet | Planned | [Phase D](../../plans/2026-05-06-network-resilience-and-bgp-completion.md) |
| Lab-VLAN client devices (Apple TV, HifiBerry, kitchen-pi) migrate to existing IoT VLAN `10.42.7.0/24` | Planned | [Phase F.1](../../plans/2026-05-06-network-resilience-and-bgp-completion.md) |
