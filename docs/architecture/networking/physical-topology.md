---
status: Stable
last_modified: 2026-05-06
---

# Physical Topology

> **Scope.** Physical equipment, port-level cabling concepts, VLAN tagging
> boundaries. For IP allocation see [addressing.md](addressing.md). For LB
> traffic flow see [cluster-load-balancing.md](cluster-load-balancing.md).

## Equipment inventory

| Role | Device | IP (mgmt) | Notes |
|---|---|---|---|
| Router / L3 gateway | UniFi Cloud Gateway Fiber (UCGF) | `10.42.2.1` | Runs UniFi OS, FRRouting (FRR) for BGP, dnsmasq for DHCP |
| Core switch | UniFi Pro XG 48 PoE | (UniFi-managed) | STP root bridge (priority 4096); uplink to UCGF; downlinks to access switches and end devices |
| Access switch | UniFi Switch (various) | (UniFi-managed) | Distribution to wired endpoints |
| Wireless | UniFi WAPs | (UniFi-managed) | Tag wireless SSIDs onto wireless VLAN(s) |
| Removed | UniFi Flex 2.5G PoE | n/a | Removed 2026-03-10 — caused STP TCN storms; see `docs/operations/incidents/2026-03-10-rpi-stp-tcn-blackouts.md` |

> The full UniFi device tree is the source of truth — `ssh root@10.42.2.1`
> then UniFi UI for live device inventory. This table tracks the
> roles/relationships, not transient device counts.

## Cluster nodes

6-node Talos cluster (`melodic-muse`). All on the Lab VLAN (`br2`,
`10.42.2.0/24`):

| Node | Role | Mgmt IP | Hardware notes |
|---|---|---|---|
| `talos-ykb-uir` | control-plane | `10.42.2.20` | Hardcoded as `k8sServiceHost` in Cilium values (SPOF) |
| `talos-2mz-rfj` | control-plane | `10.42.2.21` | |
| `talos-v2l-hng` | control-plane | `10.42.2.22` | |
| `talos-lmh-kyf` | worker | `10.42.2.23` | BGP peer |
| `talos-18u-ski` | worker | `10.42.2.24` | BGP peer |
| `talos-kot-7x7` | worker | `10.42.2.25` | BGP peer |

> Talos hostnames are auto-generated with random suffixes; a node re-image
> produces a new hostname. Any Kubernetes selector that references hostnames
> must be reapplied post-reimage. The L2 announcer's speaker-pool labels
> ([cluster-load-balancing.md](cluster-load-balancing.md#stable-speaker-pool-labels))
> avoid this trap by selecting on a custom label instead.

## Other infrastructure devices

| Device | IP | VLAN | Role |
|---|---|---|---|
| hestia (TrueNAS GPU server) | `10.42.2.10` | 2 | LLM inference (vLLM/llama.cpp), Signal-CLI bridge, iSCSI provider for non-Synology PVCs |
| Synology NAS | `10.42.2.11` | 2 | iSCSI block storage backing CNPG PVCs; DSM at port 5000 |

Both are kept on VLAN 2 with the cluster because they participate in iSCSI
sessions terminated by the cluster's Synology CSI driver. Moving them to a
separate VLAN would route iSCSI through the UCGF, adding latency and a hop
for every SCSI op.

## Wired client devices

These are user/service devices on the Lab VLAN that share L2 with the
cluster. Listed in the BGP plan as the gating constraint for ever removing
L2 announcements (`docs/plans/2026-05-06-network-resilience-and-bgp-completion.md`).

| Device | IP | Notes |
|---|---|---|
| Apple TV | `10.42.2.19` | Wired via switch; DNS configured manually in tvOS UI |
| HifiBerry kitchen | `10.42.2.38` | Snapcast client; static IP via OS config |
| HifiBerry living-room | `10.42.2.39` | Snapcast client |
| `kitchen-pi` | `10.42.2.143` | Raspberry Pi 4; `bcmgenet` NIC with EEE disabled (see incident `2026-03-10`) |

## Wireless

Wireless clients (Macs, phones, tablets) sit on the Family VLAN
(`br4`, `10.42.4.0/24`). They route to cluster services through the UCGF —
see [traffic-flows.md](traffic-flows.md) for the path.

## Other VLANs

The UCGF terminates four additional VLANs that don't participate in the
cluster's data plane but are part of the broader house network:

| VLAN | Subnet | Purpose |
|---|---|---|
| Core (`br0`) | `10.42.1.0/24` | UniFi infrastructure mgmt — switches, APs, gateway control plane |
| Security (`br3`) | `10.42.3.0/24` | Cameras and security devices |
| Guest (`br6`) | `10.42.6.0/24` | Guest WiFi |
| IoT (`br7`) | `10.42.7.0/24` | IoT devices (currently lightly populated; migration target for the wired client devices above) |

See [addressing.md](addressing.md) for the full VLAN/subnet map.

## VLAN tagging boundaries

VLAN tagging is applied at:

1. **UCGF LAN ports / bridges** — each VLAN is its own bridge (`br0`, `br2`, `br3`, `br4`, `br6`, `br7`) with the UCGF holding `.1` of each `/24`.
2. **WAP SSIDs** — each SSID maps to a VLAN tag; wireless clients are placed on the configured VLAN.
3. **Switch ports** — typically untagged on the configured VLAN; trunk uplinks tag everything.

The cluster nodes do not use 802.1Q on their NICs — Talos sees VLAN-untagged
frames on whichever access port the UCGF puts them on (the Lab VLAN).

## Spanning tree

- Pro XG 48 PoE has bridge priority 4096 (root bridge).
- Edge ports on end-device switch ports (recommended; partial — see incident `2026-03-10` Fix 2).
- TC Guard on inter-switch uplinks is a future hardening item.

## Cabling philosophy

- **Star topology** — every endpoint terminates at a switch port, no daisy-chained access switches.
- **No long copper runs over 90m.** Beyond that, use fiber.
- **Cluster nodes home-run to the core switch.** Each Talos node has a single 2.5GbE link.
- **No physical redundancy in current build.** Single uplink per access switch; single NIC per node. Network HA depends on protocol-layer redundancy (ECMP in BGP, lease re-election in L2), not redundant cabling.

## Things that intentionally don't exist (yet)

- **No dedicated storage VLAN.** iSCSI shares VLAN 2 with everything else.
  Acceptable for current load (~1 Gbps peak); revisit if storage saturates the LAN.
- **No dedicated mgmt VLAN for switches/APs.** Their mgmt IPs sit on VLAN 2.
- **No physical link aggregation (LAG/LACP).** Single 2.5GbE per node is sufficient
  for current per-node throughput.
- **No second WAN.** UCGF has one fiber uplink. ISP outage = full Internet outage.
