# Cilium

## 1. Overview

Cilium is the Container Network Interface (CNI) used in the homelab cluster. It provides high-performance networking, security, and observability via eBPF. It also serves as the LoadBalancer IPAM provider (replacing MetalLB) and the Gateway API controller.

## 2. Architecture

Cilium is deployed via Flux using the official Helm chart. It runs as a DaemonSet (`cilium`) on all nodes and a Deployment (`cilium-operator`) for cluster-wide operations.

- **Datapath**: eBPF with `kubeProxyReplacement: true` (kube-proxy is completely removed).
- **IPAM**: Kubernetes IPAM mode.
- **LoadBalancer IP advertisement**: BGP peering with the UCGF (UniFi Cloud Gateway Fiber). Each worker establishes an eBGP session from AS 65010 to UCGF AS 65100 and advertises every LoadBalancer IP as a /32. The UCGF installs three ECMP next-hops per /32 (one per worker), so any single-worker BGP failure leaves traffic served by the other two. **L2 announcements are no longer used** — see [`docs/plans/2026-03-08-bgp-rollout.md`](../plans/2026-03-08-bgp-rollout.md) for the migration history. The canonical UCGF FRR config is snapshotted at [`docs/reference/ucgf-bgp-frr.conf`](ucgf-bgp-frr.conf).
- **Gateway API**: Cilium acts as the Gateway API controller, provisioning Envoy proxies for routing ingress traffic.
- **Observability**: Hubble, Hubble Relay, and Hubble UI are enabled for network visibility.

## 3. URLs

- **Hubble UI**: internal port-forward only (`cilium hubble port-forward`), or via Gateway API ingress if configured.

## 4. Configuration

### Helm values

Located in `infra/controllers/cilium/values.yaml`:

- `kubeProxyReplacement: true`
- `bgpControlPlane.enabled: true` — enables the agent's BGP module
- `l2announcements.enabled: false` — disabled after migration (Phase 4b)
- `gatewayAPI.enabled: true`
- `hubble.enabled: true`

### LoadBalancer IP pools

Defined in `infra/configs/cilium/load-balancer-ip-pool.yaml`. Two pools:

| Pool | Range | Use |
|---|---|---|
| `home-c-pool` | `10.42.2.40` – `10.42.2.254` | Default — gateways, adguard, etc. |
| `home-compute-pool` | `10.42.2.30` – `10.42.2.37` | Compute-tier services (snapcast etc.) |

The BGP advertisement permits any /32 in `10.42.2.0/24` (covers both pools without needing pool-specific rules).

### BGP resources

In `infra/configs/cilium/`:

| File | Resource | Notes |
|---|---|---|
| `bgp-cluster-config.yaml` | `CiliumBGPClusterConfig` | nodeSelector: every non-control-plane node peers (auto picks up new workers) |
| `bgp-peer-config.yaml` | `CiliumBGPPeerConfig` | timers, IPv4 unicast family, attaches the LB-IP advertisement via `families[].advertisements.matchLabels{advertise: lb-ips}` |
| `bgp-advertisement.yaml` | `CiliumBGPAdvertisement` | matchLabels {} → advertise every LoadBalancer IP cluster-wide |

### UCGF (router) side

The UCGF runs FRRouting (FRR) 10.x. BGP is configured via `vtysh`; the UniFi controller UI does not expose BGP. Authoritative config snapshot in [`docs/reference/ucgf-bgp-frr.conf`](ucgf-bgp-frr.conf). Re-apply this snapshot after every UniFi firmware upgrade — firmware updates may revert both `/etc/frr/frr.conf` AND `/etc/frr/daemons` (re-set `bgpd=yes` in daemons and `systemctl enable --now frr` if the service comes back disabled).

## 5. Usage

Cilium operates transparently. When a `Service` of type `LoadBalancer` is created:
1. Cilium IPAM allocates an IP from the appropriate pool.
2. The agent on each worker advertises the IP as a /32 to the UCGF via BGP.
3. The UCGF installs three ECMP next-hops in its FIB.
4. Traffic from anywhere on the LAN routes through the UCGF to one of the three workers; Cilium then routes to the backing pod.

### CLI

```bash
# Cluster status
cilium status

# Connectivity test
cilium connectivity test

# BGP peers (per node — query each agent)
kubectl exec -n kube-system "$(kubectl get pod -n kube-system -l k8s-app=cilium \
  --field-selector spec.nodeName=<NODE> -o jsonpath='{.items[0].metadata.name}')" \
  -- cilium-dbg bgp peers

# BGP routes advertised by a node
kubectl exec -n kube-system "$(kubectl get pod -n kube-system -l k8s-app=cilium \
  --field-selector spec.nodeName=<NODE> -o jsonpath='{.items[0].metadata.name}')" \
  -- cilium-dbg bgp routes available ipv4 unicast
```

### Router side (read-only)

```bash
ssh root@10.42.2.1 'vtysh -c "show bgp summary"'        # peer state
ssh root@10.42.2.1 'vtysh -c "show ip route bgp"'       # ECMP path count per /32
ssh root@10.42.2.1 'vtysh -c "show running-config"'     # full FRR config
```

## 6. Testing

```bash
# Pods up
kubectl -n kube-system get pods -l k8s-app=cilium

# Recent agent logs
kubectl -n kube-system logs ds/cilium --tail=100

# End-to-end connectivity probe
cilium connectivity test
```

LB-IP-specific smoke (from any LAN client):

```bash
curl -sk -o /dev/null -w "%{http_code}\n" https://home.burntbytes.com   # gateway-prod (.40)
dig @10.42.2.43 example.com +short                                       # adguard-prod
```

## 7. Monitoring & Alerting

- **Hubble** for flow observability:
  ```bash
  cilium hubble port-forward
  hubble observe
  ```
- **BGP alerts** (in `infra/configs/alerts/prometheus-rules.yaml` group `cilium-bgp`): `CiliumBGPSessionDown` / `CiliumBGPNoPeers` / `CiliumBGPMultipleSessionsDown`.
  > **Currently silent.** The agent metrics endpoint requires `prometheus.enabled: true` in the Helm values, which is not yet set. Tracked as a separate follow-up — flipping it triggers another Cilium DaemonSet rollout.

## 8. Disaster Recovery

- **Configuration**: All cluster-side state is in Git (Helm values, BGP CRs, LB IP pools). No persistent state to back up on the cluster side.
- **UCGF FRR config**: Snapshotted at `docs/reference/ucgf-bgp-frr.conf`. Re-apply after a firmware upgrade if FRR comes back disabled or with a wiped config.
- **Restore procedure**: Re-apply the Flux Kustomization. If nodes are recreated, Cilium rebuilds eBPF maps automatically; BGP sessions re-establish within ~30s of agent startup.

## 9. Troubleshooting

- **Pods stuck in ContainerCreating** — usually a CNI issue. Check the Cilium agent log on the affected node.
- **LoadBalancer IP not reachable from LAN**:
  1. Confirm the IP is assigned: `kubectl get svc <name>`.
  2. Confirm the workers are advertising it: `cilium-dbg bgp routes available ipv4 unicast` on each worker — should list the /32.
  3. Confirm the UCGF received it: `ssh root@10.42.2.1 'vtysh -c "show ip route bgp"'` — expect 3 next-hops.
  4. If steps 1-2 are good but 3 is missing, the worker→UCGF BGP session is down. Check `cilium-dbg bgp peers`.
- **Gateway API routing**: check `Gateway` / `HTTPRoute` status; Envoy logs are inside the Cilium agent pods.
- **Helm-value-only config change didn't take effect** — Cilium config-only Helm changes update the `cilium-config` ConfigMap but **don't change the DaemonSet pod template hash**, so the rollout is a no-op and pods keep the old config. Manually `kubectl rollout restart ds/cilium -n kube-system` and (if the change affects operator-managed CRs) `kubectl rollout restart deploy/cilium-operator -n kube-system`.
