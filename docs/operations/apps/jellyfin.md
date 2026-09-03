# Jellyfin

## 1. Overview
Jellyfin is a Free Software Media System that puts you in control of managing and streaming your media. In this homelab, it serves as the primary media server for movies, TV shows, and anime, featuring hardware-accelerated video transcoding.

## 2. Architecture
Jellyfin is deployed as a standard Kubernetes `Deployment` with a single replica in the `jellyfin-prod` (and `jellyfin-stage`) namespace.
- **Storage**:
  - **Config & Cache**: Uses PersistentVolumeClaims (`jellyfin-config-pvc`, `jellyfin-cache-pvc`) backed by the `synology-iscsi` storage class for fast metadata and cache access.
  - **Media**: Uses NFS PersistentVolumes (`jellyfin-movies-pv`, `jellyfin-tvshows-pv`, `jellyfin-tvanime-pv`) to mount the media libraries directly from the Synology NAS. These are mounted as read-only to prevent accidental deletion.
- **Hardware Acceleration**: The pod mounts `/dev/dri` from the host node (hostPath) and runs `privileged: true`, so the AMD GPU is reachable. ⚠️ **The device being plumbed through does not mean Jellyfin is using it.** Whether hardware acceleration is actually *enabled* is a Jellyfin setting in `encoding.xml` on `jellyfin-config-pvc` — **not in Git, not managed by Flux, and silently lost if that PVC is restored or recreated.** **It has silently reverted at least once.** Hardware acceleration was enabled historically, and on 2026-09-02 it was found set to `none` with the device still correctly exposed — so every transcode had been running in software, for an unknown period, while this doc still described it as accelerated. The cause of the reversion could not be determined: `encoding.xml` carries only its last-write mtime, so re-enabling it destroyed the evidence. A config-PVC restore or an upgrade config migration are both plausible. **Treat this as a setting that can disappear without notice, and verify it after any Jellyfin upgrade or config-PVC operation** — see §9.
- **Transcode CPU ceiling**: `limits.cpu` is `8000m` (raised from `4000m`, 2026-09-02) on 12-core nodes. Even with VAAPI active, subtitle overlay, scaling and format conversion run on CPU, so the cgroup cap bounds transcode start-up latency. `requests` stay at `100m` — a burst ceiling, not a reservation.
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
- **Hardware Transcoding Failing** (or: playback slow to start):
  - **Check the setting before the device.** The device is nearly always fine; the setting is what silently is not, and it lives on the PVC rather than in Git:
    ```bash
    POD=$(kubectl -n jellyfin-prod get pods -o name | head -1); POD=${POD#pod/}
    kubectl -n jellyfin-prod exec $POD -c jellyfin -- \
      grep -E "HardwareAccelerationType|EnableHardwareEncoding" /config/config/encoding.xml
    ```
    `none` means every transcode is running in software.
  - ⚠️ **`vaapi` alone is not enough — check the decode list is populated.** Setting the acceleration type and `EnableHardwareEncoding` while leaving `<HardwareDecodingCodecs>` empty yields hardware *encode* with software *decode*, which leaves most of the cost in place for HEVC sources. This exact half-configured state was hit on 2026-09-02:
    ```xml
    <HardwareAccelerationType>vaapi</HardwareAccelerationType>
    <EnableHardwareEncoding>true</EnableHardwareEncoding>
    <HardwareDecodingCodecs></HardwareDecodingCodecs>   <!-- empty: software decode -->
    ```
  - **Confirm what the GPU can actually do** before enabling codecs — ticking an unsupported format just makes Jellyfin try hardware, fail, and fall back to software:
    ```bash
    kubectl -n jellyfin-prod exec $POD -c jellyfin -- \
      /usr/lib/jellyfin-ffmpeg/vainfo --display drm --device /dev/dri/renderD128
    ```
    Measured 2026-09-02, AMD Radeon (renoir, radeonsi): **decode** H264, HEVC Main, HEVC Main10, MPEG2, VC1, VP9 Profile0/2 · **encode** H264 Main+High, HEVC Main+Main10 · **no VP8, no AV1 either direction.** The two "Intel Low-Power" encoder options and "VPP Tone mapping" are Intel-only — leave off.
  - **Confirm whether it is transcoding at all, and in software or hardware:**
    ```bash
    kubectl -n jellyfin-prod exec $POD -c jellyfin -- \
      sh -c 'grep -ohE "\-preset [a-z]+|h264_vaapi|hwaccel [a-z]+" /config/log/*.log | sort | uniq -c'
    ```
    `-preset veryfast` = **software**. `h264_vaapi` = hardware.
  - ⚠️ **PGS (bitmap) subtitles force a full re-encode.** PGS cannot be passed to a client, so Jellyfin burns it into the picture — visible in the ffmpeg filter graph as an `overlay` of the subtitle stream onto the video. This happens *regardless* of whether the client could direct-play the video. **"Allow subtitle extraction on the fly" does not help** — PGS is images, not text, so there is nothing to extract. Turning subtitles off makes such files direct-play instantly; the durable fix is OCRing PGS to SRT so it can be delivered as a text track.
  - Verify `/dev/dri` is mounted and the pod has privileges — rarely the problem, since this part *is* in Git.
  - Check the FFmpeg logs in the Jellyfin dashboard for specific codec errors.
- **Media Not Showing Up**:
  - Verify the NFS volumes are mounted correctly: `kubectl describe pod -n jellyfin-prod -l app=jellyfin`
  - Ensure the Synology NAS NFS permissions allow the Kubernetes nodes to read the media directories.
  - Trigger a manual library scan in the Jellyfin dashboard.
