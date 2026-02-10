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

## Manual AdGuard Configuration (Split Horizon)

Since most production applications have been removed from the Cloudflare Tunnel to be LAN-only, you must configure **DNS Rewrites** in AdGuard Home to route local traffic to your Kubernetes cluster.

### 1. Find your Gateway IPs

Run the following command to get the LoadBalancer IPs for your Staging and Production gateways:

```bash
kubectl get svc -n default -l gateway.networking.k8s.io/gateway-name
```

*Example Output:*
```text
NAME                                  TYPE           CLUSTER-IP      EXTERNAL-IP     PORT(S)
cilium-gateway-app-gateway-production LoadBalancer   10.43.149.116   192.168.5.31    80:31977/TCP,443:30950/TCP
cilium-gateway-app-gateway-staging    LoadBalancer   10.43.238.25    192.168.5.30    80:30852/TCP,443:31742/TCP
```
*(Your IPs may differ. Use the values under `EXTERNAL-IP`)*

### 2. Configure Rewrites in AdGuard Home

Go to **Filters → DNS Rewrites** and add the following entries:

| Domain | Rewrite To (IP) | Description |
|:---|:---|:---|
| `*.stage.burntbytes.com` | `192.168.5.30` | Directs all staging subdomains to the Staging Gateway |
| `*.burntbytes.com` | `192.168.5.31` | Directs all production subdomains to the Production Gateway |

> **Note:** These wildcards will override public Cloudflare DNS records for devices on your home network. Only `auth.burntbytes.com` and `links.burntbytes.com` will arguably work both ways (Tunnel vs LAN), but for best performance/privacy, the LAN path is preferred locally.

