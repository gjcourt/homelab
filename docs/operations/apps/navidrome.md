# Navidrome

## 1. Overview
Navidrome is an open-source, web-based music collection server and streamer. It gives you the freedom to listen to your music collection from any browser or mobile device. It is compatible with the Subsonic API, allowing you to use a wide variety of client applications.

## 2. Architecture
Navidrome is deployed as a standard Kubernetes `Deployment` with a single replica in the `navidrome-prod` (and `navidrome-stage`) namespace.
- **Storage**:
  - **Data**: Uses a PersistentVolumeClaim (`navidrome-data-pvc`) backed by the `synology-iscsi` storage class to store its SQLite database, cache, and configuration.
  - **Music**: Uses an NFS PersistentVolume (`navidrome-music-pvc`) to mount the music library directly from the Synology NAS (`/volume1/family/audio/music`).
- **Security Context**: The pod runs as UID `1028` to match the owner of the music directory on the Synology NAS, ensuring read access to the NFS share.
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://navidrome.stage.burntbytes.com
- **Production**: https://navidrome.burntbytes.com. `music.burntbytes.com` also routes here until
  Music Assistant takes that name (MA plan step 2).
- Every hostname that routes to Navidrome gets an Authelia `one_factor` rule (see
  Authentication below).

## 4. Configuration
- **Environment Variables**:
  - `ND_DATAFOLDER`: `/data`
  - `ND_MUSICFOLDER`: `/music`
  - `ND_LOGLEVEL`: `info`
  - `ND_SCANSCHEDULE`: `1h` (Scans for new music every hour)
  - `ND_SESSIONTIMEOUT`: `24h`
- **ConfigMaps/Secrets**:
  - `navidrome-lastfm` (Secret): Contains Last.fm API credentials (`ND_LASTFM_APIKEY`, `ND_LASTFM_SECRET`) used for fetching album art, artist biographies, and enabling user scrobbling. Managed via SOPS.

## 5. Usage Instructions
- **Web UI**: Navigate to the URL and log in. The first user created becomes the administrator.
- **Subsonic Clients**: You can use any Subsonic-compatible client (e.g., Symfonium on Android, play:Sub on iOS, Sonixd on Desktop). Point the client to the Navidrome URL and use your credentials.

## 6. Testing
To verify Navidrome is working:
1. Navigate to the web UI and ensure the music library loads.
2. Play a song and verify it streams correctly.
3. Verify the pod is running: `kubectl get pods -n navidrome-prod`

## 7. Monitoring & Alerting
- **Metrics**: Navidrome does not expose Prometheus metrics natively.
- **Logs**: Check the pod logs for library scan errors or Last.fm API issues:
  ```bash
  kubectl logs -n navidrome-prod deploy/navidrome
  ```

## 8. Disaster Recovery
- **Backup Strategy**:
  - **Music**: The NFS share (`/volume1/family/audio/music`) is backed up natively on the Synology NAS.
  - **Data**: The `navidrome-data-pvc` contains the SQLite database (user accounts, playlists, play counts). This is backed up via Synology Snapshot Replication.
- **Restore Procedure**:
  1. Restore the `navidrome-data` LUN via Synology DSM if necessary.
  2. Ensure the NFS music share is intact.
  3. Re-deploy the Navidrome manifests.

## 9. Troubleshooting
- **Music Not Showing Up**:
  - Verify the NFS volume is mounted correctly and the pod has read permissions (UID 1028).
  - Trigger a manual "Quick Scan" or "Full Scan" from the Navidrome web UI (Activity icon -> Quick Scan).
- **Last.fm Integration Failing**:
  - Verify the `navidrome-lastfm` secret contains valid API keys.
  - Check the pod logs for API rate limiting or authentication errors.

## 10. Authentication
Navidrome uses **its own login** (username and password). Subsonic clients and Music
Assistant authenticate with Navidrome credentials too.

**Reverse-proxy (`Remote-User`) auth is disabled on purpose (2026-11-03).**
- **The flaw:** the gateway's Authelia `ext_authz` filter isn't injected on this Cilium version
  (see `infra/configs/gateway/cilium-envoy-config-production.yaml` and
  [Gateway Authentication Architecture](../../architecture/gateway-auth.md)), so nothing
  removes a `Remote-User` header the client sends. With `ND_EXTAUTH_TRUSTEDSOURCES` set to
  every private range, any LAN client could send `Remote-User: <name>` and be logged in as
  that user.
- **Worse:** an unknown name was auto-created as an admin. This was verified on stage.
- **What to keep unset:** `ND_EXTAUTH_TRUSTEDSOURCES` and `ND_EXTAUTH_USERHEADER`. Re-enable
  them only once the gateway both strips a client-sent header and sets it from Authelia,
  and a forged-header request is shown to fail.

The Authelia `one_factor` rules for the Navidrome hostnames
(`apps/{production,staging}/authelia/configuration.yaml`) stay in place for when the
filter works.
