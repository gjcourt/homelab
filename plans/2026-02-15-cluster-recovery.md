# Cluster Recovery Plan - 2026-02-15

## Goal
Methodically identify and fix issues with failing applications in the cluster.

## Status Overview
- **golinks**: ImagePullBackOff (Private Registry) -> **Action Required**: Rotate GitHub Personal Access Token (PAT).
- **biometrics**: ImagePullBackOff (Private Registry) -> **Action Required**: Rotate GitHub Personal Access Token (PAT).
- **homeassistant**: CrashLoopBackOff -> **Diagnosed**: `OSError: [Errno 5] I/O error`. Likely iSCSI target disconnected/unmapped.
- **homepage**: Recovered.
- **excalidraw**: Recovered.
- **jellyfin**: Stuck Creating -> **Diagnosed**: `Volume not found`. Target missing or unmapped on Synology.
- **linkding**: CrashLoopBackOff -> Storage issue.
- **memos**: CrashLoopBackOff -> Storage issue.

## Diagnosis
1.  **Image Pull Auth**: The current PAT in `secret-ghcr.yaml` is invalid (verified via 401 Unauthorized to `ghcr.io`). The user confirmed the key works locally, but the one in the repo is likely expired or invalid for this environment. **The key must be rotated.**
2.  **iSCSI Storage Failure**:
    - `repair_iscsi_conf.py` found no "recovery" blocks to remove, meaning the config is clean.
    - However, stuck "zombie" targets exist that the API cannot modify (Error `18990710`).
    - **Action**: Use the new SSH-based force delete script (`scripts/synology/force_delete_target.py`) to surgically remove the stuck targets, then let the rebind script (`scripts/synology/rebind_luns.py`) recreate them.

## Execution Plan & Progress

### 1. Fix Private Registry Authentication
- **Status**: **User Action Required**
- **Task**: The GitHub PAT in `apps/base/golinks/secret-ghcr.yaml` (and biometrics) is invalid.
- **Instruction**:
    1.  Generate a new PAT (Classic) with `read:packages` scope.
    2.  Update `apps/base/golinks/secret-ghcr.yaml` and `apps/base/biometrics/secret-ghcr.yaml` with the new token.
    3.  Re-encrypt with SOPS.
    4.  Commit and push.

### 2. Recover iSCSI Storage (Zombie Targets)
- **Status**: **User Action Required**
- **Task**: Force delete stuck targets via SSH and recreate them.
- **New Tool**: `scripts/synology/force_delete_target.py`
- **Instruction**:
    1.  Identify the stuck target IQN/Name using `scripts/synology/list_targets.py` or from error logs.
    2.  Run the force delete script for that target:
        ```bash
        export SYNOLOGY_USER="your-user"
        export SYNOLOGY_PASSWORD="your-password"
        # Example using the ID found in your error logs:
        python3 scripts/synology/force_delete_target.py --target-name "pvc-0dc9b880-f873-4865-b258-d23f23593867"
        ```
    3.  Run the rebind script again to recreate the target and map it correctly:
        ```bash
        python3 scripts/synology/rebind_luns.py
        ```
    4.  Repeat for other stuck targets if necessary.

### 3. Application Checks (Post-Storage Fix)
- Once storage is back:
    - Restart `homeassistant` and `jellyfin` pods.
    - Check log output for successful mount.
