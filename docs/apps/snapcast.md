# Snapcast (Snapserver)

## 1. Overview
Snapcast is a multi-room client-server audio player, where all clients are time-synchronized with the server to play perfectly synced audio. In this homelab, it serves as the backbone for multi-room audio, allowing various clients (Raspberry Pis, PCs, mobile devices) to play synchronized music.

## 2. Architecture
Snapcast is deployed as a Kubernetes `Deployment` with a single replica in the `snapcast-prod` (and `snapcast-stage`) namespace.
- **Containers**:
  - `snapserver`: The main Snapcast server that reads audio streams and serves them to clients.
  - `go-librespot` (Sidecar): An open-source Spotify client that acts as a Spotify Connect receiver. It outputs raw PCM audio to a shared named pipe (FIFO) that `snapserver` reads.
- **Storage**:
  - **Spotify State**: Uses a PersistentVolumeClaim (`snapcast-spotify-state`) backed by the `synology-iscsi` storage class to store Spotify credentials and pairing state.
  - **Shared Audio**: Uses an `emptyDir` volume (`shared-audio`) to share the named pipe (`/audio/spotify.fifo`) between the `go-librespot` sidecar and `snapserver`.
- **Networking**: 
  - The `snapcast` Service is a `LoadBalancer` (via Cilium IPAM) exposing:
    - `1704/TCP`: Snapcast audio stream
    - `1705/TCP`: Snapcast control / RPC
    - `1780/TCP`: Snapweb (web UI)
  - Exposed via Cilium Gateway API (`HTTPRoute`) for the web UI.

## 3. URLs
- **Staging**: https://snapcast.stage.burntbytes.com
- **Production**: https://snapcast.burntbytes.com

## 4. Configuration
- **Environment Variables**: N/A
- **ConfigMaps/Secrets**:
  - `snapcast-config` (ConfigMap): Contains the `snapserver.conf` file, defining the audio streams (e.g., `pipe:///audio/spotify.fifo?name=Spotify&sampleformat=44100:16:2`).
- **Spotify Connect**: The `go-librespot` sidecar is configured via command-line arguments in the deployment to output to the shared FIFO.

## 5. Usage Instructions
- **Web UI (Snapweb)**: Navigate to the URL to control client volume, group clients, and select the active audio stream for each group.
- **Clients**: Run `snapclient` on your devices (e.g., Raspberry Pi) pointing to the `snapserver` LoadBalancer IP:
  ```bash
  snapclient -h <snapserver-lb-ip>
  ```
- **Spotify Connect**: Open the Spotify app on your phone or computer, select "Devices Available", and choose "Snapcast". Playback will be routed through the Snapserver to all connected clients.

## 6. Testing
To verify Snapcast is working:
1. Navigate to the Snapweb UI and ensure it loads.
2. Connect a `snapclient` and verify it appears in the Snapweb UI.
3. Play music via Spotify Connect to the "Snapcast" device and verify audio plays on the connected client.
4. Verify the pod is running: `kubectl get pods -n snapcast-prod`

### Quick Test (Noise)
To test audio output without Spotify, you can pipe random noise into a test FIFO:
```bash
kubectl -n snapcast-prod exec deploy/snapcast -c snapserver -- sh -c 'cat /dev/urandom > /tmp/snapfifo'
```

## 7. Monitoring & Alerting
- **Metrics**: Snapcast does not expose Prometheus metrics natively.
- **Logs**: Check the pod logs for server errors or Spotify Connect issues:
  ```bash
  kubectl logs -n snapcast-prod deploy/snapcast -c snapserver
  kubectl logs -n snapcast-prod deploy/snapcast -c go-librespot
  ```

## 8. Disaster Recovery
- **Backup Strategy**:
  - **Spotify State**: The `snapcast-spotify-state` PVC contains the Spotify pairing credentials. This is backed up via Synology Snapshot Replication.
  - **Config**: The `snapserver.conf` is stored in Git.
- **Restore Procedure**:
  1. Restore the `snapcast-spotify-state` LUN via Synology DSM if necessary.
  2. Re-deploy the Snapcast manifests. If the Spotify state is lost, you will simply need to re-pair the device in the Spotify app.

## 9. Troubleshooting
- **Spotify Connect Device Not Showing Up**: 
  - Verify the `go-librespot` sidecar is running and hasn't crashed.
  - Check the `go-librespot` logs for authentication or mDNS discovery errors. Note that mDNS discovery across VLANs/subnets may require an mDNS repeater (e.g., Avahi) on your router.
- **No Audio on Clients**: 
  - Verify the client is connected to the correct stream in the Snapweb UI.
  - Check the `snapserver` logs for errors reading from the FIFO (`/audio/spotify.fifo`).
  - Ensure the client is not muted in Snapweb.
- **Audio Sync Issues**: 
  - Ensure all clients and the server have accurate time synchronization (NTP).
  - Adjust the latency offset for specific clients in the Snapweb UI if necessary.
