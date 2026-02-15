# Synology iSCSI Repair Tools

These scripts help diagnose and fix "zombie" iSCSI targets on Synology NAS devices that cause Kubernetes storage failures (e.g., `Volume not found`, `I/O error`, `Target Busy`).

## Prerequisites

- Python 3 with `paramiko` library installed.
  ```bash
  pip3 install paramiko
  ```
- SSH access to your Synology NAS (user must be in `administrators` group or `root`).
- Environment variables set for credentials:
  ```bash
  export SYNOLOGY_USER="your-user"
  export SYNOLOGY_PASSWORD="your-password"
  ```
  *(Note: For security, consider using a read-only user for listing, but deletion requires admin/root privileges via `sudo` or direct root login if enabled. The scripts utilize `synosystemctl` which requires root permissions, so the user might need to be `root` or have sudoers setup. Default Synology admin usually works with password.)*

## Manifest

- **`list_targets.py`**: Lists all iSCSI targets currently configured in `/usr/syno/etc/iscsi_target.conf`. Use this to identify the IQN of the "zombie" target matching your persistent volume (e.g., `pvc-uuid`).
- **`force_delete_target.py`**: Surgically removes a specific target block from the configuration file and restarts the iSCSI service. Usage:
  ```bash
  python3 force_delete_target.py --target-name "pvc-uuid"
  ```
- **`rebind_luns.py`**: (Formerly `repair_iscsi_conf.py` / `rebind`) Recreates missing targets/LUN mappings. Run this *after* deleting a zombie target to restore connectivity.
  ```bash
  python3 rebind_luns.py
  ```

## Workflow for "Target Busy" / Stuck Volumes

1.  **Identify the Zombie**:
    Run `list_targets.py` to see what is currently capable of being mapped.
    Check your Kubernetes logs (`kubectl describe pod ...`) to find the PVC ID (e.g., `pvc-1234...`).
    
2.  **Surgical Removal**:
    If the target exists but is unreachable or "busy" (error `18990710`), use the force delete tool:
    ```bash
    python3 force_delete_target.py --target-name "pvc-1234..."
    ```
    
3.  **Rebind/Recreate**:
    Immediately run the rebind script to generate a clean config for the target:
    ```bash
    python3 rebind_luns.py
    ```

4.  **Verify**:
    Restart the affected Kubernetes pod.
