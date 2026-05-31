# hestia backup scripts

Source of truth for daily backup jobs that run on hestia (TrueNAS Scale) and
pull data from external hosts into local ZFS datasets.

## immich-photos-backup.sh

Daily rsync of the Immich photo library (`/volume1/family/images/photos` on
alcatraz, ~5 TiB) into the local ZFS dataset `main/backups/immich-photos`.

### Architecture

```
alcatraz (Synology, 10.42.2.11)            hestia (TrueNAS, 10.42.2.10)
  /volume1/family/images/photos   ─SSH→     /mnt/main/backups/immich-photos/
        (primary, RW)                              (mirror, RO via snapshots)
```

- **Primary** stays on alcatraz; Immich keeps mounting alcatraz NFS for live access.
- **Backup** on hestia is pull-only — alcatraz never reaches into hestia.
- **Snapshots** are managed by a TrueNAS periodic-snapshot task on the dataset, not by the rsync script. Snapshot retention: daily/14, weekly/8, monthly/12.

### Manual deploy (operator-only)

The deploy-hestia GitHub Actions workflow only auto-deploys docker-compose
files. Shell scripts are copied manually.

```bash
# 1. As truenas_admin on hestia
sudo install -m 0755 -o root -g root \
  /tmp/immich-photos-backup.sh /usr/local/bin/immich-photos-backup.sh

# 2. Add a cron entry. Pick 04:00 — after the 02:00 CNPG S3 backup window
#    so they don't contend for the WAN uplink.
sudo crontab -e
# 0 4 * * *  /usr/local/bin/immich-photos-backup.sh

# 3. First run is the 5 TiB initial seed. Run it manually inside a `tmux` or
#    `screen` session — it'll take ~12 hours over gigabit. After the seed,
#    daily incremental runs typically finish in <30 minutes.
tmux new -s immich-seed
/usr/local/bin/immich-photos-backup.sh
```

### Prerequisites the operator must set up before the script can run

1. **ZFS dataset on hestia:**
   - Name: `main/backups/immich-photos`
   - Compression: `lz4` (default)
   - `recordsize=1M`
   - `atime=off`
   - Quota: `8T`

2. **TrueNAS periodic snapshot task** on the dataset:
   - Daily snapshot, retain 14 days
   - Weekly snapshot, retain 8 weeks
   - Monthly snapshot, retain 12 months
   - Schedule **after** the cron-driven rsync (e.g. `05:00` if rsync starts at `04:00` and finishes within an hour).

3. **SSH key from hestia → alcatraz:**
   ```bash
   # On hestia (as root or via sudo):
   ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519_alcatraz -N "" -C "immich-photos-backup@hestia"
   cat /root/.ssh/id_ed25519_alcatraz.pub
   ```
   Then on alcatraz (DSM):
   - Create a dedicated user `truenas-backup` (administrator group so it's allowed to SSH).
   - Enable User Home Service (Control Panel → User & Group → Advanced).
   - Paste the pubkey into `/var/services/homes/truenas-backup/.ssh/authorized_keys` (file must be owned by `truenas-backup`, mode 600; parent `.ssh` owned by user, mode 700).
   - Grant `truenas-backup` Read access on the `family` shared folder (Control Panel → Shared Folder → family → Edit → Permissions).
   - Verify from hestia:
     ```bash
     ssh -i /root/.ssh/id_ed25519_alcatraz truenas-backup@10.42.2.11 'ls /volume1/family/images/photos | head'
     ```
   Optional hardening: prefix the key entry with `command="rsync --server ..."` to restrict it to rsync only.

4. **node-exporter textfile collector** must be running on hestia with
   `--collector.textfile.directory=/var/lib/node-exporter/textfile`. The script
   writes `immich-backup.prom` there.

### Monitoring

The script writes two Prometheus textfile metrics on success:

| Metric | Type | Meaning |
|---|---|---|
| `immich_photos_backup_last_success_seconds` | gauge | Unix timestamp of last successful rsync |
| `immich_photos_backup_duration_seconds` | gauge | Wall-clock duration of last successful rsync |

The `ImmichPhotoBackupStale` alert in
`infra/configs/alerts/prometheus-rules.yaml` fires if the timestamp is older
than 36 hours (one missed daily run + buffer).

Logs land in `/var/log/immich-photos-backup.log`. The script tees stdout/stderr,
so `tail -F` works for live observation.
