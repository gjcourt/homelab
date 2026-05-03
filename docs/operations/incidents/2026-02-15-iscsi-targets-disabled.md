# Postmortem: Disabled iSCSI Targets & Orphaned LUNs Recovery

**Date:** 2026-02-15
**Status:** Resolved
**Severity:** Critical (All iSCSI-backed pods offline)
**Authors:** Copilot

## 1. Incident Summary

All Kubernetes pods using Synology iSCSI-backed PersistentVolumes failed to
start, reporting "volume not found" and iSCSI connection errors. Investigation
revealed that 126 of 128 iSCSI targets on the NAS had `enabled=no` in their
configuration, making them invisible to the CSI driver. Additionally, 57
orphaned LUNs (no matching Kubernetes PV) were consuming storage, and several
CNPG database PVs were in Released state, preventing PostgreSQL clusters from
starting.

## 2. Root Cause

### Primary: Disabled Targets

The `synoiscsiwebapi` CLI tool creates targets in a disabled state when
performing LUN-to-target rebinding (the `target map_lun` / `lun map_target`
commands). During the Feb 12 recovery (see
[2026-02-12-iscsi-zombie-targets.md](2026-02-12-iscsi-zombie-targets.md)),
`rebind_luns_ssh.py` successfully mapped 126 LUNs back to their targets, but
the mapping operation set `enabled=no` in `/usr/syno/etc/iscsi_target.conf` for
each affected target. This was not caught because the script only verified
mapping success, not target enablement.

The `enabled=no` field appears on the same line as the `iqn=` field in the INI
config file (e.g., `iqn=iqn.2000-01.com.synology:...enabled=no`), which made
it difficult to detect with simple grep patterns.

### Secondary: Orphaned LUNs

Over time, Kubernetes PVs were deleted (via PVC deletion, scaling events, or
CNPG cluster reconfigurations) without the CSI driver cleaning up the
corresponding NAS-side LUNs and targets. This accumulated 57 orphaned LUNs
and their targets, consuming storage space and config slots.

### Tertiary: Released PVs

CNPG cluster scaling and redeployments created new PVCs that provisioned new
PVs, while the old PVs (containing actual database data) remained in Released
state. The new PVs were empty block devices, causing "format of disk failed"
errors when CNPG attempted to initialize them.

## 3. Timeline

- **2026-02-12**: LUN-to-target rebinding performed (126 LUNs). Targets
  inadvertently left in disabled state. Pods begin failing.
- **2026-02-15 ~10:00**: Investigation begins. Discovered 126/128 targets have
  `enabled=no`.
- **~10:30**: Created `enable_targets.py`. Enabled all 126 disabled targets.
  Verified 128/128 targets now `enabled=yes`.
- **~11:00**: Pods begin recovering. Jellyfin reaches Running state. Some apps
  still failing (GHCR auth — unrelated), immich pods have "format of disk"
  errors.
- **~12:00**: Created `audit_luns.py`. Audit reveals 57 orphaned LUNs, 12
  Released PVs, 58 Bound PVs.
- **~13:00**: Created `cleanup_orphans.py`. Dry run confirms 58 orphaned LUNs
  to delete.
- **~13:30**: Executed orphan cleanup. 58 LUNs + targets deleted. 0 orphans
  remaining.
- **~14:00**: Analyzed Released PVs. Found CNPG clusters had duplicate PVs
  (new empty + old with data). Identified oldest PVs by creation timestamp.
- **~14:30**: Rebound 3 Released PVs to pending PVCs by clearing
  `claimRef.uid`:
  - `pvc-1a684683` → immich-prod cnpg-v1-2 (created Feb 8, contains data)
  - `pvc-4d030790` → immich-prod cnpg-v1-3 (created Feb 8, contains data)
  - `pvc-dda2e0a0` → immich-stage cnpg-v1-3 (created Feb 13)
- **~15:00**: CNPG clusters recovered (3/3 replicas Running). The `-app` secret
  auto-created by CNPG operator once all instances were healthy. Immich server
  pod reached Running state.
- **~15:30**: Deleted 2 remaining stale targets (no LUN mapped). Cleaned up 8
  obsolete scripts. Final state verified clean.

## 4. Resolution

### Step 1: Enable disabled targets

```bash
python3 scripts/synology/enable_targets.py
```

Parsed the full `iscsi_target.conf`, found all targets with `enabled=no`, and
called `synoiscsiwebapi target enable <tid>` for each. 126 targets re-enabled.

### Step 2: Audit and clean orphaned LUNs

```bash
python3 scripts/synology/audit_luns.py          # Diagnose
python3 scripts/synology/cleanup_orphans.py      # Dry run
python3 scripts/synology/cleanup_orphans.py --execute  # Delete
```

For each orphan: unmap LUN from target → delete LUN → delete target.

### Step 3: Rebind Released PVs to CNPG clusters

```bash
kubectl patch pv <pv-name> --type=json \
  -p '[{"op":"remove","path":"/spec/claimRef/uid"},
       {"op":"remove","path":"/spec/claimRef/resourceVersion"}]'
```

This moved PVs from Released → Available. Kubernetes auto-bound them to the
matching pending PVCs based on `claimRef.name` and `claimRef.namespace`.

## 5. Impact

- **Duration**: ~3 days (Feb 12–15). Pods were failing since the rebinding on
  Feb 12, but the disabled-target root cause was not identified until Feb 15.
- **Services affected**: All iSCSI-backed apps (immich, jellyfin, memos,
  linkding, mealie, audiobookshelf, homeassistant, etc.).
- **Data loss**: None. Old PVs with data were successfully rebound. Orphaned
  LUNs that were deleted had no corresponding Kubernetes PV and were not in use.

## 6. Corrective Measures

### Scripts consolidated

Obsolete and broken scripts were removed. The operational toolkit is now 4
scripts in `scripts/synology/`:

| Script | Purpose |
|--------|---------|
| `audit_luns.py` | Cross-reference NAS LUNs with K8s PVs |
| `cleanup_orphans.py` | Delete orphaned LUNs (dry-run by default) |
| `enable_targets.py` | Enable disabled iSCSI targets |
| `rebind_luns_ssh.py` | Map orphaned LUNs to their targets |

See [scripts/synology/README.md](../../scripts/synology/README.md) for detailed
usage.

### Deleted scripts

The following were removed as broken (Web API), dangerous (direct config
editing), or one-time analysis tools:

- `repair_iscsi_conf.py`, `synology_prune_zombies.py`, `synology_rebind_luns.py`
- `force_delete_target.py`, `list_targets.py`, `rebind_luns.py`
- `analyze_pv_data.py`, `analyze_pvs.py`

## 7. Prevention & Recommendations

1. **Always verify target enablement after rebinding.** The `rebind_luns_ssh.py`
   workflow should be followed by `enable_targets.py`. The
   [operations runbook](../synology-iscsi-operations.md) documents this.
2. **Run periodic audits.** Schedule `audit_luns.py` monthly or after any
   storage-related incident to catch orphaned LUNs early.
3. **Monitor the CSI driver.** If pods fail with iSCSI errors, check target
   enablement before assuming LUN mapping issues.
4. **CNPG PV lifecycle.** When CNPG clusters are redeployed, check for Released
   PVs that may contain data. Use `kubectl get pv | grep Released` to identify
   candidates for rebinding.
5. **Document the `enabled=no` quirk.** The Synology `synoiscsiwebapi` CLI sets
   `enabled=no` on targets during certain operations. This is not documented by
   Synology and must be worked around explicitly.
