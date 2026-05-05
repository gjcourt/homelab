# Snapcast (Snapserver)

## 1. Overview
Snapcast is a multi-room client-server audio player, where all clients are time-synchronized with the server to play perfectly synced audio. In this homelab, it serves as the backbone for multi-room audio, allowing various clients (Raspberry Pis, PCs, mobile devices) to play synchronized music.

## 2. Architecture
Snapcast is deployed as a Kubernetes `Deployment` with a single replica in the `snapcast-prod` (and `snapcast-stage`) namespace.
- **Containers**:
  - `snapserver`: The main Snapcast server that reads audio streams and serves them to clients.
  - `go-librespot` (Sidecar): An open-source Spotify client that acts as a Spotify Connect receiver. It outputs raw PCM audio to a shared named pipe (FIFO) that `snapserver` reads.
- **Storage**:
  - **Spotify State / Server State**: Uses a PersistentVolumeClaim (`snapcast-spotify-state`) backed by the `synology-iscsi` storage class. Both containers share this PVC:
    - `go-librespot` mounts it at `/config` — stores `state.json` (Spotify OAuth credentials and device ID).
    - `snapserver` mounts it at `/var/lib/snapserver` (`XDG_CONFIG_HOME`) — stores `snapserver/server.json` (client MAC addresses and their group/stream assignments). This persistence means kitchen and living-room stay on the `spotify` stream across pod restarts.
  - **Shared Audio**: Uses an `emptyDir` volume to share the named pipe (`/audio/spotify.fifo`) between the `go-librespot` sidecar and `snapserver`.
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

## 8. Spotify Connect: Initial Auth and Re-auth

go-librespot uses interactive OAuth (Spotify's PKCE flow). The one-time auth must be performed manually; credentials persist in the PVC afterwards and survive pod restarts.

### Initial authentication (or after PVC loss)

1. Port-forward the OAuth callback port from your Mac to the pod:
   ```bash
   kubectl port-forward -n snapcast-prod deploy/snapcast 57622:57622 &
   ```

2. Capture the auth URL from the go-librespot log:
   ```bash
   kubectl logs -n snapcast-prod deploy/snapcast -c go-librespot | grep "accounts.spotify.com"
   ```

3. Open the URL in your browser. Spotify will redirect to `http://127.0.0.1:57622/login?code=...` — the port-forward catches this callback.

4. Verify success:
   ```bash
   kubectl logs -n snapcast-prod deploy/snapcast -c go-librespot | grep "authenticated"
   kubectl exec -n snapcast-prod deploy/snapcast -c go-librespot -- cat /config/state.json
   ```
   `state.json` should have a non-empty `credentials.username` and `credentials.data`.

5. Kill the port-forward once done: `pkill -f "port-forward.*57622"`

**Notes:**
- The URL expires if the pod restarts. If the pod crashes during auth (it can happen if the callback arrives malformed), delete the pod to get a fresh URL: `kubectl delete pod -n snapcast-prod -l app=snapcast`
- `zeroconf_enabled: false` in the go-librespot config means the device registers via Spotify cloud, not mDNS. "Snapcast" appears in the Spotify "Devices Available" list without needing mDNS propagation.

## 9. Disaster Recovery
- **Backup Strategy**:
  - **Spotify State + Server State**: The `snapcast-spotify-state` PVC stores both go-librespot credentials (`state.json`) and snapserver's client/stream assignments (`snapserver/server.json`). Backed up via Synology Snapshot Replication.
  - **Config**: `snapserver.conf` and `go-librespot` config are in Git (ConfigMaps).
- **Restore Procedure**:
  1. Restore the `snapcast-spotify-state` LUN via Synology DSM if necessary.
  2. Re-deploy the Snapcast manifests. If the Spotify credentials are lost, redo the auth flow in Section 8. Client stream assignments will also be lost — clients reconnect to the `default` stream; use Snapweb or the JSON-RPC one-liner below to move them back to `spotify`:
     ```bash
     # Move a group to the spotify stream (get group IDs from Server.GetStatus first)
     curl -s http://10.42.2.37:1780/jsonrpc -H "Content-Type: application/json" \
       -d '{"id":1,"jsonrpc":"2.0","method":"Group.SetStream","params":{"id":"<group-id>","stream_id":"spotify"}}'
     ```

## 10. Troubleshooting

### Spotify Connect device "Snapcast" not showing up
- Verify go-librespot is running and not crash-looping: `kubectl get pods -n snapcast-prod`
- Check go-librespot logs for auth errors. If you see `"to complete authentication visit the following link"` on every startup, credentials were lost — redo the auth flow in Section 8.
- `zeroconf_enabled: false` means the device uses cloud registration, not mDNS. It should appear in Spotify's device list on any device logged into the same account without needing mDNS.

### No audio on clients (spotify stream is idle)
- Check which stream clients are assigned to:
  ```bash
  curl -s http://10.42.2.37:1780/jsonrpc -H "Content-Type: application/json" \
    -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' | python3 -m json.tool
  ```
  Clients should have `"stream_id": "spotify"`. If they show `"stream_id": "default"`, the server state was lost (PVC issue or first boot after PVC wipe). Use Snapweb or the `Group.SetStream` RPC call from Section 9 to reassign them.
- Verify the spotify stream status is `"playing"` (not `"idle"`) in the same output. If it's idle, go-librespot is not writing audio — check its logs.

### Playback keeps transferring away from "Snapcast" to "Kitchen" or "Living Room"
The HifiBerry OS devices (kitchen/living-room) ship with a native Spotify Connect implementation (Vollibrespot / HifiBerry's built-in service) that registers separate Spotify Connect devices named after the hostname. These compete with "Snapcast" for the active playback session.

When this happens, go-librespot logs: `"playback was transferred to Kitchen"`. Audio on the snapcast stream stops because go-librespot stops writing to the FIFO.

**Workaround**: Disable native Spotify on the HifiBerry devices. SSH in and check for a Vollibrespot or HifiBerry Spotify service:
```bash
ssh root@10.42.2.38 "systemctl list-units | grep -i spotify"
ssh root@10.42.2.38 "ps aux | grep -i librespot"
```
Stop/disable any conflicting service and verify only "Snapcast" appears in Spotify's device list.

If the native Spotify service cannot be cleanly disabled, as a workaround you can unregister it by deleting its credentials file (location varies by HifiBerry OS version — look in `/data/` or `/etc/`).

### No audio despite spotify stream showing "playing"
- Check client volumes — they default to ~28-30% after first connection. Use Snapweb to raise them.
- Verify the client is not muted in Snapweb.
- Check snapserver logs for FIFO read errors: `kubectl logs -n snapcast-prod deploy/snapcast -c snapserver`

### Audio sync issues
- Ensure all clients and the server have accurate NTP time synchronization.
- Adjust the latency offset for specific clients in the Snapweb UI if necessary.

## 11. HifiBerry Clients (kitchen / living-room)

Two HifiBerry OS devices run snapclient as a Docker extension:
- `kitchen` — `10.42.2.38`
- `living-room` — `10.42.2.39`

### Image

The upstream HifiBerry extension image (`ghcr.io/hifiberry/extension_snapcast:0.28.0`) has two bugs that prevent snapclient from running. A patched image is maintained at `ghcr.io/gjcourt/snapcast-hifiberry` with a build pipeline in `images/snapcast-hifiberry/`. See `images/snapcast-hifiberry/README.md` for full details on the bugs and upgrade procedure.

**Known upstream bugs fixed by the patch image:**
1. Runtime audio libs missing from the final build stage — `libasound`, `libvorbis`, `libogg`, `libFLAC`, `libopus`, `libsoxr` are built in the compile stage but not installed in the runtime image.
2. Wrong binary path — `snapcastmpris.py` hardcodes `/bin/snapclient` but the binary lands at `/usr/local/bin/snapclient`. The patch adds a symlink.

### Device setup

Each device has:
- `/data/extensions/snapcast/docker-compose.yaml` — extension config; references `ghcr.io/gjcourt/snapcast-hifiberry:<tag>`
- `/etc/snapcastmpris.conf` — INI file (no section header) with `server = 10.42.2.37` (the production LB VIP)

To check status:
```bash
ssh root@10.42.2.38 "docker exec snapcast ps aux"
# Should show: /usr/bin/python3 snapcastmpris.py AND /bin/snapclient -e -h 10.42.2.37
```

To pull and deploy a new image on both devices:
```bash
for ip in 10.42.2.38 10.42.2.39; do
  ssh root@$ip "
    docker pull ghcr.io/gjcourt/snapcast-hifiberry:<tag>
    sed -i 's|image: ghcr.io/gjcourt/snapcast-hifiberry:.*|image: ghcr.io/gjcourt/snapcast-hifiberry:<tag>|' /data/extensions/snapcast/docker-compose.yaml
    docker-compose -f /data/extensions/snapcast/docker-compose.yaml up -d
  "
done
```

### Network policy note

The snapcast CNP uses `fromEntities: world` (not `fromCIDR: 10.42.2.0/24`) for ports 1704/1705/1780. This is intentional: Cilium SNATs LB traffic to a node IP before it reaches the pod, so a CIDR rule for the LAN subnet never matches. The security boundary is the LAN VLAN — `10.42.2.37` is unreachable from outside VLAN 2.
