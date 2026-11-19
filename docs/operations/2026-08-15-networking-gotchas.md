---
title: Networking gotchas — Cilium, LoadBalancer, gateways, tunnel, log volume
status: Stable
created: 2026-08-15
updated: 2026-10-06
updated_by: gjcourt
tags: [operations, cilium, networking, cloudflare, loki, gotchas]
---

# Networking gotchas — Cilium, LoadBalancer, gateways, tunnel, log volume

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

## cloudflare tunnel apex and access

**A hand-made apex CNAME to `<tunnel-uuid>.cfargotunnel.com` fails with Cloudflare error 1016; route
the apex with `cloudflared tunnel route dns`. A leftover Cloudflare Access app can silently gate a
hostname.**

The public path is the `production` tunnel, served by `deploy/cloudflared` in ns
`cloudflare-tunnel`, with its ingress list in `apps/production/cloudflare-tunnel/config.yaml` (see
that directory's README for the bar a hostname must clear before it is published).

1. **Subdomain** (`foo.burntbytes.com`): a proxied CNAME to `<tunnel-uuid>.cfargotunnel.com` created
   in the Cloudflare dashboard works. Every existing subdomain routes this way.
2. **Apex** (bare `burntbytes.com`): a *manually created* apex CNAME to cfargotunnel **fails with
   error 1016** ("Origin DNS error"). The zone apex is CNAME-flattened and `cfargotunnel.com` has no
   public IP, so the route never registers. Register it through the tunnel API instead:
   `cloudflared tunnel route dns production burntbytes.com`. That needs the account `cert.pem`,
   which lives wherever the tunnel was first created — the cluster holds only the tunnel's
   `credentials.json`.
3. **Cloudflare Access can gate a hostname before the origin is ever reached.** `burntbytes.com`
   and `flashcards.burntbytes.com` were once behind a Zero Trust Access application. The symptom is a
   302 to `<team>.cloudflareaccess.com/cdn-cgi/access/login/<host>` whatever the origin serves, on
   that hostname only. Delete the app in Zero Trust → Access → Applications. When a hostname "won't
   serve publicly", check for an Access app before debugging the tunnel or the origin.

**Config reload is automatic now.** cloudflared does not hot-reload its ingress, which used to need a
manual `rollout restart` after every edit (a stale config shows as one routed hostname returning 530
while the rest work). Since #894 (2026-06-10) the ConfigMap comes from a `configMapGenerator` with a
content-hash suffix, so any `config.yaml` change rolls the pods through Flux. If you ever see the
530 pattern, check that the running pods reference the current hashed ConfigMap.

Hit 2026-06-10 during the burntbytes.com self-host
([plan](../plans/2026-06-10-burntbytes-self-host.md)). The in-cluster half of the same path
(gateway → pod) is [cilium gateway netpol](#cilium-gateway-netpol).

---

## loki fill cilium debug

**Two distinct Loki failure modes. A filling PVC means log volume, and the first suspect is Cilium
debug logging. A crashloop on `read-only file system` is an iSCSI remount, not a fill.**

**Fill (`KubePersistentVolumeFillingUp` on `storage-loki-0`).** On 2026-06-28 the Loki PVC filled
because Cilium had been left with `debug.enabled: true` after the June Talos recovery.
`cilium-agent` emitted ~74 GB/day of `level=debug` lines (Envoy xDS churn and load-balancer
reconciler `Update RevNat` / `Update master service`) — about 99% of all cluster log ingestion.
Found with a LogQL `topk(sum by (namespace|pod|container) (bytes_over_time(...)))` that pointed at
kube-system → cilium-agent.

First check if log storage ever fills again:

```bash
kubectl -n kube-system get cm cilium-config -o jsonpath='{.data.debug}'
```

Fix: `debug.enabled: false` in `infra/controllers/cilium/values.yaml` (#1001), **then**
`kubectl -n kube-system rollout restart ds/cilium` — agents read `debug` only at startup. That cut
ingestion by ~99%, and 7-day retention fits the original 40Gi with no expansion.

If the volume genuinely needs to be bigger, online growth of iSCSI PVCs works since democratic-csi
v1.9.5 — see [democratic-csi driver and PVC expansion](./2026-08-15-storage-gotchas.md#democratic-csi-driver-and-pvc-expansion).
Older notes saying "never patch a PVC to grow it, recreate instead" predate that fix and are wrong.

**Read-only remount (2026-07-27: crashloop, not a fill).** Log line:
`error running loki err="unlinkat /var/loki/... : read-only file system"`. The ext4 iSCSI volume was
kernel-remounted read-only after a block-device I/O error (an iSCSI blip; `debug` was already false
and the volume ~1% full). Fix: `kubectl delete pod loki-0 -n monitoring`, which forces an iSCSI
detach/reattach and a fresh read-write mount. If it goes read-only again immediately, the LUN or
TrueNAS has a real fault. The same reattach works for any ext4 iSCSI PVC that goes read-only; see
[the 2026-08-13 incident](./incidents/2026-08-13-iscsi-readonly-remount-monitoring-blind.md) for the
cluster-wide version.

---
