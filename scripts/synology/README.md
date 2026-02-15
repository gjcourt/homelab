# Synology iSCSI Operations Toolkit

Scripts for managing Synology NAS iSCSI storage used by the Kubernetes cluster's
CSI driver (`synology-csi`). All scripts use SSH + the `synoiscsiwebapi` CLI
tool on the NAS — they do **not** use the Synology Web API.

## Prerequisites

- Python 3 with `paramiko`:
  ```bash
  pip3 install paramiko
  ```
- SSH access to the Synology NAS (user in `administrators` group).
- Environment variables:
  ```bash
  export SYNOLOGY_HOST="192.168.5.8"   # NAS IP
  export SYNOLOGY_USER="manager"        # SSH user
  export SYNOLOGY_PASSWORD="..."        # SSH password
  ```
- `kubectl` configured with cluster access (for K8s cross-referencing).

## Scripts

### `audit_luns.py` — Audit LUN/target state

Cross-references LUNs and targets on the NAS with Kubernetes PersistentVolumes.
Reports counts for Bound, Released, and Orphaned LUNs, plus targets with no LUN.

```bash
python3 scripts/synology/audit_luns.py
```

Output example:
```
=== LUN AUDIT ===
Total LUNs on NAS: 70
Total Targets on NAS: 70
  Matched to Bound PV: 62
  Matched to Released/Available PV: 8
  No K8s PV (orphaned): 0
```

**When to use:** Before and after any repair operation, or as a periodic health
check. Always run this first to understand current state.

### `cleanup_orphans.py` — Remove orphaned LUNs

Deletes LUNs that have no matching Kubernetes PV (orphaned). For each orphan,
unmaps the LUN from its target, deletes the LUN, then deletes the target.

```bash
# Dry run (default) — shows what would be deleted
python3 scripts/synology/cleanup_orphans.py

# Execute deletions
python3 scripts/synology/cleanup_orphans.py --execute
```

**When to use:** After `audit_luns.py` shows orphaned LUNs. Always do a dry run
first.

### `enable_targets.py` — Enable disabled targets

Finds iSCSI targets with `enabled=no` in the config and enables them via the
CLI. Disabled targets cause pods to fail with "volume not found" errors even
when LUN mappings are correct.

```bash
python3 scripts/synology/enable_targets.py
```

**When to use:** After mass LUN rebinding, firmware updates, or any operation
that may have disabled targets. Symptoms: pods stuck in `ContainerCreating` with
iSCSI connection errors.

### `rebind_luns_ssh.py` — Rebind orphaned LUNs to targets

Finds LUNs that exist on the NAS but are not mapped to any target, matches them
to existing targets by PVC UUID, and creates the mapping. Run `enable_targets.py`
afterward since newly-mapped targets may start disabled.

```bash
python3 scripts/synology/rebind_luns_ssh.py
```

**When to use:** When LUNs exist on the NAS but are unmapped from their targets
(usually after iSCSI service restarts or config corruption). Symptoms: pods fail
with "LUN not found" in CSI driver logs.

## Typical Recovery Workflow

```
1. Diagnose:    python3 scripts/synology/audit_luns.py
2. Rebind:      python3 scripts/synology/rebind_luns_ssh.py  (if unmapped LUNs)
3. Enable:      python3 scripts/synology/enable_targets.py   (if disabled targets)
4. Clean:       python3 scripts/synology/cleanup_orphans.py  (dry run, then --execute)
5. Verify:      python3 scripts/synology/audit_luns.py
```

## How It Works

- **Name mapping**: The CSI driver creates LUNs named `k8s-csi-pvc-<UUID>` and
  targets named with IQN `iqn.2000-01.com.synology:*.pvc-<UUID>`. The scripts
  match them by extracting the PVC UUID from both sides.
- **SSH + CLI**: Scripts SSH to the NAS and run
  `/usr/local/bin/synoiscsiwebapi` with `sudo`. This is the internal CLI tool
  that the DSM UI calls; it is more reliable than the HTTP-based Web API for
  bulk operations.
- **Config files**: The NAS stores iSCSI config in:
  - `/usr/syno/etc/iscsi_target.conf` — target definitions
  - `/usr/syno/etc/iscsi_lun.conf` — LUN definitions
  - `/usr/syno/etc/iscsi_mapping.conf` — LUN-to-target mappings
