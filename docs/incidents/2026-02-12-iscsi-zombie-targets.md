# Postmortem: Synology "Zombie Target" & Orphan LUNs Recovery
**Date:** 2026-02-12
**Status:** Resolved
**Severity:** High (Storage Provisioning Blocked)
**Authors:** Copilot

## 1. Incident Summary
The Synology NAS (`192.168.5.8`) reached its maximum iSCSI target limit (128), preventing the Kubernetes cluster from provisioning new persistent volumes. Attempts to delete old "recovery" targets via the Synology API failed with error `18990710` ("Target Busy"), even after a reboot. The system was stuck in a "Zombie" state where targets deleted from the kernel would reappear upon service restart due to a configuration desynchronization.

## 2. Root Cause
*   **Layer Desynchronization**: The Synology management layer (User-space DB/Config) and the iSCSI Kernel Layer (ConfigFS) became out of sync.
*   **Persistence Loop**: The configuration file `/usr/syno/etc/iscsi_target.conf` acted as the source of truth. Even when targets were manually nuked from the kernel (`/sys/kernel/config/target/iscsi`), the `pkg-iscsi` service would read this file on restart and resurrect the bad targets.
*   **API Deadlock**: The API logic flagged these "recovery" targets as busy/invalid, refusing deletion requests, while the kernel considered them active active, creating a Catch-22.

## 3. Timeline
*   **2026-02-12 10:00 UTC**: Kubernetes PVC provisioning failed.
*   **10:30 UTC**: Diagnosed 128/128 target limit reached. 4x `recovery-target` entries found blocking slots.
*   **11:00 UTC**: Attempted deletion via API scripts; failed with "Target Busy".
*   **11:30 UTC**: Rebooted Synology; targets persisted.
*   **12:00 UTC**: Escalated to SSH/Root. Manually removed targets from ConfigFS.
*   **12:15 UTC**: Service restart (`systemctl restart pkg-iscsi`) restored the deleted targets. Root cause (Persistence Loop) identified.
*   **13:00 UTC**: Developed `repair_iscsi_conf.py` to surgically edit the config file.
*   **13:15 UTC**: Applied fix. Service restarted. 4 slots freed.
*   **13:30 UTC**: Developed `synology_prune_orphans.py` to clean up remaining orphaned LUNs.
*   **13:45 UTC**: Cleanup complete. 22 Targets and 6 LUNs reclaimed.

## 4. Resolution
We bypassed the blocked API entirely and performed a "Deep Clean" on the persistent configuration files.

1.  **Stop Service**: `systemctl stop pkg-iscsi`
2.  **Config Surgery**: Parsed `/usr/syno/etc/iscsi_target.conf` and `/usr/syno/etc/iscsi_lun.conf`, removing blocks associated with orphans.
3.  **Safe Upload**: Used Base64 encoding to upload modified configs via SSH (bypassing shell buffer issues).
4.  **Restart Service**: `systemctl start pkg-iscsi`.

## 5. Corrective Measures (Scripts)

> **Note (2026-02-15):** The scripts referenced below (`repair_iscsi_conf.py`,
> `synology_prune_orphans.py`) have been superseded and deleted. The current
> operational toolkit lives in `scripts/synology/`. See
> [scripts/synology/README.md](../../scripts/synology/README.md) and the
> [operations runbook](../synology-iscsi-operations.md) for current procedures.

## 6. Prevention & Recommendations
1.  **Avoid Hard Shutdowns**: Ensure Kubernetes nodes unmount PVCs cleanly before Synology reboots to prevent "stale" sessions that create these zombie targets.
2.  **Monitor Capacity**: Set up alerts when iSCSI target count approaches 100 (Max 128).
3.  **Orphan Crons**: Periodically run `audit_orphans.py` (read-only mode of the prune script) to detect leaks early.
