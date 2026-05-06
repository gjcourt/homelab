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

## One-paragraph mental model

The house has a single physical LAN segment terminating at a UniFi Cloud
Gateway Fiber (UCGF) at `10.42.2.1`. The UCGF tags traffic into VLANs at the
edge: cluster + wired devices share VLAN 2 (`10.42.2.0/24`), wireless clients
sit on a separate VLAN (`10.42.4.0/24`), and the LB IP pool currently overlaps
VLAN 2. Inside the cluster, Cilium runs as the CNI with VXLAN tunneling for
pod-to-pod traffic, BGP for advertising LoadBalancer IPs to the UCGF, and L2
announcements for same-subnet ARP fallback. External traffic enters via
Cloudflare Tunnel (no port-forwarding); internal traffic resolves through
AdGuard split-horizon DNS to internal LB IPs and terminates TLS at a Cilium
Gateway running Envoy.

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

## Top-level invariants (as of 2026-05-06)

These are facts that should hold true in steady state. Violations indicate a
bug or incomplete migration.

1. **Single physical LAN, segmented by VLAN.** All wired ports terminate on
   the UCGF or a UCGF-managed switch. VLAN tagging is the only network
   isolation mechanism at L2.
2. **The cluster is on one /24 (VLAN 2).** All 6 Talos nodes have IPs in
   `10.42.2.20–.25`. The UCGF gateway is `10.42.2.1`.
3. **External traffic enters via Cloudflare Tunnel only.** No port-forwarding
   on the UCGF; no router-side NAT for inbound. The tunnel
   (`apps/base/cloudflare-tunnel/`) terminates inside the cluster and routes
   to the production gateway.
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

## Out of scope for this folder

- **Per-app HTTPRoute and Service definitions** — see `apps/base/<app>/`.
- **Cilium internals beyond architecture** (BPF maps, eBPF programs) — see
  `docs/reference/cilium.md`.
- **Authentication flow** — see [../gateway-auth.md](../gateway-auth.md).
- **Storage networking (iSCSI/NFS)** — see `docs/reference/storage.md`.
- **Incident postmortems** — see `docs/operations/incidents/`.
- **Forward plans** — see `docs/plans/`.
