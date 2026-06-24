---
status: planned
last_modified: 2026-06-24
summary: "Add a Talos layer-2 control-plane VIP (10.42.2.26) so kubeconfig/talosconfig reference a floating endpoint instead of hardcoding a single node. talosconfig already fixed client-side (multi-endpoint); this plan covers the operator-led VIP for kubeconfig."
---

# Plan: Control-plane VIP for a stable, node-independent API endpoint

**Status:** Planned
**Date:** 2026-06-24
**Author:** George (with Claude)

## Problem

Both the workstation `kubeconfig` and `talosconfig` hardcoded a single control-plane
node, `10.42.2.20`. When `.20` hung on 2026-06-21, the cluster was actually up (served
by `.21`/`.23`), but every `kubectl`/`talosctl` from the laptop failed — the single
endpoint made a one-node outage look total. (See
`docs/operations/incidents/2026-06-24-cp-node-hang-cilium-k8sservicehost-spof.md`.)

`talosconfig` is **already fixed** (client-side, no cluster change):

```bash
talosctl --talosconfig ~/.talos/config config endpoint 10.42.2.20 10.42.2.21 10.42.2.23
```

`talosctl` natively load-balances/fails over across multiple endpoints — done.

`kubeconfig` is the hard one: it has a single `server:` field and TLS validates against
the apiserver cert SANs. The cluster currently has **no floating endpoint** — Talos
`cluster.controlPlane.endpoint` is literally `https://10.42.2.20:6443`, and the apiserver
cert SANs are `kubernetes…, localhost, talos-2mz-rfj, IP:10.42.2.20, 10.42.2.21,
10.96.0.1, 127.0.0.1` (note: **no shared name, and not even `.23`**). So you can't just
repoint at another node without it being the next SPOF (and `.23` would fail TLS).

## Decision

Add a **Talos layer-2 control-plane VIP** and make it the cluster endpoint.

| Item | Value |
|---|---|
| VIP IP | **`10.42.2.26`** — free; outside the Cilium LB pool (`.30–.37` + `.40–.254`) and the node range (`.20–.25`). Operator should `arp`-confirm free before applying. |
| Interface | **`eno1`** (the active NIC carrying the node IP; `bond0` is down/unused — verify per node). |
| Endpoint | `https://10.42.2.26:6443` |

Why a VIP over a DNS round-robin name: the VIP is **etcd-leader-elected by Talos** — it
only ever lives on a *healthy* control-plane node and floats off a dead one via
gratuitous ARP. That's crisper failover than DNS A-record round-robin, and Talos adds
the endpoint + VIP to the apiserver cert SANs automatically.

## Machine-config patch

Apply to **all three** control-plane nodes (`.20`, `.21`, `.23`). Save as `cp-vip.patch.yaml`:

```yaml
machine:
  network:
    interfaces:
      # Strategic-merges into the existing eno1 config by interface name —
      # it ADDS the vip; it must not drop the node's existing addressing.
      - interface: eno1
        vip:
          ip: 10.42.2.26
cluster:
  controlPlane:
    endpoint: https://10.42.2.26:6443
  apiServer:
    certSANs:
      - 10.42.2.26   # the VIP
      - 10.42.2.20
      - 10.42.2.21
      - 10.42.2.23   # also closes the current "cert missing .23" gap
```

## Apply procedure (one node at a time)

Patches are network/cert changes — **no reboot** required (Talos reconciles live and
regenerates the apiserver cert). Still go one node at a time and verify between, per the
`.20/.21` shared-switch/PSU caveat. **Order: `.23` first (most independent), then `.20`,
then `.21`.** Keep etcd 3/3 throughout.

For each node `N` in `10.42.2.23`, `10.42.2.20`, `10.42.2.21`:

```bash
# 1. confirm healthy before touching the next node
talosctl -e 10.42.2.21 -n 10.42.2.21 etcd members        # expect 3 healthy
kubectl get nodes                                         # all Ready

# 2. apply the patch (auto mode = no reboot if possible)
talosctl -e $N -n $N patch machineconfig --mode=auto --patch @cp-vip.patch.yaml

# 3. verify eno1 still has its node IP (patch merged, didn't replace)
talosctl -e $N -n $N get addresses | grep -E "$N|10.42.2.26"
```

After all three are patched:

```bash
# VIP is held by exactly ONE healthy CP node:
talosctl -n 10.42.2.20,10.42.2.21,10.42.2.23 get addresses | grep 10.42.2.26

# API reachable via the VIP, TLS validates (cert now includes .26):
kubectl --server=https://10.42.2.26:6443 get nodes

# Regenerate kubeconfig pointed at the VIP endpoint:
talosctl -e 10.42.2.26 kubeconfig --force ~/.kube/config
kubectl get nodes                                         # now via the VIP

# Optional: add the VIP to talosconfig endpoints too (already multi-endpoint):
talosctl config endpoint 10.42.2.26 10.42.2.20 10.42.2.21 10.42.2.23
```

## Validation

- Drain/cordon or briefly power-cycle the node currently holding the VIP; confirm the VIP
  re-elects to another CP node and `kubectl get nodes` (via `10.42.2.26`) keeps working
  with at most a brief blip. This is the exact failure that broke the laptop on 2026-06-21.
- `kubectl --server=https://10.42.2.26:6443 get --raw='/healthz'` returns `ok`.

## Rollback

The VIP is additive and non-destructive. To revert, patch out the `vip` block and point
the endpoint back at a healthy node:

```yaml
cluster:
  controlPlane:
    endpoint: https://10.42.2.21:6443
```
…then `talosctl kubeconfig --force` again. (Leaving the extra certSANs is harmless.)

## Notes / caveats

- **KubePrism makes this low-risk internally.** Kubelets, Cilium, and other in-cluster
  components reach the API via Talos KubePrism (`localhost:7445`, set up in PR #978), not
  the external endpoint — so changing `controlPlane.endpoint` does not disturb them. The
  endpoint change is essentially workstation-facing + cert SANs.
- **No Cilium conflict.** `10.42.2.26` is outside the Cilium LB pool, and Cilium only
  L2-announces LB-pool IPs. Talos announces the VIP itself via ARP on `eno1`.
- **VIP never lands on a sick node** — it's tied to etcd membership/leadership, which is
  the whole point (a hung `.20` would not hold it).
- Confirm `eno1` is the active NIC on `.20` and `.23` too (it is on `.21`; `bond0` is
  down). If any node differs, adjust the `interface:` in the patch for that node.
- Requires Talos ≥ 1.5 for `machine.network.interfaces[].vip` (cluster is on 1.12 ✓).
