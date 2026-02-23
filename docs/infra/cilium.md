# Cilium

## 1. Overview
Cilium is the Container Network Interface (CNI) used in the homelab cluster. It provides high-performance networking, security, and observability using eBPF. It also serves as the LoadBalancer IPAM provider (replacing MetalLB) and the Gateway API controller.

## 2. Architecture
Cilium is deployed via Flux using the official Helm chart. It runs as a DaemonSet (`cilium`) on all nodes and a Deployment (`cilium-operator`) for cluster-wide operations.
- **Datapath**: Uses eBPF with `kubeProxyReplacement: true` (kube-proxy is completely removed).
- **IPAM**: Uses Kubernetes IPAM mode.
- **LoadBalancer**: Cilium L2 Announcements are enabled to announce LoadBalancer IPs to the local network.
- **Gateway API**: Cilium acts as the Gateway API controller, provisioning Envoy proxies for routing ingress traffic.
- **Observability**: Hubble, Hubble Relay, and Hubble UI are enabled for network visibility.

## 3. URLs
- **Hubble UI**: (Internal port-forward only, or via specific ingress if configured)

## 4. Configuration
- **Helm Values**: Located in `infra/controllers/cilium/values.yaml`.
  - `kubeProxyReplacement: true`
  - `l2announcements.enabled: true`
  - `gatewayAPI.enabled: true`
  - `hubble.enabled: true`
- **IP Pool**: Defined in `infra/configs/cilium/load-balancer-ip-pool.yaml`.
  - Pool Name: `home-compute-pool`
  - Range: `192.168.5.30` - `192.168.5.255`
- **L2 Announcement Policy**: Defined in `infra/configs/cilium/l2-announcement-policy.yaml`.

## 5. Usage Instructions
Cilium operates transparently. When a `Service` of type `LoadBalancer` is created, Cilium automatically assigns an IP from the `home-compute-pool` and announces it via ARP (L2).
When a `Gateway` or `HTTPRoute` is created, Cilium configures Envoy to route the traffic.

To interact with Cilium CLI:
```bash
# Install cilium CLI locally if not present
cilium status
cilium connectivity test
```

## 6. Testing
To verify Cilium is working correctly:
```bash
kubectl -n kube-system get pods -l k8s-app=cilium
kubectl -n kube-system logs ds/cilium
```
Run the Cilium connectivity test suite:
```bash
cilium connectivity test
```

## 7. Monitoring & Alerting
- **Hubble**: Use Hubble CLI or UI to observe network flows.
  ```bash
  cilium hubble port-forward
  hubble observe
  ```
- **Metrics**: Cilium exposes Prometheus metrics (if configured in values).

## 8. Disaster Recovery
- **Backup Strategy**: Cilium configuration is stored in Git. No persistent state needs to be backed up.
- **Restore Procedure**: Re-apply the Flux Kustomization. If nodes are recreated, Cilium will automatically rebuild the eBPF maps.

## 9. Troubleshooting
- **Pods stuck in ContainerCreating**: Often a CNI issue. Check Cilium agent logs on the specific node.
- **LoadBalancer IP not reachable**:
  - Verify the IP is assigned: `kubectl get svc <name>`
  - Check L2 announcements: `kubectl get ciliuml2announcementpolicies`
  - Ensure no IP conflicts on the local network.
- **Gateway API routing issues**: Check the status of the `Gateway` and `HTTPRoute` resources. Look at the Envoy logs in the Cilium agent pods.
