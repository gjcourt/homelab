# Runbook: Promote a Talos worker to control-plane (replace a dead etcd member)

**When to use:** an etcd/control-plane member died and won't return (hardware), leaving etcd
below 3 healthy members. This promotes a surviving worker to restore a fault-tolerant
3-member etcd. First executed 2026-06-19 (`.22` dead → promoted `.23`); see
[`docs/plans/2026-06-19-promote-talos-25-to-controlplane.md`](../plans/2026-06-19-promote-talos-25-to-controlplane.md)
for the original incident. Related: [Talos node maintenance](2026-06-17-talos-node-maintenance.md),
[`feedback_talos_shelf_correlated_failure`].

Config source: `~/src/melodic-muse/` (`controlplane.yaml`, `patch.yaml`, `talosconfig` —
raw `talosctl gen config`, not talhelper). `TALOSCONFIG=~/.talos/config`. Run from the Mac.

## Choosing which worker to promote

An etcd member's **power/fault independence outranks temperature** — *when you have a choice.* The
ideal is 3 control-plane nodes on 3 independent switch + power paths, so no single device drops 2 of 3.

**Reality check first (do this before agonizing over node choice):** map where the nodes actually
plug in. On melodic-muse (verified 2026-06-19) **all nodes share one Rack Switch (USWED42) and one
USP PDU Pro** (on separate outlets). So switch/PSU independence between control-plane nodes **isn't
achievable** — the Rack Switch and the PDU are accepted single points of failure, and which worker you
promote doesn't change that. In that situation, just promote any healthy worker and let the coolest box
stay the worker (it carries app load). thermalscope gives temps; the switch/PDU map is in the **UniFi
controller** (Topology view; PDU outlet power via unpoller `unpoller_device_outlet_outlet_power`).
The wired-client→port map is **not** in Prometheus (`unpoller_client_info` = 0 series) — use the UniFi UI.

**The mitigation that actually helps** when everything's on one switch + PDU: put the **Rack Switch and
PDU on a UPS**, so a brief power/path blip (the 2026-06-18 trigger) doesn't cascade. A 3-member etcd then
tolerates the realistic failure (a single node/outlet loss); whole-switch/whole-PDU loss stays a known SPOF.

## Pre-flight (all true before starting)

- [ ] `talosctl -n <survivor-cp> etcd status` → survivors healthy, leader present, no `no leader`, same raft index.
- [ ] `talosctl -n <survivor-cp> etcd alarm list` → empty.
- [ ] `kubectl get --raw='/readyz?verbose'` → `etcd ok`, passes. **(Quote the URL — zsh globs the `?`.)**
- [ ] Target worker's install disk confirmed: `talosctl -n <target> get disks` (e.g. `/dev/nvme0n1`).
- [ ] `controlplane.yaml` validates: `talosctl validate -c controlplane.yaml --mode metal`.
- [ ] Decision confirmed: the dead member is **decommissioned** (rejoins later only via reset + fresh add).

## Procedure

```bash
export TALOSCONFIG=~/.talos/config
cd ~/src/melodic-muse
SURV=10.42.2.20            # a healthy control-plane survivor
TGT=10.42.2.23            # the worker being promoted
DEAD=5f91a708c22777db     # dead member ID from `etcd members`

# install-disk patch (match `get disks`)
printf 'machine:\n  install:\n    disk: /dev/nvme0n1\n' > install-disk.yaml

# 1. Drain the target (PDBs at 0 disruptions need --disable-eviction --force)
kubectl drain <target-nodename> --ignore-daemonsets --delete-emptydir-data --disable-eviction --force --timeout=180s

# 2. Remove the dead member  ⚠️ opens the 2-member window — both survivors must stay up now
talosctl -n $SURV etcd remove-member $DEAD
talosctl -n $SURV etcd members          # expect only the survivors

# 3. Reset the target to maintenance mode, then apply control-plane config
talosctl -n $TGT reset --graceful=false --reboot \
  --system-labels-to-wipe STATE --system-labels-to-wipe EPHEMERAL
#    wait until it's in maintenance mode (poll), THEN:
talosctl -e $TGT -n $TGT apply-config --insecure \
  --file controlplane.yaml --config-patch @patch.yaml --config-patch @install-disk.yaml

# 4. Verify it joins etcd as the 3rd member (closes the window)
talosctl -n $SURV etcd members          # expect 3 members incl. $TGT
talosctl -n $SURV etcd status; talosctl -n $SURV etcd alarm list

# 5. Finalize
kubectl uncordon <worker-nodename>
kubectl delete node <stale-old-target> <dead-node> <any-out-of-rack>   # ghosts
```
**Never run `bootstrap`** — that's for a brand-new cluster and is catastrophic on a live one.

## Gotchas (cost real time on 2026-06-19)

- **macOS has no `timeout(1)`.** Wrapping `talosctl reset` in `timeout` → `command not found`,
  silently no-ops the whole command. Don't wrap; background the reset and poll for maintenance mode.
- **Maintenance-mode talosctl needs `-e <nodeIP>`**, not just `-n`. With `TALOSCONFIG` set the
  endpoint defaults to the cluster API (`.20`), so `-n <target> --insecure` never reaches the
  unconfigured node. Use `-e <target> -n <target> --insecure`.
- **`get disks` rejects `--insecure`** (not a global flag). To detect maintenance mode, probe the
  secure API: a freshly-reset node answers with `x509: certificate signed by unknown authority`
  (fresh identity) instead of authenticating — that's the maintenance-mode tell.
- **Reset regenerates the node identity.** With `hostname: auto (stable)`, the node rejoins under a
  **new name** (e.g. `talos-lmh-kyf` → `talos-fpd-h0t`). Delete the stale node object afterward.

## Post-checks (some are physical — don't skip)

- [ ] etcd 3 healthy members, in sync, no alarms; `/readyz` ok; all nodes Ready; Flux green.
- [ ] **Map the switch + power topology of the control-plane nodes** (UniFi UI / PDU outlet metrics).
      If they're independent, good. If they share a switch and/or PDU (as on melodic-muse — single Rack
      Switch + single USP PDU Pro), accept those as named SPOFs and **put the switch + PDU on a UPS** —
      that's the mitigation that addresses the realistic blip-on-a-shared-path failure.
- [ ] Prod CNPG clusters healthy; watch any staging clusters re-spin replicas after the node churn.
