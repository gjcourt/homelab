# Synology iSCSI / Volume Debugging

This cluster uses a Synology NAS (192.168.5.8) as the iSCSI storage backend via the `synology-csi` driver. Volume issues are among the most common failure modes. Use this section to diagnose and fix them efficiently.

## Architecture

- **LUN**: Block device on the NAS at `/volume1/@iSCSI/LUN/BLUN/<uuid>/`. Named `k8s-csi-pvc-<UUID>` by the CSI driver.
- **Target**: iSCSI endpoint identified by IQN `iqn.2000-01.com.synology:alcatraz.pvc-<UUID>`. Each LUN maps to exactly one target.
- **PV/PVC**: Kubernetes abstractions referencing the target IQN. CSI driver translates PVC requests into LUN+Target creation on the NAS.
- **Max targets**: 128 (hard Synology limit). Monitor proactively.

## Common failure modes and diagnosis

1. **Pods stuck in `ContainerCreating` / `FailedMount`**:
    - Check `kubectl describe pod <pod>` for mount errors.
    - Check CSI plugin logs: `kubectl logs -n synology-csi -l app=synology-csi-node -c csi-plugin --tail=50`.
    - Common causes: disabled targets, unmapped LUNs, stale iSCSI sessions.

2. **Read-only filesystem (btrfs remounted ro)**:
    - Symptom: apps crash with write errors, `dmesg` shows `BTRFS warning: btrfs_check_rw_degradable`.
    - Root cause: iSCSI I/O errors cause btrfs to remount read-only.
    - **Fix**: Scale deployment to 0 (forces unmount), wait 30s, scale back to 1 (forces fresh mount). If the underlying iSCSI path is broken, fix that first.
    - Check iSCSI sessions from the CSI node plugin: `kubectl exec -n synology-csi <csi-node-pod> -c csi-plugin -- iscsiadm -m session -P3 | grep -A5 "Target:"`.

3. **LUN mismatch (wrong lun-N)**:
    - Symptom: mount fails or mounts wrong device.
    - The CSI driver expects `lun-1`. After NAS operations, LUNs may be remapped to `lun-2` or higher.
    - **Fix**: Rescan iSCSI session to restore correct lun mapping: `iscsiadm -m session -r <session-id> --rescan` (run from within the CSI node plugin container).

4. **Released PVs (CNPG clusters)**:
    - Symptom: CNPG pods Pending, PVCs Pending, old PVs in `Released` state.
    - **Fix**: Remove stale UID from PV claimRef: `kubectl patch pv <pv-name> --type=json -p '[{"op":"remove","path":"/spec/claimRef/uid"},{"op":"remove","path":"/spec/claimRef/resourceVersion"}]'`.
    - Prefer the oldest PV (by `.metadata.creationTimestamp`) as it likely has real data.

5. **Disabled targets on NAS**:
    - The Synology `synoiscsiwebapi` CLI silently sets `enabled=no` on targets during LUN mapping operations.
    - Always run `python3 scripts/synology/enable_targets.py` after any NAS LUN operation.
    - Audit: `python3 scripts/synology/audit_luns.py`.

## Synology NAS access

- **DSM API**: `https://192.168.5.8:5001` (HTTPS, self-signed cert — use `-k` with curl).
- **SSH**: Port 22, same credentials.
- **Credentials**: Stored in `client-info-secret` in the `synology-csi` namespace. Retrieve with: `kubectl get secret -n synology-csi client-info-secret -o jsonpath='{.data.client-info\.yaml}' | base64 -d`.
- **Scripts**: All operational scripts are in `scripts/synology/`. See `scripts/synology/README.md`.

## Diagnostic workflow (run in order)

```bash
# 1. Find unhealthy pods
kubectl get pods -A --field-selector 'status.phase!=Running,status.phase!=Succeeded'

# 2. Check for iSCSI-related mount errors
kubectl describe pod <pod> | grep -A5 "Events:"

# 3. Audit NAS LUN/target state
python3 scripts/synology/audit_luns.py

# 4. Check for disabled targets
python3 scripts/synology/enable_targets.py

# 5. Check iSCSI sessions from the node
kubectl exec -n synology-csi <csi-node-pod> -c csi-plugin -- iscsiadm -m session

# 6. Check for Released PVs
kubectl get pv | grep Released
```

## Documentation references

- Operations runbook: `docs/guides/synology-iscsi-operations.md`
- Cleanup concepts: `docs/guides/synology-iscsi-cleanup.md`
- Incident postmortems: `docs/incidents/`
