---
title: Talos node maintenance — safe power-down and restore
status: Stable
created: 2026-06-17
updated: 2026-06-17
updated_by: gjcourt
tags: [operations, talos, etcd, maintenance, cnpg, hardware]
---

# Talos Node Maintenance — Safe Power-Down & Restore

How to take one or more Talos nodes offline for hardware work and bring them back
with zero data loss. Driven from this Mac (`kubectl` + `talosctl` have LAN access;
talosctl context `melodic-muse`).

> **Current task (2026-06-17):** `talos-v2l-hng` (10.42.2.22) reports **30.6 GiB**
> instead of ~62 GiB — a likely unseated/failed DIMM. To open that box the operator
> must power down four co-located nodes, **10.42.2.22–25**. This runbook is written
> against that case; the safety model generalizes.

## The one rule: etcd quorum

The cluster has **3 control-plane / etcd members** (`.20`, `.21`, `.22`). etcd needs
**a strict majority (2 of 3)** to keep the control plane writable.

- **You may power down at most ONE control-plane node at a time.** Taking down two
  drops etcd below quorum → the API server goes read-only → the cluster is
  effectively frozen until a member returns.
- During any single-CP-down window, etcd runs at **2/3 = zero fault tolerance**: if
  either *surviving* control-plane node hiccups, you lose quorum. Keep the window
  short and **do not touch the survivors**.

Workers carry no etcd; any number of workers can go down (subject to capacity and
stateful-workload tolerance below).

### Node roles (verify before every run — names/IPs are stable, roles are not assumed)

```
kubectl get nodes -L node-role.kubernetes.io/control-plane -o wide
```

| Node | IP | Role | Mem | This task |
|---|---|---|---|---|
| talos-ykb-uir | 10.42.2.20 | control-plane | 62G | **STAYS UP** — also the talosctl endpoint |
| talos-2mz-rfj | 10.42.2.21 | control-plane | 62G | **STAYS UP** |
| talos-v2l-hng | 10.42.2.22 | control-plane | **30.6G** | power off → hardware work |
| talos-lmh-kyf | 10.42.2.23 | worker | 62G | power off (no hw work) |
| talos-18u-ski | 10.42.2.24 | worker | 62G | power off (no hw work) |
| talos-kot-7x7 | 10.42.2.25 | worker | 62G | power off (no hw work) |

Powering off `.22–.25` keeps **`.20` + `.21` = 2/3 etcd → quorum holds.** This is the
only reason taking four nodes down at once is safe here.

## What else to know before pulling power

- **Storage is network-attached, not node-local.** All PVCs use democratic-csi iSCSI
  from hestia/TrueNAS (`truenas-iscsi*` StorageClasses, `Retain` reclaim). A node
  going down does **not** lose data; volumes re-attach elsewhere. iSCSI is **RWO**,
  so a volume attaches to one node at a time — graceful drain avoids Multi-Attach
  churn; ungraceful shutdown can leave a stale attachment (see Troubleshooting).
- **CNPG runs degraded on fewer nodes.** With all 3 workers down, every workload
  consolidates onto `.20` + `.21`. Scheduling fits (survivors sit at ~3–6% memory
  requests), but a 3-instance CNPG cluster can't place 3 anti-affine replicas on 2
  nodes — expect clusters to drop to 2/3 or fewer until nodes return. **This is
  expected and data-safe.** Several clusters are often *already* "Waiting for the
  instances to become active" (e.g. `golinks-prod`, `vitals`, `memos`, `immich-prod`)
  — baseline them in step 1 so you don't chase pre-existing degradation afterward.
- **`talosctl shutdown` halts a node gracefully. `talosctl reset` WIPES it.** Only
  ever use `shutdown` for maintenance. Never `reset` a node you intend to bring back.
- **Do not remove a temporarily-down etcd member.** It rejoins automatically on boot.
  `etcd remove-member` is for decommissioning only.

## Procedure

### 1. Baseline (capture the "before")
```
kubectl get nodes -o wide                                   # all 6 Ready
talosctl -n 10.42.2.20 etcd members                         # 3 healthy members
flux get kustomizations -A                                  # green
kubectl get clusters.postgresql.cnpg.io -A                  # SAVE THIS — compare in step 8
kubectl get volumeattachments | wc -l                       # note baseline count
```

### 2. Cordon the four (stop new scheduling onto doomed nodes)
```
kubectl cordon talos-v2l-hng talos-lmh-kyf talos-18u-ski talos-kot-7x7
```

### 3. Drain the four (evict workloads; workers first, CP last)
```
for n in talos-lmh-kyf talos-18u-ski talos-kot-7x7 talos-v2l-hng; do
  kubectl drain "$n" --ignore-daemonsets --delete-emptydir-data --timeout=300s
done
```
- A CNPG **PDB may block** a drain (CNPG protecting availability). With only 2
  survivor nodes that's unavoidable — let CNPG do its switchover; if a drain stalls
  on a CNPG PDB past the timeout, proceed. The replica that can't move goes `Pending`
  until the node returns (data safe on iSCSI). **Don't fight the PDB.**
- After each drain: `kubectl get pods -A -o wide | grep <node>` should show only
  daemonset pods.

### 4. Graceful shutdown
```
talosctl -n 10.42.2.22,10.42.2.23,10.42.2.24,10.42.2.25 shutdown
```
- `kubectl get nodes` → the four go `NotReady`, then unreachable.
- `talosctl -n 10.42.2.20 etcd members` → `.22` shows unhealthy/down; cluster still
  has quorum (2/3). **Leave the member in place.**

### 5. Physical maintenance (talos-v2l-hng / .22)
Once powered off and pulled:
- Re-seat all DIMMs; confirm full population and correct channel layout.
- Check BIOS/POST memory training and the BMC/IPMI SEL log for a flagged or
  auto-disabled DIMM.
- On a test boot, `dmidecode -t memory` — look for a slot reading missing/half-size,
  a half-populated channel, or a BIOS rank/ECC-sparing reservation. Memtest a
  suspect stick. The other three nodes (`.23–.25`) need no hardware work.

### 6. Power on + auto-rejoin
Power on all four. Talos boots from on-disk config and rejoins the cluster + etcd
automatically.
```
kubectl get nodes                                           # all 6 Ready
talosctl -n 10.42.2.20 etcd members                         # back to 3 healthy
```

### 7. Confirm the fix (the actual objective)
```
kubectl get node talos-v2l-hng -o jsonpath='{.status.capacity.memory}{"\n"}'
```
Should now read ~`65000000Ki` (≈62 GiB), not ~32G.

### 8. Uncordon + verify heal
```
kubectl uncordon talos-v2l-hng talos-lmh-kyf talos-18u-ski talos-kot-7x7
kubectl get clusters.postgresql.cnpg.io -A                  # compare to step 1 baseline
kubectl get volumeattachments
kubectl get pods -A | grep -iE "ContainerCreating|Pending|Error"
flux get kustomizations -A                                  # green
```
- Every CNPG cluster healthy in step 1 should return to 3/3 (CNPG recreates the
  missing instances onto the returned nodes). Pre-existing "Waiting" clusters may
  stay as they were — not caused by this op.
- Clear any Multi-Attach stragglers (see Troubleshooting). Spot-check a few app URLs.

## Troubleshooting

- **Pod stuck `ContainerCreating` with a Multi-Attach error after return** — a stale
  iSCSI `volumeattachment` still bound to the old node. Find the orphaned
  `Terminating` pod and force-delete it:
  `kubectl delete pod <pod> -n <ns> --grace-period=0 --force`. The volume then
  re-attaches to the new node.
- **A node won't rejoin on boot** — `talosctl -n <ip> dmesg` and `talosctl health`.
  If its etcd member is genuinely stale (won't re-add), as a **last resort**:
  `talosctl -n 10.42.2.20 etcd remove-member <id>` and let it re-add on next boot.
- **Quorum unexpectedly lost** (a survivor died mid-window) — the API goes read-only.
  Power the four back on immediately; etcd recovers once ≥2 members are up. Never
  `reset`.

## Abort criteria

Stop and reassess before powering down if, at step 1: etcd is not 3/3 healthy, a
survivor control-plane node is unhealthy, or Flux/CNPG is mid-incident. Powering a
node down on top of a pre-existing degradation can tip etcd or a database past
recovery.
