# immich-photos-backup

Daily incremental backup of the Immich photo library from alcatraz (Synology, 10.42.2.11) into the local ZFS dataset `main/family/images/photos`. Always-running container; in-container busybox `crond` fires the rsync at 04:00 local time.

Sources are per-user `homes/<user>/Photos/` paths, not `family/images/photos/<user>/`. On the Synology side those family paths are symlinks into each user's home, with per-file ACLs that deny `truenas-backup` file open via the family path — so we sync from the homes path directly and re-map into the canonical hestia `family/images/photos/<user>/` layout.

| Attribute | Value |
|---|---|
| Image | `ghcr.io/gjcourt/immich-photos-backup` (built from `images/immich-photos-backup/`) |
| Schedule | `0 4 * * *` (after CNPG S3 backups at 02:00) |
| Sources | `truenas-backup@10.42.2.11:/volume1/homes/{george,mara}/Photos/` |
| Destinations | `/mnt/main/family/images/photos/{george,mara}/` (ZFS, lz4, recordsize=1M, atime=off) |
| SSH key | `/mnt/main/apps/immich-photos-backup/ssh/id_ed25519_alcatraz` (mode 600, root) |
| Metric | `immich_photos_backup_last_success_seconds` via node-exporter textfile collector |
| Alert | `ImmichPhotoBackupStale` (fires after >36h) |

## One-time bootstrap

Run on hestia as root (TrueNAS Web UI → System Settings → Shell).

### 1. ZFS datasets + snapshot tasks
The destination dataset is `main/family/images/photos` (renamed from `main/backups/immich-photos` in the 2026-06-01 hestia-SOT migration — see `docs/plans/2026-06-01-hestia-photos-sot.md`). Snapshot tasks (daily/14, weekly/8, monthly/12) run on the `main/family` parent. To verify:
```bash
midclt call pool.dataset.query '[["id", "=", "main/family/images/photos"]]' \
  | jq '.[0] | {compression: .compression.value, recordsize: .recordsize.value, atime: .atime.value, mountpoint}'
midclt call pool.snapshottask.query '[["dataset", "=", "main/family"]]' \
  | jq '.[] | {lifetime_value, lifetime_unit, schedule: .schedule | "\(.hour):\(.minute) dow=\(.dow) dom=\(.dom)"}'
```

### 2. SSH key on hestia
```bash
mkdir -p /mnt/main/apps/immich-photos-backup/ssh
mv /root/.ssh/id_ed25519_alcatraz    /mnt/main/apps/immich-photos-backup/ssh/
mv /root/.ssh/id_ed25519_alcatraz.pub /mnt/main/apps/immich-photos-backup/ssh/
chown -R root:root /mnt/main/apps/immich-photos-backup
chmod 700 /mnt/main/apps/immich-photos-backup/ssh
chmod 600 /mnt/main/apps/immich-photos-backup/ssh/id_ed25519_alcatraz
chmod 644 /mnt/main/apps/immich-photos-backup/ssh/id_ed25519_alcatraz.pub
```

### 3. SSH key on alcatraz (already done)
The matching public key is in `truenas-backup@10.42.2.11:/var/services/homes/truenas-backup/.ssh/authorized_keys`. The `truenas-backup` user needs Read access on the `homes` shared folder and on each user's `Photos/` subtree (verify per-user — DSM file-level ACLs override share-level grants; if needed, in DSM Control Panel → Shared Folder → homes → Edit → Permissions → tick "Apply to this folder, sub-folders and files"). Verify access:
```bash
ssh -i /mnt/main/apps/immich-photos-backup/ssh/id_ed25519_alcatraz \
    truenas-backup@10.42.2.11 'ls /volume1/homes/george/Photos | head; ls /volume1/homes/mara/Photos | head'
```

### 4. node-exporter textfile collector
If not already running, node-exporter on hestia must be invoked with `--collector.textfile.directory=/var/lib/node-exporter/textfile`. The container writes `immich-backup.prom` there on each successful run, which Prometheus scrapes for the `ImmichPhotoBackupStale` alert in `infra/configs/alerts/prometheus-rules.yaml`.

### 5. Create the Custom App in SCALE UI (one-time — auto-deploy can't bootstrap)
- Apps → Discover Apps → **Custom App**
- Name: `immich-photos-backup` (matches `hosts/hestia/immich-photos-backup/` — that's what the deploy-hestia matrix derives)
- Paste the contents of `docker-compose.yml` from this directory
- Install. Wait for state=RUNNING.

### 6. Kick the first run manually (don't wait for 04:00)
```bash
docker exec -it ix-immich-photos-backup-immich-photos-backup-1 \
  /usr/local/bin/immich-photos-backup.sh
```
First run pulls ~425 GB over gigabit (~30-60 min). Detach-safe via `docker logs -f` in another shell. On success the textfile metric is written and the container goes back to waiting for the next 04:00.

## Subsequent updates

Every change to `images/immich-photos-backup/**` on `master` triggers `.github/workflows/build-immich-photos-backup.yml` → publishes `ghcr.io/gjcourt/immich-photos-backup:YYYY-MM-DD` (with `:latest` mirror). Bump the digest in `docker-compose.yml` in a follow-up PR; `.github/workflows/deploy-hestia.yml` then auto-applies via `truenas-update-app.sh`.

To roll back: revert the digest in `docker-compose.yml` and merge; auto-deploy applies the prior image.

## Logs + observability

- Container logs: `docker logs ix-immich-photos-backup-immich-photos-backup-1` (or via SCALE UI → Apps → immich-photos-backup → Logs)
- Last successful run timestamp: `cat /var/lib/node-exporter/textfile/immich-backup.prom`
- Snapshots: TrueNAS UI → Storage → Snapshots, filter by dataset `main/family`
- Prometheus alert state: query `ALERTS{alertname="ImmichPhotoBackupStale"}`
