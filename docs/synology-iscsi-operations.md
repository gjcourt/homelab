# Synology iSCSI Operations Runbook

This document covers common operational tasks for the Synology NAS iSCSI storage
that backs the Kubernetes cluster's PersistentVolumes via the `synology-csi`
driver.

## Architecture Overview

```
┌─────────────────┐     iSCSI      ┌──────────────────────┐
│  K8s Node        │◄──────────────►│  Synology NAS        │
│  (melodic-muse)  │               │  (192.168.5.8)       │
│                  │               │                      │
│  synology-csi    │               │  LUN ──► Target      │
│  driver creates: │               │  (block)  (iSCSI     │
│  - PV            │               │           endpoint)  │
│  - PVC           │               │                      │
└─────────────────┘               └──────────────────────┘
```

- **LUN**: A block device on the NAS, stored at `/volume1/@iSCSI/LUN/BLUN/<uuid>/`.
  Named `k8s-csi-pvc-<UUID>` by the CSI driver.
- **Target**: An iSCSI endpoint identified by an IQN
  (e.g., `iqn.2000-01.com.synology:alcatraz.pvc-<UUID>`). Each LUN is mapped to
  exactly one target.
- **PV/PVC**: Kubernetes abstractions that reference the target IQN. The CSI
  driver translates PVC requests into LUN+Target creation on the NAS.

## Prerequisites

```bash
pip3 install paramiko
export SYNOLOGY_HOST="192.168.5.8"
export SYNOLOGY_USER="manager"
export SYNOLOGY_PASSWORD="your-password-here"
```

All scripts live in `scripts/synology/`. See
[scripts/synology/README.md](../scripts/synology/README.md) for per-script
details.

## Common Scenarios

### Pods stuck in ContainerCreating with iSCSI errors

**Symptoms:**
- `kubectl describe pod` shows `FailedAttachVolume` or `FailedMount`
- CSI driver logs show "volume not found" or "target not found"
- iSCSI initiator cannot connect to NAS targets

**Diagnosis:**
```bash
# Check which LUNs/targets are healthy
python3 scripts/synology/audit_luns.py

# Check for disabled targets specifically
python3 scripts/synology/enable_targets.py
```

**Resolution:**
```bash
# If targets are disabled:
python3 scripts/synology/enable_targets.py

# If LUNs are unmapped from targets:
python3 scripts/synology/rebind_luns_ssh.py
python3 scripts/synology/enable_targets.py   # always run after rebinding
```

### Orphaned LUNs consuming storage

**Symptoms:**
- NAS storage usage higher than expected
- `audit_luns.py` shows orphaned LUNs (no matching K8s PV)

**Resolution:**
```bash
python3 scripts/synology/audit_luns.py           # see the state
python3 scripts/synology/cleanup_orphans.py       # dry run
python3 scripts/synology/cleanup_orphans.py --execute  # delete
python3 scripts/synology/audit_luns.py           # verify
```

### CNPG PostgreSQL cluster won't start (Released PVs)

**Symptoms:**
- CNPG pods stuck in `Pending` or `ContainerCreating`
- PVCs in `Pending` state
- `kubectl get pv` shows matching PVs in `Released` state

**Diagnosis:**
```bash
# Find Released PVs
kubectl get pv | grep Released

# Check which claim they belong to
kubectl get pv <pv-name> -o jsonpath='{.spec.claimRef}' | jq .
```

**Resolution:**
```bash
# Clear the stale UID so the PV becomes Available and re-binds
kubectl patch pv <pv-name> --type=json \
  -p '[{"op":"remove","path":"/spec/claimRef/uid"},
       {"op":"remove","path":"/spec/claimRef/resourceVersion"}]'

# Verify the PVC binds
kubectl get pvc -n <namespace>
```

> **Tip:** If multiple Released PVs match the same claim, prefer the oldest one
> (check `.metadata.creationTimestamp`) as it likely contains the actual data.

### After Synology firmware update or reboot

Run the full diagnostic workflow:
```bash
python3 scripts/synology/audit_luns.py
python3 scripts/synology/enable_targets.py
python3 scripts/synology/audit_luns.py
```

Check for pods that failed to reconnect:
```bash
kubectl get pods --all-namespaces --field-selector 'status.phase!=Running,status.phase!=Succeeded'
```

## Key Quirks & Gotchas

1. **`enabled=no` after mapping**: The `synoiscsiwebapi` CLI may set
   `enabled=no` on targets during LUN mapping operations. Always run
   `enable_targets.py` after `rebind_luns_ssh.py`.

2. **Config file format**: In `iscsi_target.conf`, the `enabled=` field is
   appended to the end of the `iqn=` line (not on its own line). Simple
   `grep enabled` patterns may miss it.

3. **Target limit**: The NAS supports a maximum of 128 iSCSI targets. Monitor
   with `audit_luns.py` and clean orphans proactively.

4. **LUN name mapping**: The CSI driver names LUNs `k8s-csi-pvc-<UUID>`, while
   Kubernetes PVs are named `pvc-<UUID>`. Strip the `k8s-csi-` prefix to match.

5. **`du` reports provisioned size**: Running `du` on LUN backing files
   (`/volume1/@iSCSI/LUN/BLUN/*/`) reports the provisioned (thin) size, not
   actual data usage. Do not rely on `du` to determine which LUNs have real data.

6. **CNPG auto-creates secrets**: The CNPG operator automatically creates the
   `-app` and `-superuser` secrets once a cluster has all instances running.
   Do not create these manually unless the cluster is unhealthy.

## Related Documentation

- [Incident: Zombie targets (2026-02-12)](incidents/2026-02-12-iscsi-zombie-targets.md)
- [Incident: Disabled targets (2026-02-15)](incidents/2026-02-15-iscsi-targets-disabled.md)
- [PV recovery (2026-02-08)](incidents/2026-02-08-pv-recovery.md)
- [Synology iSCSI cleanup](synology-iscsi-cleanup.md)
