# qbittorrent

qBittorrent P2P client running as a TrueNAS Custom App on hestia. Used for private-tracker downloads only.

| Attribute | Value |
|-----------|-------|
| Image | `lscr.io/linuxserver/qbittorrent` (digest-pinned in compose) |
| Web UI | `http://10.42.2.10:8080` (LAN-only) |
| Torrenting port | `6881/tcp` + `6881/udp` |
| Config dataset | `/mnt/main/apps/qbittorrent/config` |
| Downloads dataset | `/mnt/main/downloads` (subdirs: `incomplete/`, `complete/`) |
| Network | host bridge, no VPN |

## One-time bootstrap

1. **Pre-create the persistence datasets on hestia** (one-time):
   ```bash
   ssh truenas_admin@10.42.2.10
   sudo zfs list main/apps 2>/dev/null || sudo zfs create main/apps
   sudo zfs create main/apps/qbittorrent
   sudo mkdir -p /mnt/main/apps/qbittorrent/config
   sudo zfs create main/downloads
   sudo mkdir -p /mnt/main/downloads/{incomplete,complete}
   sudo chown -R 950:950 /mnt/main/apps/qbittorrent /mnt/main/downloads
   ```

2. **Router port forward on UCGF** (one-time): forward inbound `WAN tcp/udp 6881` → `10.42.2.10:6881`. Without this, qBittorrent runs in passive mode (no inbound connections, no seeding, degraded download speeds).

3. **Create the Custom App in SCALE UI** (one-time — auto-deploy can't do the initial create):
   - Apps → Discover Apps → Custom App
   - Name: `qbittorrent` (must match the matrix entry in `.github/workflows/deploy-hestia.yml`)
   - Paste the contents of `docker-compose.yml` from this directory
   - Install
   - Wait for it to reach Running

4. **Find the generated admin password**:
   ```bash
   ssh truenas_admin@10.42.2.10 'sudo docker logs qbittorrent 2>&1 | grep -E "temporary password|WebUI"' | head -10
   ```
   The linuxserver image generates a random admin password on first boot (5.0+). Log into the Web UI, change to a permanent password via Tools → Options → Web UI.

## Recommended qBittorrent settings (set via Web UI on first login)

- **Downloads**:
  - Default save path: `/downloads/complete`
  - Keep incomplete torrents in: `/downloads/incomplete` (toggle on, set path)
  - Append `.!qB` to incomplete files: on
- **Connection**:
  - Port used for incoming connections: `6881`
  - Use UPnP / NAT-PMP port forwarding: **off** (you're using a static router forward)
- **Bittorrent**:
  - Enable DHT: off (private trackers)
  - Enable PeX: off (private trackers)
  - Enable Local Peer Discovery: off
  - Enable anonymous mode: off (private trackers usually require non-anonymous)
- **Web UI**:
  - Set a strong admin password
  - Enable HTTPS: optional; LAN-only by default

## Subsequent updates

Once the Custom App is bootstrapped, every change to `docker-compose.yml` on `master` triggers `.github/workflows/deploy-hestia.yml`, which calls `scripts/truenas-update-app.sh qbittorrent ...` on the self-hosted runner and applies the new compose via the TrueNAS WebSocket API.

To roll back: revert the offending commit and merge; auto-deploy applies the old compose. Or manually `midclt call app.update qbittorrent '{"custom_compose_config": "<old yaml>"}'` from a hestia shell.

## Moving completed downloads to the Jellyfin library

This stack intentionally does NOT mount `/mnt/main/media/` into qBittorrent. After a torrent finishes, manually move the contents:

```bash
# On hestia
sudo mv /mnt/main/downloads/complete/'Some.Movie.2026.mkv' /mnt/main/media/movies/
```

Then Dashboard → Scheduled Tasks → "Scan Media Library" in Jellyfin (or wait for the automatic interval) to pick it up.
