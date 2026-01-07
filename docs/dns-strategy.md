# DNS Strategy: Split-Horizon Automation

To scale your homelab and automate DNS records without exposing staging IPs, we will implement a **Split-Horizon DNS** architecture using **ExternalDNS**.

## The Architecture

We will run **two** separate instances of ExternalDNS in the cluster:

1.  **Production** (`*.burntbytes.com`)
    - **Source**: Gateway API HTTPRoutes in `production` namespaces.
    - **Target**: Cloudflare DNS.
    - **Exposure**: Public (or effectively public) records.
2.  **Staging** (`*.stage.burntbytes.com`)
    - **Source**: Gateway API HTTPRoutes in `staging` namespaces.
    - **Target**: An in-cluster **CoreDNS** server (Internal).
    - **Exposure**: Private only. Records never leave your LAN.

## Component Failure Domain

### 1. Production Flow (Cloudflare)
`App (HTTPRoute)` → `ExternalDNS (Prod)` → `Cloudflare API`

- **Result**: `myapp.burntbytes.com` resolves globally to your WAN IP (or Tunnel) or Internal VIP depending on config.
- **Benefit**: Zero manual Cloudflare edits.

### 2. Staging Flow (Internal)
`App (HTTPRoute)` → `ExternalDNS (Stage)` → `In-Cluster CoreDNS (RFC2136)` ← `AdGuard Home (Forwarding)`

- **Why an intermediate CoreDNS?**
    - AdGuard Home is great for filtering/clients but lacks a standard RFC2136 interface for frequent automation.
    - We deploy a small CoreDNS configured as authoritative for `stage.burntbytes.com`.
    - We configure your main AdGuard to **forward** `/*.stage.burntbytes.com/` queries to this CoreDNS Service IP.
- **Result**: `myapp-stage.burntbytes.com` resolves only inside your network.

## Scaling & BGP

This setup is fully transparent to your networking layer (L2 vs BGP).

- **Current (L2)**: ExternalDNS sees the MetalLB/Cilium L2 IP on the Gateway and publishes it.
- **Future (BGP)**: When you switch to BGP, the Gateway gets a new VIP. ExternalDNS detects the IP change and updates Cloudflare/CoreDNS automatically.

## Implementation Plan

### Phase 1: Internal DNS Authority (Staging)

1.  Deploy **CoreDNS** (Authoritative) in `infra/controllers/coredns-custom`.
    - Expose via Service (TCP/UDP 53).
    - Config: Authoritative zone `stage.burntbytes.com`, enable `file` plugin with reload or `etcd`.
2.  Deploy **ExternalDNS (Staging)** in `infra/controllers/external-dns-stage`.
    - Arguments: `--source=gateway-httproute`, `--provider=rfc2136`, `--rfc2136-host=<coredns-service>`.
    - RSA key for secure updates (TSIG).
3.  Configure physical **AdGuard Home**:
    - Upstream DNS server for `/stage.burntbytes.com/`: `<Cluster-CoreDNS-LoadBalancer-IP>`.

### Phase 2: Production Automation

1.  Deploy **ExternalDNS (Prod)** in `infra/controllers/external-dns-prod`.
    - Arguments: `--source=gateway-httproute`, `--provider=cloudflare`.
    - Secret: Cloudflare API Token.
