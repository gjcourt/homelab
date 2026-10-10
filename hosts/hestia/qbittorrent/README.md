# qbittorrent

qBittorrent P2P client running as a TrueNAS Custom App on hestia. Used for private-tracker downloads only.

| Attribute | Value |
|-----------|-------|
| Image | `lscr.io/linuxserver/qbittorrent` (digest-pinned in compose) |
| Web UI | `http://10.42.2.10:8080` (LAN-only) |
| Torrenting port | `6881/tcp` + `6881/udp` |
| Config dataset | `/mnt/main/apps/qbittorrent/config` |
| Downloads (`/downloads` in the container) | `/mnt/main/family/media/video/movies` — the Jellyfin movies library itself (see below) |
| Runs as | `truenas_admin` (uid/gid 950) |
| Network | host bridge, no VPN |

## One-time bootstrap

1. **Pre-create the persistence datasets on hestia** (one-time):
   ```bash
   ssh truenas_admin@10.42.2.10
   sudo zfs list main/apps 2>/dev/null || sudo zfs create main/apps
   sudo zfs create main/apps/qbittorrent
   sudo mkdir -p /mnt/main/apps/qbittorrent/config
   sudo chown -R 950:950 /mnt/main/apps/qbittorrent
   ```
   The downloads path is the existing movies library, not a separate dataset (see below).

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
  - Default save path: `/downloads` — this is the movies library itself (see below), so a
    `complete/` or `incomplete/` subfolder here would appear inside the library
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

## Downloads land in the library, which is also the seeding set

`/downloads` is bind-mounted to `/mnt/main/family/media/video/movies`, so completed torrents land
directly in the Jellyfin movies library and seed from there. This layout matches the old alcatraz
qBittorrent, which let the migrated `.fastresume` files resolve without path rewrites. (An earlier layout used a separate `main/downloads` dataset; it no longer exists on hestia.)

Consequences:

- **Don't rename, dedup or transcode-and-delete files in `video/movies` on the filesystem** — it
  breaks the torrents (missing-files, seeding stops). Use qBittorrent's own Rename, which updates
  the torrent's file map. Jellyfin matches scene names without it.
- **"Delete torrent and files" reaches into the library.** Don't use that right-click option
  casually. For a staging-then-move workflow, give a category its own save path.
- **The directory must be writable by uid 950.** Files copied in as `root:root 755` seed fine
  (read-only) but any partial torrent fails with `file_open ... Permission denied`. Fix with
  `sudo chown -R truenas_admin:truenas_admin` on the affected path.

## Migrating qBittorrent state between hosts

Stop both instances so their config is flushed, rsync the whole `config/qBittorrent/` directory
across, `chown -R 950:950` it, make sure the downloads path exists at the same in-container path and
is writable by 950, then start the new instance. Torrents are rediscovered from `BT_backup/`; Force
Recheck one before Resume All.

qBittorrent 5.x Web API: `/torrents/pause` and `/torrents/resume` return 404 — use
`/torrents/stop` and `/torrents/start`. `/torrents/recheck` is unchanged.
