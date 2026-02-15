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
    - However, multiple apps (`homeassistant`, `jellyfin`) still fail to mount volumes.
    - This indicates that the LUNs are likely **unmapped** or the Targets are in a "zombie" state where the API sees them but they aren't working.
    - **Action**: Run `scripts/synology_rebind_luns.py` to identify and fix unmapped/broken targets.

## Execution Plan & Progress

### 1. Fix Private Registry Authentication
- **Status**: **User Action Required**
- **Task**: The GitHub PAT in `apps/base/golinks/secret-ghcr.yaml` (and biometrics) is invalid.
- **Instruction**:
    1.  Generate a new PAT (Classic) with `read:packages` scope.
    2.  Update `apps/base/golinks/secret-ghcr.yaml` and `apps/base/biometrics/secret-ghcr.yaml` with the new token.
    3.  Re-encrypt with SOPS.
    4.  Commit and push.

### 2. Recover iSCSI Storage
- **Status**: **User Action Required**
- **Task**: Rebind orphaned LUNs on Synology.
- **Instruction**: Run the rebind script which uses the Synology Web API to map LUNs to Targets.
    ```bash
    export SYNOLOGY_USER="your-user"
    export SYNOLOGY_PASSWORD="your-password"
    # First, dry run to see what will happen
    python3 scripts/synology_rebind_luns.py --dry-run
    # If it proposes fixes (Mapping or Recreating), run without dry-run
    # python3 scripts/synology_rebind_luns.py
    ```

### 3. Application Checks (Post-Storage Fix)
- Once storage is back:
    - Restart `homeassistant` and `jellyfin` pods.
    - Check log output for successful mount.
