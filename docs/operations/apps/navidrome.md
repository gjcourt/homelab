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
**Browser: Authelia single sign-on.** `navidrome.`, `music.` and `navidrome.stage.` sit behind the
gateway's Authelia `ExternalAuth` filter. One Authelia login goes straight into Navidrome: the
gateway sets `Remote-User` and Navidrome trusts it (reverse-proxy auth). See
[Gateway authentication](../../architecture/gateway-auth.md).

**Subsonic clients and Music Assistant** use their own Navidrome credentials on `/rest`, which is
not gated. Snapcast's mopidy talks to the in-cluster Service and never goes through the gateway.

What makes reverse-proxy auth safe (all three must hold; `apps/{production,staging}/navidrome/`):
- `ND_EXTAUTH_TRUSTEDSOURCES` is the four per-node Cilium ingress IPs, never the private ranges.
  Navidrome checks the direct peer, so a client can't spoof it. A rebuilt node can get a new
  ingress IP (`kubectl get ciliumnode -o custom-columns=N:.metadata.name,I:.spec.ingress`); SSO
  then fails closed to Navidrome's own login until the list is updated.
- The gated rule sets `Remote-User` from Authelia, replacing any client-sent value.
- The ungated `/rest` and `/auth` rules remove a client-sent `Remote-User`. Navidrome's Subsonic
  API also honours the header (v0.61.2 `server/subsonic/middlewares.go:61`).

**`/auth` also drops `Cookie`.** On a failed login Navidrome logs every request header
(`server/auth.go:56`), which would put the Authelia session cookie in the pod log. That cookie
is valid on every gated `*.burntbytes.com` host. The login endpoints read credentials from the
body only, so they don't need it.

**An Authelia user with no Navidrome account is created on first visit.** Only Authelia's own
users can reach the gated rule.

**History (2026-11-03):** reverse-proxy auth was turned off because the old gateway filter never
ran (Cilium 1.19) and the header was trusted from every private range: any LAN client could send
`Remote-User: <name>` and be logged in as that user, and an unknown name was auto-created (on
stage, as the first user, an admin). Staging re-enabled it with the conditions above once
Cilium 1.20's `ExternalAuth` worked (#1828), and the forged-header checks passed there before
prod got it.
