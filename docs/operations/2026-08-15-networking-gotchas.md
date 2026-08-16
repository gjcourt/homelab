---
title: Networking gotchas — Cilium, LoadBalancer, gateways
status: Stable
created: 2026-08-15
updated: 2026-08-15
updated_by: gjcourt
tags: [operations, cilium, networking, gotchas]
---

# Networking gotchas — Cilium, LoadBalancer, gateways

Hard-won Cilium behaviour that is not obvious from the docs and has cost real debugging time. Promoted from working notes 2026-08-15.

---

## cilium gateway netpol

**How to write CiliumNetworkPolicy ingress rules for apps behind the Gateway API on a multi-node VXLAN cluster**

For apps exposed via Cilium Gateway API on a multi-node cluster with VXLAN tunneling, the ingress rule must allow **all three** entities: `host`, `remote-node`, AND `ingress`:

```yaml
ingress:
  - fromEntities:
      - host        # Envoy and pod on the same node
      - remote-node # Envoy and pod on different nodes (VXLAN cross-node)
      - ingress     # Cilium proxy source IP (reserved identity 8)
    toPorts:
      - ports:
          - port: "8080"
            protocol: TCP
```

**Why:** Cilium's Gateway API Envoy binds upstream connections to a dedicated proxy IP (e.g. `10.244.x.15`) that Cilium classifies as reserved identity 8 (`ingress`), not `host` or `remote-node`. Without `ingress`, all Envoy→pod connections fail even though direct `/dev/tcp` from the same pod works. Confirmed by checking `cilium bpf ipcache list` on the destination node: the Envoy source IP shows `identity=8`. Also: `remote-node` is needed when Envoy and the pod are on different nodes (VXLAN cross-node). All three are required.

`fromEndpoints: {namespace: default}` and `fromEndpoints: {namespace: kube-system}` are both wrong — hostNetwork pods don't carry namespace-based pod identities.

**How to diagnose:** Check Envoy admin socket: if `upstream_cx_connect_fail` equals `upstream_cx_total` and `upstream_rq_total=0`, and `/dev/tcp` from within the Envoy pod succeeds, the missing entity is `ingress`.

**How to apply:** Any time writing or reviewing a CiliumNetworkPolicy ingress rule for a gateway-facing app, use `[host, remote-node, ingress]`. Applies to adguard and excalidraw too (same bug).

---

## cilium lb snat pattern

**fromCIDR doesn't work for LAN clients hitting Kubernetes LoadBalancer services due to SNAT; use fromEntities:world instead**

`fromCIDR` rules do NOT work for external LAN clients connecting through a Kubernetes `LoadBalancer` service with `externalTrafficPolicy: Cluster` (the default).

Cilium SNATs the source IP to a node IP (e.g. `10.244.0.168`) before the packet reaches the pod. The pod sees the node IP (classified as `world` identity), not the original LAN IP. `cilium monitor --type drop` shows:
```
drop (Policy denied) identity world->12962: 10.244.0.168:33114 -> 10.244.1.72:1704
```

**Why `externalTrafficPolicy: Local` doesn't help**: Cilium's L2 announcement lease does not transfer to the node running the pod when the policy changes. The old lease holder (wrong node) keeps renewing it, causing `Connection refused` from correct-node-only traffic routing.

**Fix**: Use `fromEntities: world` for ports that need external/LAN access. The security boundary is the L2 VLAN — the VIP is only reachable on VLAN 2, so permitting `world` at the CNP level is appropriate.

**How to apply**: For any LAN-accessible LoadBalancer service (snapcast, future audio/IoT services), use `fromEntities: world` in the CNP ingress rules instead of `fromCIDR`. Confirmed fixed in PR #494 for snapcast ports 1704/1705/1780.

---
