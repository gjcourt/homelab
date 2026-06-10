---
status: Stable
last_modified: 2026-05-06
---

# Networking Architecture

> **Audience.** This folder is the canonical home for networking architecture
> docs covering the homelab cluster (`melodic-muse`) and the broader house
> network it runs on. It is written for two consumers:
>
> - **LLMs** building context for cluster work — load `README.md` first, then
>   the topic file relevant to the question.
> - **Human operators** debugging or planning network changes — start at
>   `README.md` for the mental model, then drill into the specific doc.
>
> Each file is self-contained: cross-referenced where useful, but not
> dependent on chain-loading. As-of dates appear in every frontmatter so a
> reader knows when a fact was last verified.

## Mental model

The house has a single physical LAN segment terminating at a UniFi Cloud
Gateway Fiber (UCGF) at `10.42.2.1`. The UCGF terminates 6 VLANs (Core,
Lab, Security, Family, Guest, IoT), each on its own bridge interface and
`/24`: Lab (`br2`, `10.42.2.0/24`) hosts the cluster, storage, and most
wired devices; Family (`br4`, `10.42.4.0/24`) hosts wireless; the others
serve their named purposes. The LoadBalancer IP pool currently overlaps
the Lab VLAN — the source of the 2026-05-05 wired-device incident.

Inside the cluster, Cilium runs as the CNI with VXLAN tunneling for
pod-to-pod traffic, BGP for advertising LoadBalancer IPs to the UCGF, and
L2 announcements for same-subnet ARP fallback. External traffic enters via
Cloudflare Tunnel (no port-forwarding by default, see invariant 3 for the one exception); internal traffic resolves through
AdGuard (distributed via DHCP option 6 to every VLAN as
`[10.42.2.43, 10.42.2.45]`), gets a wildcard-rewritten LB IP for
`*.burntbytes.com`, and terminates TLS at a Cilium Gateway running Envoy
on a worker node.

## Quick map

| Question | Doc |
|---|---|
| What does the physical network look like? Which switches, APs, cables? | [physical-topology.md](physical-topology.md) |
| What VLANs exist? What IP ranges? Where do specific devices live? | [addressing.md](addressing.md) |
| How do LoadBalancer IPs get advertised? L2 vs BGP, why both? | [cluster-load-balancing.md](cluster-load-balancing.md) |
| How does a packet from device X reach service Y? | [traffic-flows.md](traffic-flows.md) |
| What does <term> mean? | [glossary.md](glossary.md) |
| How does DNS resolution work (split-horizon)? | [../dns-strategy.md](../dns-strategy.md) |
| How is ingress authentication wired (Authelia + Envoy ext_authz)? | [../gateway-auth.md](../gateway-auth.md) |
| Detailed Cilium config reference (Helm values, BGP CRDs)? | [../../reference/cilium.md](../../reference/cilium.md) |
| Why does the current state look like this — what migrations happened? | [../../plans/](../../plans/) (search for `network`, `bgp`, `migration`) |

## Top-level invariants (as of 2026-05-17)

These are facts that should hold true in steady state. Violations indicate a
bug or incomplete migration.

1. **Single physical LAN, segmented by VLAN.** All wired ports terminate on
   the UCGF or a UCGF-managed switch. VLAN tagging is the only network
   isolation mechanism at L2. Six VLANs are configured (Core, Lab, Security,
   Family, Guest, IoT) — see [addressing.md](addressing.md).
2. **The cluster is on the Lab VLAN.** All 6 Talos nodes have IPs in
   `10.42.2.20–.25`. The UCGF gateway is `10.42.2.1`.
3. **External traffic enters via Cloudflare Tunnel only — with one documented
   exception.** No general port-forwarding on the UCGF; no router-side NAT
   for inbound HTTP/HTTPS. The tunnel (`apps/base/cloudflare-tunnel/`)
   terminates inside the cluster and routes to the production gateway.

   **Exception:** a single TCP+UDP port forward exists for the qBittorrent
   peer port on the Synology NAS (`alcatraz`, `10.42.2.11`). BitTorrent
   requires inbound peer connectivity to upload to firewalled peers and to
   participate in DHT, and Cloudflare Tunnel cannot proxy non-HTTP traffic
   on retail accounts. The forward is narrow (single port, single LAN
   target) and the exposure is acknowledged: the WAN IP becomes visible to
   every swarm peer, carrying DMCA-notice risk. Any additional port forwards
   must be added to this list and justified here.
4. **Internal hostnames resolve to LAN IPs via AdGuard split-horizon DNS.**
   `*.burntbytes.com` rewrites to a LB IP on the LAN; public Cloudflare DNS
   exists for some hosts but is overridden internally.
5. **TLS terminates at the Cilium Gateway.** All `*.burntbytes.com` requests
   land at an Envoy proxy on a worker node, which terminates TLS using
   cert-manager-issued certificates and forwards plaintext or re-encrypted
   traffic to backend pods.
6. **LB IPs are advertised by both L2 and BGP simultaneously.** Cilium
   announces every LoadBalancer IP via L2 (ARP) for same-subnet clients and
   via BGP (3 worker peers, ECMP) to the UCGF for cross-subnet clients. See
   [cluster-load-balancing.md](cluster-load-balancing.md) for why both are
   currently required.
7. **GitOps-driven cluster network state.** Every Cilium resource lives in
   `infra/configs/cilium/` and `infra/controllers/cilium/`. Out-of-band
   changes drift back on the next Flux reconcile.

## Threat model — what this folder intentionally exposes

This is a public GitHub repo. The networking docs publish concrete IPs,
VLAN layout, service-to-IP mappings, cluster node hostnames, and storage
backend addresses. That is intentional: the threat model assumes
**perimeter defenses hold**, and internal network reconnaissance is not
something we expect to defend by topology obscurity.

What is in scope (i.e. what defends the network):

- **Perimeter:** Cloudflare Tunnel handles all inbound HTTP/HTTPS. The UCGF
  firewall blocks unsolicited inbound except for the one documented port
  forward (qBittorrent peer port → alcatraz; see invariant 3).
- **Public services:** Authelia SSO + per-service authentication; TLS
  terminates at the Cilium gateway with cert-manager-issued certificates.
- **Internal segmentation:** CiliumNetworkPolicy gates pod-to-pod traffic
  (~45 active policies); Kubernetes RBAC gates API access; SOPS encrypts
  secrets at rest in the repo.
- **Auditability:** GitOps means every change is in the commit history;
  Hubble logs every flow.

What is **not** defended by these docs:

- Internal network recon by an attacker already on the LAN. Anyone with a
  foothold can `nmap` the same map in seconds, browse mDNS, or read the
  Cilium API for the full service inventory. Topology obscurity adds no
  meaningful defense here.
- Subdomain enumeration. Every Let's Encrypt cert issued by cert-manager
  goes into Certificate Transparency logs (publicly searchable via
  `crt.sh`), so listing service hostnames in docs reveals nothing new.

What we deliberately keep **out** of the public repo:

- Credentials, API keys, tokens, private keys, certificates.
- SOPS-decrypted secrets (the `.sops.yaml` key reference is committed; the
  decrypted contents never are).
- MAC addresses, DHCP reservation tables, and any per-device identifiers
  that would aid hardware-targeted attacks.
- Specific firmware versions of network gear.

If you're forking this repo or borrowing the architecture, the published
detail level is appropriate **only** if your security model also leans on
auth/perimeter rather than topology secrecy. If you need topology
secrecy, move addressing.md and the per-device inventory into a private
repo.

## Out of scope for this folder

- **Per-app HTTPRoute and Service definitions** — see `apps/base/<app>/`.
- **Cilium internals beyond architecture** (BPF maps, eBPF programs) — see
  `docs/reference/cilium.md`.
- **Authentication flow** — see [../gateway-auth.md](../gateway-auth.md).
- **Storage networking (iSCSI/NFS)** — see `docs/reference/storage.md`.
- **Incident postmortems** — see `docs/operations/incidents/`.
- **Forward plans** — see `docs/plans/`.
