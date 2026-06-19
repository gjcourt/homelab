# Plan: Promote talos-kot-7x7 (.25) to control-plane, restore 3-member etcd

**Status:** Draft — not yet executed
**Date:** 2026-06-19
**Author:** George (with Claude)

## Why

On 2026-06-18 the cluster lost control-plane node **`.22` (talos-v2l-hng)** during a
DIMM investigation (it reads 30.6 GiB instead of ~62 GiB). `.22` did not come back
up and is likely down for an extended period (hardware — possibly RMA). `.23` and
`.24` (workers, same shelves) are also down.

`.22` is **1 of 3 etcd members**. With it gone, etcd runs at **2/3 — quorum holds
but zero fault tolerance**. This was not theoretical: during the incident, when the
surviving member `.21` briefly dropped on a shared switch/power path, the **entire
control plane went read-only** (apiserver `etcd failed`, `etcdserver: no leader`).
Recovery only came when `.21` returned.

Running indefinitely at 2/3 means **any blip on `.20` or `.21` takes the API down**.
This plan restores a third healthy etcd member by promoting the surviving worker
**`.25` (talos-kot-7x7)** to control-plane, returning the cluster to a
fault-tolerant 3-member etcd (`.20`, `.21`, `.25`).

## Current state (baseline)

| Node | IP | Role | Status |
|---|---|---|---|
| talos-ykb-uir | .20 | control-plane / etcd | **Ready** — DO NOT TOUCH |
| talos-2mz-rfj | .21 | control-plane / etcd | **Ready** (etcd leader) — DO NOT TOUCH |
| talos-v2l-hng | .22 | control-plane / etcd | **down** (DIMM; member `5f91a708c22777db`) |
| talos-lmh-kyf | .23 | worker | down |
| talos-18u-ski | .24 | worker | down |
| talos-kot-7x7 | .25 | worker | **Ready** — promotion target, install disk `/dev/nvme0n1` |

- etcd: members `.20`, `.21`, `.22`; `.22` down → **2/3, quorum holds, zero tolerance**.
- Config source: `~/src/melodic-muse/` (`controlplane.yaml`, `worker.yaml`, `patch.yaml`,
  `talosconfig`). Raw `talosctl gen config` material (not talhelper). `patch.yaml` is the
  common cluster patch (`cni: none`, `proxy.disabled: true`). Install disk is `/dev/nvme0n1`
  on `.25`; hostname is auto (`auto: stable`); networking via DHCP.

## Key facts / constraints

1. **Talos cannot change `machine.type` in place.** Worker→control-plane requires a
   **`talosctl reset` + reinstall** with the control-plane config. (Confirmed:
   [Sidero docs](https://docs.siderolabs.com/talos/v1.9/learn-more/control-plane),
   [siderolabs/talos #9942](https://github.com/siderolabs/talos/discussions/9942).)
2. **The wipe of `.25` is safe.** All persistent app storage is network-attached
   democratic-csi iSCSI on hestia (TrueNAS) with `Retain`. `.25` holds no unique local
   data — only the Talos OS install and ephemeral pod state.
3. **Remove the dead `.22` member first.** Adding `.25` without removing `.22` yields a
   **4-member** etcd (`.20/.21/.22-dead/.25`), which needs **3** for quorum — strictly
   worse than today. Removing `.22` first → 2 members, then `.25` joins → **3 members,
   quorum 2, tolerates 1 failure.**
4. **Window risk:** between `etcd remove-member .22` and `.25` rejoining etcd, the cluster
   is a **2-member etcd (quorum 2)** — both `.20` and `.21` must stay up for the whole
   window. Keep it short; do not touch `.20`/`.21` or their shared switch/PSU.
5. Control-plane nodes here are **not** tainted `NoSchedule` (74 app pods ran on `.20`/`.21`
   during the incident), so promoting `.25` does **not** lose worker capacity.

## Pre-flight (all must be true before starting)

- [ ] `talosctl -n 10.42.2.20 etcd status` → `.20` and `.21` healthy, **no `no leader`**,
      both at the same raft index. `etcd alarm list` empty.
- [ ] `kubectl get --raw=/readyz` → `etcd ok`, passes.
- [ ] `.20` and `.21` confirmed physically stable (and ideally on independent power/switch
      from each other and from the maintenance shelves).
- [ ] `controlplane.yaml` for `.25` is staged and validated **before** removing `.22`
      (so the 2-member window is as short as possible).
- [ ] Decision confirmed: `.22` is being **decommissioned** from etcd (it can rejoin later
      only via reset + fresh add, not as the old member).
- [ ] (Preferred) `.23`/`.24` back online first for worker headroom while `.25` reboots —
      not required (cluster ran fine on 2 nodes during the incident), but lower-risk.

## Procedure

All `kubectl`/`talosctl` from this Mac. `talosctl` endpoint = `.20` (a survivor).
`TALOSCONFIG=~/.talos/config` (cluster context `melodic-muse`).

### Phase 1 — Stage the control-plane config for .25
Build `.25`'s control-plane machine config from `controlplane.yaml` + `patch.yaml`,
with a node patch pinning the install disk:
```
# node patch (install-disk.yaml)
machine:
  install:
    disk: /dev/nvme0n1
```
Validate before applying:
```
talosctl validate -c controlplane.yaml --mode metal
```
Do not apply yet.

### Phase 2 — Drain .25
```
kubectl cordon talos-kot-7x7
kubectl drain talos-kot-7x7 --ignore-daemonsets --delete-emptydir-data \
  --disable-eviction --force --timeout=180s
```
(`--disable-eviction --force` is required — singleton-app PDBs at 0 allowed disruptions
block ordinary eviction. Confirmed during the 2026-06-18 maintenance.)
Verify no app pods remain on `.25`. Workloads consolidate onto `.20`/`.21`.

### Phase 3 — Remove the dead .22 etcd member  ⚠️ starts the 2-member window
```
talosctl -n 10.42.2.20 etcd remove-member 5f91a708c22777db
talosctl -n 10.42.2.20 etcd members          # expect ONLY .20 and .21
talosctl -n 10.42.2.20 etcd status           # leader present, no "no leader"
```
From here until Phase 5 completes, **do not touch `.20` or `.21`.**

### Phase 4 — Reset .25 and reinstall as control-plane
```
# Wipe .25 back to maintenance mode (it is a worker; safe — no local data)
talosctl -n 10.42.2.25 reset --graceful=false --reboot \
  --system-labels-to-wipe STATE --system-labels-to-wipe EPHEMERAL
# When .25 is back in maintenance mode (no config), apply the control-plane config:
talosctl -n 10.42.2.25 apply-config --insecure \
  --file controlplane.yaml --config-patch @patch.yaml --config-patch @install-disk.yaml
```
- `.25` boots as control-plane, discovers the cluster via the `.20:6443` endpoint, and
  **auto-joins etcd as a new member**.
- Do **not** run `bootstrap` — that is only for a brand-new cluster and would be
  catastrophic here.

### Phase 5 — Verify etcd 3-member + node healthy
```
talosctl -n 10.42.2.20 etcd members     # expect .20, .21, .25
talosctl -n 10.42.2.20 etcd status      # all in sync, leader present, no alarms
kubectl get nodes                        # talos-kot-7x7 now ROLES=control-plane, Ready
kubectl get --raw=/readyz                # etcd ok
```
Closes the 2-member window. Cluster is now fault-tolerant (3 members, tolerates 1 loss).

### Phase 6 — Uncordon + confirm heal
```
kubectl uncordon talos-kot-7x7
kubectl get clusters.postgresql.cnpg.io -A   # prod clusters return to 3/3
```

## Abort / rollback

- **Quorum lost during the window** (`.20` or `.21` dies before `.25` joins): get the
  dead survivor back on the network immediately — etcd recovers once ≥2 members are up.
  Never `bootstrap` or `etcd --force-new-cluster` unless a survivor is *permanently* gone
  (single-member force-new-cluster is lossy and a last resort).
- **`.25` fails to join etcd after reinstall:** check `talosctl -n .25 dmesg`,
  `talosctl -n .25 service etcd`. If the member was half-added, `etcd remove-member` the
  partial `.25` member and retry Phase 4. Cluster stays at 2-member meanwhile.
- **Wrong install disk / `.25` won't boot:** it's already drained and removed from etcd
  scope; reset and reapply with the corrected disk. No impact on `.20`/`.21`.

## Done = all true

- etcd has **3 healthy members** (`.20`, `.21`, `.25`), in sync, no alarms.
- `talos-kot-7x7` is `control-plane`, `Ready`; `/readyz` passes.
- All prod CNPG clusters healthy; Flux green.
- Cluster tolerates a single node loss again.

## Follow-up (not blocking)

- `.22` hardware verdict (DIMM/board/RMA). If `.22` is repaired later, it rejoins as a
  **4th** control-plane via reset + fresh config (an even member count — consider whether
  to keep 3 and leave `.22` a worker, or go to 5 with `.23`/`.24`).
- Capture a Talos node-maintenance / control-plane-promotion runbook in
  `docs/operations/` once this runs clean.
- Revisit etcd resilience posture: 3 control-plane on shared shelves/switch is a
  correlated-failure risk (root cause of the 2026-06-18 outage).
