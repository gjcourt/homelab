# Jellyfin

## 1. Overview
Jellyfin is a Free Software Media System that puts you in control of managing and streaming your media. In this homelab, it serves as the primary media server for movies, TV shows, and anime, featuring hardware-accelerated video transcoding.

## 2. Architecture
Jellyfin is deployed as a standard Kubernetes `Deployment` with a single replica in the `jellyfin-prod` (and `jellyfin-stage`) namespace.
- **Storage**:
  - **Config & Cache**: Uses PersistentVolumeClaims (`jellyfin-config-pvc`, `jellyfin-cache-pvc`) backed by the `synology-iscsi` storage class for fast metadata and cache access.
  - **Media**: Uses NFS PersistentVolumes (`jellyfin-movies-pv`, `jellyfin-tvshows-pv`, `jellyfin-tvanime-pv`) to mount the media libraries directly from the Synology NAS. These are mounted as read-only to prevent accidental deletion.
- **Hardware Acceleration**: The pod mounts `/dev/dri` from the host node and runs in privileged mode to utilize the AMD GPU for hardware-accelerated video transcoding (VAAPI/AMF).
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://jellyfin.stage.burntbytes.com
- **Production**: https://jellyfin.burntbytes.com

## 4. Configuration
- **Environment Variables**:
  - `JELLYFIN_FFmpeg__probesize` and `JELLYFIN_FFmpeg__analyzeduration` are tuned for better playback compatibility.
  - Additional variables are loaded from the `jellyfin-container-env` ConfigMap.
- **ConfigMaps/Secrets**:
  - `jellyfin-container-env`: Contains basic environment variables.

## 5. Usage Instructions
- **Web UI**: Navigate to the URL and log in with your Jellyfin credentials.
- **Clients**: Use official Jellyfin apps on smart TVs, mobile devices, or desktop clients. Enter the server URL and authenticate.

## 6. Testing
To verify Jellyfin is working:
1. Navigate to the web UI and ensure the media libraries load.
2. Play a video file and verify it streams correctly.
3. To test hardware transcoding, play a high-bitrate video (e.g., 4K HEVC) and lower the playback quality in the player settings. Check the Jellyfin dashboard to confirm it is transcoding using VAAPI/AMF.
4. Verify the pod is running: `kubectl get pods -n jellyfin-prod`

## 7. Monitoring & Alerting
- **Metrics**: Jellyfin does not expose Prometheus metrics natively by default.
- **Logs**: Check the pod logs for FFmpeg transcoding errors or library scan issues:
  ```bash
  kubectl logs -n jellyfin-prod deploy/jellyfin
  ```

## 8. Disaster Recovery
- **Backup Strategy**:
  - **Media**: The NFS shares (`/volume1/media`) are backed up natively on the Synology NAS.
  - **Config**: The `jellyfin-config-pvc` contains the SQLite database, user data, and metadata. This is backed up via Synology Snapshot Replication.
  - **Cache**: The `jellyfin-cache-pvc` is ephemeral and does not need to be backed up.
- **Restore Procedure**:
  1. Restore the `jellyfin-config` LUN via Synology DSM if necessary.
  2. Ensure the NFS media shares are intact.
  3. Re-deploy the Jellyfin manifests.

## 9. Troubleshooting
- **Hardware Transcoding Failing**:
  - Verify `/dev/dri` is mounted correctly and the pod has the necessary privileges.
  - Check the Jellyfin Dashboard -> Playback settings to ensure hardware acceleration (VAAPI or AMF) is enabled and the correct codecs are selected.
  - Check the FFmpeg logs in the Jellyfin dashboard for specific codec errors.
- **Media Not Showing Up**:
  - Verify the NFS volumes are mounted correctly: `kubectl describe pod -n jellyfin-prod -l app=jellyfin`
  - Ensure the Synology NAS NFS permissions allow the Kubernetes nodes to read the media directories.
  - Trigger a manual library scan in the Jellyfin dashboard.
