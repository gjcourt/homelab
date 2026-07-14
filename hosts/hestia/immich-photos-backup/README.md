# immich-photos-backup

Daily incremental **pull** of the Immich photo library from alcatraz (Synology, 10.42.2.11) into hestia's `main/family/media/photos`. Additive — brings phone uploads into hestia (the source of truth); no `--delete`, so direct-to-hestia SD-card imports are never wiped. Always-running container; busybox `crond` fires at 01:00 local.

The reverse leg — hestia→alcatraz, so alcatraz stays a full backup and DSM Photos picks up SD-card imports — is **not** a push from this container. It runs **from alcatraz** as a DSM Task Scheduler pull job: [`hosts/alcatraz/immich-photos-pull/`](../../alcatraz/immich-photos-pull/README.md). See ["Synology inbound-rsync limitation"](#synology-inbound-rsync-limitation) below for why a hestia-side push-back was retired.

Sources are per-user `homes/<user>/Photos/` paths, not `family/images/photos/<user>/`. On the Synology side those family paths are symlinks into each user's home, with per-file ACLs that deny `truenas-backup` file open via the family path — so we sync from the homes path directly and re-map into the canonical hestia `family/images/photos/<user>/` layout.

### Synology inbound-rsync limitation

A hestia→alcatraz **push** (which an earlier "duplex" version attempted via `--rsync-path="sudo -n rsync"`) **cannot work** and has been retired. Synology's `/bin/rsync` is **setuid-root** and, in inbound server mode, authenticates the **real uid** against the DSM account database:

- `sudo rsync` → real uid = **root**, a disabled DSM account → rejected ("user has disabled/expired").
- `sudo -u mara rsync` → real uid = **mara**, non-admin → rejected (the check gates on the administrators group).
- Only an **administrator** (e.g. `truenas-backup`) passes the account check — but an admin that isn't the owning user can't write that user's private `0700` `homes/<user>/Photos` with correct ownership.

No sudoers rule squares this. The fix is to reverse the direction: alcatraz **pulls** from hestia as local root (no inbound account check applies, and local root can chown the received files). That job lives in [`hosts/alcatraz/immich-photos-pull/`](../../alcatraz/immich-photos-pull/README.md). **No rsync sudoers entry on alcatraz is needed** — only the chmod sudoers rule (section 3a) that supports this container's pull leg remains.

| Attribute | Value |
|---|---|
| Image | `ghcr.io/gjcourt/immich-photos-backup` (built from `images/immich-photos-backup/`) |
| Schedule | `0 1 * * *` (01:00 ET / 05:00 UTC — before the 07:00 UTC Immich scan; CNPG S3 backup follows at 02:00 ET) |
| Sources | `truenas-backup@10.42.2.11:/volume1/homes/{george,mara}/Photos/` |
| Destinations | `/mnt/main/family/media/photos/{george,mara}/` (ZFS, lz4, recordsize=1M, atime=off) |
| SSH key | `/mnt/main/apps/immich-photos-backup/ssh/id_ed25519_alcatraz` (mode 600, root) |
| Metric | `immich_photos_backup_last_success_seconds` via node-exporter textfile collector |
| Alert | `ImmichPhotoBackupStale` (fires after >36h) |

## One-time bootstrap

Run on hestia as root (TrueNAS Web UI → System Settings → Shell).

### 1. ZFS datasets + snapshot tasks
The destination dataset is `main/family/media/photos` (renamed from `main/backups/immich-photos` in the 2026-06-01 hestia-SOT migration — see `docs/plans/2026-06-01-hestia-photos-sot.md`). Snapshot tasks (daily/14, weekly/8, monthly/12) run on the `main/family` parent. To verify:
```bash
midclt call pool.dataset.query '[["id", "=", "main/family/media/photos"]]' \
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

### 3a. Sudoers entry on alcatraz (one-time, required)

Synology DSM Photos uploads new files with POSIX mode `0700`. The owning group is `users` (gid 100) — which `truenas-backup` is also in, so the group bit's `---` denies before POSIX falls through to "other". rsync hits `send_files Permission denied (13)` on every new file and the run fails partially-but-silently (zero bytes transferred for new content; no metric update; the `ImmichPhotoBackupStale` alert eventually fires).

The script's per-user PULL runs a `sudo -n chmod -R g+rX,o+rX` on the source via rsync's `--rsync-path` before each transfer to fix this. That requires NOPASSWD sudo for `truenas-backup` on exactly that chmod (and no `requiretty` constraint — see Notes below). No `rsync` grant is needed: the retired hestia→alcatraz push-back is gone (see ["Synology inbound-rsync limitation"](#synology-inbound-rsync-limitation)), so the sudoers rule covers only the chmod:

```bash
# As a DSM admin (currently: manager) on alcatraz.
#
# DSM doesn't ship `visudo`, so we can't pre-validate the file before
# install. Mitigations:
#   1. Quoted heredoc ('EOF') — no shell substitution can corrupt content.
#   2. Post-install sanity check that sudo itself still works. If sanity
#      fails, the recovery path is to delete /etc/sudoers.d/immich-photos-backup
#      via DSM File Station (logged in as a DSM admin) — that skips sudo
#      entirely and avoids the chicken-and-egg trap.
cat > /tmp/immich-photos-backup.sudoers <<'EOF'
truenas-backup ALL=(root) NOPASSWD: /bin/chmod -R g+rX\,o+rX /volume1/homes/george/Photos, /bin/chmod -R g+rX\,o+rX /volume1/homes/mara/Photos
EOF
sudo install -m 0440 -o root -g root \
    /tmp/immich-photos-backup.sudoers \
    /etc/sudoers.d/immich-photos-backup
rm /tmp/immich-photos-backup.sudoers

# Sanity #1: sudo itself still works. If sudoers got corrupted by a typo,
# sudo refuses to operate at all and this exits non-zero.
sudo -n true && echo "sudo still functional"

# Sanity #2: the new rule is visible to truenas-backup. The output
# displays `\,` literally — that's sudo -l showing the escape, normal.
sudo -l -U truenas-backup 2>&1 | grep -i chmod
```

Sanity check the paths exist on your DSM (paths differ between Synology firmware versions):
```bash
ssh -i /mnt/main/apps/immich-photos-backup/ssh/id_ed25519_alcatraz \
    truenas-backup@10.42.2.11 'command -v sudo chmod'
```
Should print `/usr/bin/sudo` (or `/bin/sudo`) and `/bin/chmod`. If `chmod` resolves elsewhere on your DSM, update the script and sudoers line to match.

Notes:
- Commas inside the command args must be escaped (`g+rX\,o+rX`); unescaped commas separate multiple commands in sudoers.
- Adding a third user (e.g. `kid1`) requires extending this file with another comma-separated command — same pattern.
- DSM major-version upgrades may stomp on `/etc/sudoers.d/` entries. If a future DSM upgrade silently re-disables this, the next 01:00 cron will fail loudly: `sudo -n` exits immediately, `&&` short-circuits, rsync exits with code `12` (protocol data stream error). Re-add the file post-upgrade.
- If a future DSM update sets `Defaults requiretty` in `/etc/sudoers`, NOPASSWD alone won't be enough — sudo will reject all non-tty invocations including this one. Confirm `sudo -l` from a non-interactive ssh continues to work after any DSM upgrade.

Verify after install:
```bash
ssh -i /mnt/main/apps/immich-photos-backup/ssh/id_ed25519_alcatraz \
    truenas-backup@10.42.2.11 \
    'sudo -n /bin/chmod -R g+rX,o+rX /volume1/homes/george/Photos && echo ok'
```
Should print `ok` with no password prompt.

### 4. node-exporter textfile collector
If not already running, node-exporter on hestia must be invoked with `--collector.textfile.directory=/var/lib/node-exporter/textfile`. The container writes `immich-backup.prom` there on each successful run, which Prometheus scrapes for the `ImmichPhotoBackupStale` alert in `infra/configs/alerts/prometheus-rules.yaml`.

### 5. Create the Custom App in SCALE UI (one-time — auto-deploy can't bootstrap)
- Apps → Discover Apps → **Custom App**
- Name: `immich-photos-backup` (matches `hosts/hestia/immich-photos-backup/` — that's what the deploy-hestia matrix derives)
- Paste the contents of `docker-compose.yml` from this directory
- Install. Wait for state=RUNNING.

### 6. Kick the first run manually (don't wait for 01:00)
```bash
docker exec -it ix-immich-photos-backup-immich-photos-backup-1 \
  /usr/local/bin/immich-photos-backup.sh
```
First run pulls ~425 GB over gigabit (~30-60 min). Detach-safe via `docker logs -f` in another shell. On success the textfile metric is written and the container goes back to waiting for the next 01:00.

## Subsequent updates

Every change to `images/immich-photos-backup/**` on `master` triggers `.github/workflows/build-immich-photos-backup.yml` → publishes `ghcr.io/gjcourt/immich-photos-backup:YYYY-MM-DD` (with `:latest` mirror). Bump the digest in `docker-compose.yml` in a follow-up PR; `.github/workflows/deploy-hestia.yml` then auto-applies via `truenas-update-app.sh`.

The sudoers entry in section 3a is a **prerequisite** for any image whose script invokes `sudo -n /bin/chmod` (currently: all images since 2026-06-09). If the entry was wiped by a DSM upgrade and the script is updated in the meantime, the next 01:00 cron will fail until the entry is restored — verify before each digest bump if alcatraz had recent firmware updates.

To roll back: revert the digest in `docker-compose.yml` and merge; auto-deploy applies the prior image.

## Logs + observability

- Container logs: `docker logs ix-immich-photos-backup-immich-photos-backup-1` (or via SCALE UI → Apps → immich-photos-backup → Logs)
- Last successful run timestamp: `cat /var/lib/node-exporter/textfile/immich-backup.prom`
- Snapshots: TrueNAS UI → Storage → Snapshots, filter by dataset `main/family`
- Prometheus alert state: query `ALERTS{alertname="ImmichPhotoBackupStale"}`
