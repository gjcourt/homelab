---
status: planned
last_modified: 2026-03-10
---

# BGP Rollout Plan — Unifi Cloud Gateway Fiber + Cilium

Replace Cilium L2 announcements (ARP-based) with BGP peering between the Kubernetes node and the Unifi Cloud Gateway Fiber (UCGF). This gives the router real routing table entries for LoadBalancer IPs instead of relying on gratuitous ARP, improving reliability, observability, and multi-node readiness.

## Current State

| Component | Configuration |
|:----------|:-------------|
| Cilium version | 1.19.1 |
| IP advertisement | L2 announcements (`CiliumL2AnnouncementPolicy`) |
| LoadBalancer IP pool | `10.42.2.40` – `10.42.2.254` (`home-c-pool`) |
| K8s node | `talos-ykb-uir` at `10.42.2.20` (single node) |
| Router | Unifi Cloud Gateway Fiber at `10.42.2.1` |
| Cilium BGP CRDs | Installed (part of CRD bundle) but unused |
| Cilium `bgpControlPlane` | `false` (default, not overridden) |
| Gateway API | Enabled, Cilium is the controller |

### Files Involved

| File | Purpose |
|:-----|:--------|
| [infra/controllers/cilium/values.yaml](../../infra/controllers/cilium/values.yaml) | Helm values — `l2announcements.enabled: true` |
| [infra/configs/cilium/l2-announcement-policy.yaml](../../infra/configs/cilium/l2-announcement-policy.yaml) | `CiliumL2AnnouncementPolicy` resource |
| [infra/configs/cilium/load-balancer-ip-pool.yaml](../../infra/configs/cilium/load-balancer-ip-pool.yaml) | `CiliumLoadBalancerIPPool` (kept as-is) |

---

## Target State

```
                    BGP Peering (eBGP)
                    AS 65100 ◄──────► AS 65010

┌──────────────────┐                   ┌──────────────────────┐
│  Unifi Cloud      │                   │  Kubernetes Node     │
│  Gateway Fiber    │                   │  (talos-ykb-uir)     │
│                  │  10.42.2.1:179     │  10.42.2.20:179      │
│  AS 65100        │◄─────────────────►│  AS 65010            │
│                  │                   │                      │
│  Learns routes:  │                   │  Advertises:         │
│  10.42.2.40/32   │                   │  LoadBalancer IPs    │
│  10.42.2.41/32   │                   │  as /32 routes       │
│  10.42.2.42/32   │                   │  next-hop: self      │
│     ...          │                   │                      │
└──────────────────┘                   └──────────────────────┘
```

- **Router (UCGF):** AS 65100 — receives routes, installs them in its routing table
- **K8s node:** AS 65010 — advertises LoadBalancer IPs via Cilium BGP Control Plane
- **IP pool:** Unchanged (`10.42.2.40` – `10.42.2.254`)

---

## Prerequisites

- [ ] SSH access to Unifi Cloud Gateway Fiber (root via `ssh root@10.42.2.1`)
- [ ] Verify FRRouting (FRR) is available on UCGF (`vtysh --version` or `which frr`)
- [ ] Confirm the UCGF firmware supports BGP (UniFi OS 4.x+ with FRR)
- [ ] Plan a maintenance window — LoadBalancer IPs will be briefly unreachable during cutover

---

## Phase 1: Enable BGP on the Unifi Cloud Gateway Fiber

The UCGF runs FRRouting (FRR) under the hood. BGP must be configured via SSH since the UniFi controller UI does not expose BGP settings.

### 1.1 SSH into the gateway

```bash
ssh root@10.42.2.1
```

### 1.2 Check FRR availability

```bash
vtysh -c "show version"
```

If `vtysh` is not found, check for `/usr/lib/frr/` or `/etc/frr/`. On newer UniFi OS firmware (4.x+), FRR should be present.

### 1.3 Configure BGP via vtysh

```bash
vtysh
```

```text
configure terminal

! Create the BGP instance with the gateway's AS number
router bgp 65100
  bgp router-id 10.42.2.1
  no bgp ebgp-requires-policy

  ! Peer with the Kubernetes node
  neighbor 10.42.2.20 remote-as 65010
  neighbor 10.42.2.20 description talos-ykb-uir

  ! Accept all routes from the k8s node (LoadBalancer IPs)
  address-family ipv4 unicast
    neighbor 10.42.2.20 activate
    neighbor 10.42.2.20 route-map ALLOW-ALL in
    neighbor 10.42.2.20 route-map DENY-ALL out
  exit-address-family

! Route maps
route-map ALLOW-ALL permit 10

route-map DENY-ALL deny 10

end
write memory
```

**Key decisions:**
- **AS 65100** for the gateway (private AS range 64512–65534)
- **AS 65010** for the k8s cluster
- **`no bgp ebgp-requires-policy`** — required on FRR 8+ or routes are silently dropped
- **`DENY-ALL` outbound** — the gateway should not advertise its own routes to k8s
- **`ALLOW-ALL` inbound** — accept all LoadBalancer IP advertisements from k8s

### 1.4 Verify the peer is configured (before k8s side is ready)

```bash
vtysh -c "show bgp summary"
```

The peer `10.42.2.20` should appear as `Active` (not `Established` yet — the k8s side isn't configured).

### 1.5 Persistence across firmware upgrades

> **Warning:** UniFi gateway configuration via SSH does **not survive firmware upgrades** by default. FRR config written via `write memory` persists across reboots but may be lost during major firmware updates.

Options for persistence:
1. **`/etc/frr/frr.conf`** — Check if this file is preserved. Back it up.
2. **UniFi boot script** — Place a script in `/etc/udm-boot.d/` (if supported) or use a cron `@reboot` job to re-apply the config.
3. **Document the config** — Keep the vtysh commands in this plan so they can be re-applied after upgrades.

Create a backup:
```bash
vtysh -c "show running-config" > /root/frr-bgp-backup.conf
```

---

## Phase 2: Enable Cilium BGP Control Plane

### 2.1 Update Cilium Helm values

Edit [infra/controllers/cilium/values.yaml](../../infra/controllers/cilium/values.yaml):

```yaml
# Add this section:
bgpControlPlane:
  enabled: true
```

> **Do NOT remove `l2announcements.enabled: true` yet.** Both can coexist during the transition. L2 will continue working while BGP is being validated.

### 2.2 Create BGP peer configuration

Create `infra/configs/cilium/bgp-peer-config.yaml`:

```yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumBGPPeerConfig
metadata:
  name: ucgf-peer
spec:
  # Timers (seconds)
  timers:
    holdTimeSeconds: 90
    keepAliveTimeSeconds: 30
    connectRetryTimeSeconds: 120
  transport:
    # Use default BGP port 179
    peerPort: 179
  families:
    - afi: ipv4
      safi: unicast
  # Graceful restart helps during Cilium agent restarts
  gracefulRestart:
    enabled: true
    restartTimeSeconds: 120
```

### 2.3 Create BGP advertisement

Create `infra/configs/cilium/bgp-advertisement.yaml`:

```yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumBGPAdvertisement
metadata:
  name: lb-ip-advertisement
spec:
  advertisements:
    - advertisementType: Service
      service:
        # Advertise allocated LoadBalancer IPs
        addresses:
          - LoadBalancerIP
      selector:
        # Match all services (no filter)
        matchExpressions:
          - key: somekey
            operator: NotIn
            values: ["never-match"]
      attributes:
        # No communities needed for a simple single-peer setup
        communities: []
```

### 2.4 Create BGP cluster configuration

Create `infra/configs/cilium/bgp-cluster-config.yaml`:

```yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumBGPClusterConfig
metadata:
  name: homelab-bgp
spec:
  nodeSelector:
    matchLabels:
      # Match all nodes (Talos labels)
      kubernetes.io/os: linux
  bgpInstances:
    - name: homelab
      localASN: 65010
      peers:
        - name: ucgf
          peerASN: 65100
          peerAddress: 10.42.2.1
          peerConfigRef:
            name: ucgf-peer
            group: cilium.io
            kind: CiliumBGPPeerConfig
```

### 2.5 Update Kustomization

Edit [infra/configs/cilium/kustomization.yaml](../../infra/configs/cilium/kustomization.yaml):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - bgp-advertisement.yaml
  - bgp-cluster-config.yaml
  - bgp-peer-config.yaml
  - l2-announcement-policy.yaml   # Keep during transition
  - load-balancer-ip-pool.yaml
```

> Resources are alphabetically sorted per repo conventions.

---

## Phase 3: Validate BGP Peering

### 3.1 Commit and push the Cilium changes

```bash
git add infra/configs/cilium/ infra/controllers/cilium/values.yaml
git commit -m "Enable Cilium BGP control plane alongside L2 announcements"
git push
```

Wait for Flux to reconcile (~2 minutes).

### 3.2 Verify Cilium BGP agent status

```bash
# Check BGP peering state from Cilium
kubectl exec -n kube-system ds/cilium -- cilium-dbg bgp peers
```

Expected output should show the UCGF peer (`10.42.2.1`) in `Established` state.

### 3.3 Verify routes on the gateway

```bash
ssh root@10.42.2.1
vtysh -c "show bgp ipv4 unicast"
```

You should see `/32` routes for each allocated LoadBalancer IP with next-hop `10.42.2.20`.

### 3.4 Verify routes on the gateway routing table

```bash
vtysh -c "show ip route bgp"
```

Each LoadBalancer IP should appear as a BGP route pointing to `10.42.2.20`.

### 3.5 Test connectivity

From a LAN client (not on the k8s node), verify that LoadBalancer services are still reachable:

```bash
# Test production gateway
curl -sk https://home.burntbytes.com

# Test a few specific services
curl -sk https://links.burntbytes.com
curl -sk https://vitals.burntbytes.com
```

At this point, **both L2 and BGP are advertising the same IPs**. Traffic may use either path depending on ARP cache state. This is fine — the goal is to validate BGP before removing L2.

---

## Phase 4: Remove L2 Announcements

Only proceed once BGP peering is `Established` and routes are confirmed on the gateway.

### 4.1 Remove the L2 announcement policy

Delete `infra/configs/cilium/l2-announcement-policy.yaml`.

### 4.2 Disable L2 in Cilium Helm values

Edit [infra/controllers/cilium/values.yaml](../../infra/controllers/cilium/values.yaml):

```yaml
# Change:
l2announcements:
  enabled: false    # was: true
```

Or remove the `l2announcements` section entirely.

### 4.3 Update Kustomization

Edit `infra/configs/cilium/kustomization.yaml` — remove `l2-announcement-policy.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - bgp-advertisement.yaml
  - bgp-cluster-config.yaml
  - bgp-peer-config.yaml
  - load-balancer-ip-pool.yaml
```

### 4.4 Commit, push, and reconcile

```bash
git add -A infra/configs/cilium/ infra/controllers/cilium/values.yaml
git commit -m "Remove L2 announcements, BGP is now the sole advertisement method"
git push
```

### 4.5 Verify L2 is gone

```bash
kubectl get ciliuml2announcementpolicy
# Should return: No resources found

# Verify BGP is still working
kubectl exec -n kube-system ds/cilium -- cilium-dbg bgp peers
```

### 4.6 Full connectivity test

```bash
# Flush ARP cache on a LAN client to ensure traffic uses BGP routes
sudo arp -d 10.42.2.40 2>/dev/null

curl -sk https://home.burntbytes.com
curl -sk https://links.burntbytes.com
curl -sk https://vitals.burntbytes.com
curl -sk https://grafana.burntbytes.com
```

---

## Phase 5: Post-Cutover Hardening

### 5.1 Add BGP route filtering (optional)

On the gateway, restrict accepted prefixes to only the LoadBalancer pool range:

```text
vtysh
configure terminal

ip prefix-list K8S-LB-IPS seq 10 permit 10.42.2.40/32 ge 32 le 32
ip prefix-list K8S-LB-IPS seq 20 permit 10.42.2.41/32 ge 32 le 32
! ... or use a range:
ip prefix-list K8S-LB-IPS seq 10 permit 10.42.2.0/24 ge 32 le 32

route-map K8S-ONLY permit 10
  match ip address prefix-list K8S-LB-IPS

router bgp 65100
  address-family ipv4 unicast
    neighbor 10.42.2.20 route-map K8S-ONLY in
  exit-address-family

end
write memory
```

### 5.2 Monitoring

Add alerting for BGP session drops:

```bash
# Quick check — peer should be Established
kubectl exec -n kube-system ds/cilium -- cilium-dbg bgp peers | grep -i established
```

Consider adding a Prometheus alert via the Cilium agent metrics:
- `cilium_bgp_peers` (gauge, number of peers)
- `cilium_bgp_session_state` (per-peer state)

### 5.3 Document the change

Update [docs/architecture/](../../docs/architecture/) with a note that the cluster uses BGP for LoadBalancer IP advertisement instead of L2 ARP.

---

## Rollback Plan

If BGP is not working correctly after Phase 4 (L2 removed):

1. **Re-enable L2 immediately:**
   ```bash
   # Quick fix — re-apply the L2 policy directly
   kubectl apply -f - <<EOF
   apiVersion: cilium.io/v2alpha1
   kind: CiliumL2AnnouncementPolicy
   metadata:
     name: l2-announcement-policy-staging
     namespace: kube-system
   spec:
     externalIPs: true
     loadBalancerIPs: true
   EOF
   ```

2. **Re-enable in Helm values:**
   ```yaml
   l2announcements:
     enabled: true
   ```

3. **Revert the git changes and push.**

L2 and BGP can coexist, so re-enabling L2 does not require disabling BGP. Services will be reachable via whichever path works first.

---

## ASN Reference

| Entity | ASN | Notes |
|:-------|:----|:------|
| Unifi Cloud Gateway Fiber | 65100 | Private ASN, chosen arbitrarily |
| Kubernetes cluster | 65010 | Private ASN, chosen arbitrarily |

Private AS range: 64512–65534 (2-byte) or 4200000000–4294967294 (4-byte).

---

## Multi-Node Considerations (Future)

If more nodes are added to the cluster:

- Each node will automatically establish a BGP session with the UCGF (matched by the `nodeSelector` in `CiliumBGPClusterConfig`)
- The gateway will learn multiple next-hops for the same LoadBalancer IP and can ECMP load-balance across nodes
- This is a significant advantage over L2 announcements, which require leader election and only one node can respond to ARP for a given IP
- No changes needed to the gateway BGP config — it already accepts any peer from AS 65010
