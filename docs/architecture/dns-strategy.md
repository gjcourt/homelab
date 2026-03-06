# DNS Strategy: Split-Horizon & Simplification

To scale your homelab and automate DNS records without exposing staging IPs, we implement a **Split-Horizon DNS** architecture.

**Update (Feb 2026)**: We have simplified the architecture to rely on **Wildcard DNS** instead of complex in-cluster DNS servers (ExternalDNS + CoreDNS).

## Current Architecture: The "Simple Wildcard"

We use a single "dumb" wildcard record in the physical DNS layer (AdGuard Home) to route all traffic to the Kubernetes Gateway.

### The Flow
1.  **Client** asks for `bio.stage.burntbytes.com`.
2.  **AdGuard Home** has a static rewrite rule: `*.stage.burntbytes.com` -> `10.42.2.31` (Staging Gateway IP).
3.  **AdGuard Home** answers `10.42.2.31`.
4.  **Client** connects to `10.42.2.31`.
5.  **Cilium Gateway** inspects the HTTP Host header (`bio.stage.burntbytes.com`) and routes to the correct Pod.

### Why this is better
- **Zero In-Cluster Maintenance**: No need to maintain highly-available CoreDNS/etcd inside the cluster just to resolve IPs that never change.
- **Fail-Safe**: Even if the cluster control plane is down, DNS resolution works (debugging is easier).
- **Less Noise**: Removed `external-dns` crash loops and `cert-manager` complexity associated with individual records.

---

## When to use "Complex" DNS (ExternalDNS + CoreDNS)

We previously attempted a complex setup (ExternalDNS writing to an in-cluster CoreDNS). This added significant "complexity tax". You should **only** re-implement this if you hit one of these specific limits:

### 1. Multiple Entry Points (Apps with dedicated IPs)
Currently, all apps sit behind the Gateway (`10.42.2.31`).
*   **Scenario**: You deploy a game server (Minecraft) or Database that needs its *own* LoadBalancer IP (e.g., `10.42.2.50`) because it can't use the HTTP Gateway.
*   **Failure**: The wildcard sends `minecraft.stage...` to the Gateway (`.30`), but the app is at `.50`.
*   **Fix**: ExternalDNS detects the Service of type `LoadBalancer` and updates DNS for that specific host.

### 2. Headless Services (Direct Pod Access)
Distributed systems (Kafka, Cassandra, MongoDB Replicasets) often require clients to talk to *specific* pod IPs directly.
*   **Scenario**: `mongo-0.stage.burntbytes.com` needs to resolve to `10.42.0.5`, not the Gateway.
*   **Fix**: ExternalDNS supports Headless settings to create A-records for every individual pod.

### 3. Non-Gateway TCP/UDP Traffic
*   **Scenario**: A legacy protocol that doesn't support Host headers (unlike HTTP) and cannot use the Gateway's listeners.
*   **Fix**: Similar to #1, these services get random LoadBalancer IPs that need dynamic DNS registration.

---

## Configuration (AdGuard Home)

Since we are LAN-only for Staging (and most of Production), configure **DNS Rewrites** in AdGuard Home.

### 1. Find your Gateway IPs

Run the following command to get the LoadBalancer IPs for your Staging and Production gateways:

```bash
kubectl get svc -n default -l gateway.networking.k8s.io/gateway-name
```

*Example Output:*
```text
NAME                                  EXTERNAL-IP
cilium-gateway-app-gateway-production 10.42.2.30
cilium-gateway-app-gateway-staging    10.42.2.31
```

### 2. Configure Rewrites

Go to **Filters → DNS Rewrites** and add:

| Domain | Rewrite To (IP) | Description |
|:---|:---|:---|
| `*.stage.burntbytes.com` | `10.42.2.31` | Staging Gateway Wildcard |
| `*.burntbytes.com` | `10.42.2.30` | Production Gateway Wildcard |

> **Note:** These wildcards override public Cloudflare DNS records on your LAN, ensuring traffic stays local for speed and security.

