#!/bin/bash
# Daily incremental backup of the Immich photo library from alcatraz → hestia.
#
# Pulls /volume1/family/images/photos on alcatraz (Synology NAS, 10.42.2.11)
# into the local ZFS dataset main/backups/immich-photos. Snapshots are managed
# by a TrueNAS periodic-snapshot task scheduled after this rsync completes.
#
# Deployed manually to /usr/local/bin/immich-photos-backup.sh on hestia and
# scheduled via cron at 04:00 daily (after CNPG S3 backups at 02:00).
#
# Reports backup freshness to node-exporter's textfile collector at
# /var/lib/node-exporter/textfile/immich-backup.prom so the
# `ImmichPhotoBackupStale` alert in infra/configs/alerts/prometheus-rules.yaml
# can fire when daily runs stop landing.

set -euo pipefail

SRC="truenas-backup@10.42.2.11:/volume1/family/images/photos/"
DST="/mnt/main/backups/immich-photos/"
SSH_KEY="/root/.ssh/id_ed25519_alcatraz"
# Bind-mounted from host so first-run host-key acceptance survives
# container restarts; see docker-compose.yml.
KNOWN_HOSTS="/root/.ssh/known_hosts"
LOG="/var/log/immich-photos-backup.log"
TEXTFILE_DIR="/var/lib/node-exporter/textfile"
TEXTFILE="${TEXTFILE_DIR}/immich-backup.prom"

mkdir -p "${TEXTFILE_DIR}"
exec > >(tee -a "${LOG}")
exec 2>&1

START_TS=$(date +%s)
echo "=== $(date -u +%FT%TZ) START ==="

# chacha20-poly1305 + no SSH compression matches the plan's high-throughput
# configuration. --delete keeps the destination tight to the source.
#
# Host-key handling: when run from cron there's no stdin to answer a
# `Are you sure you want to continue connecting?` prompt — without
# StrictHostKeyChecking=accept-new the first run inside a fresh container
# (no known_hosts) hangs forever and rsync eventually fails with code 12.
# We point UserKnownHostsFile at a bind-mounted host path so the accepted
# host key persists across container restarts (otherwise it lives only in
# the container's writable layer and is lost on restart).
if rsync -avh --delete \
    --rsh="ssh -T -i ${SSH_KEY} \
           -o StrictHostKeyChecking=accept-new \
           -o UserKnownHostsFile=${KNOWN_HOSTS} \
           -c chacha20-poly1305@openssh.com -o Compression=no -x" \
    --stats \
    "${SRC}" "${DST}"; then
  END_TS=$(date +%s)
  DURATION=$((END_TS - START_TS))
  echo "=== $(date -u +%FT%TZ) END (success, ${DURATION}s) ==="

  cat > "${TEXTFILE}.tmp" <<EOF
# HELP immich_photos_backup_last_success_seconds Unix timestamp of last successful immich photos rsync
# TYPE immich_photos_backup_last_success_seconds gauge
immich_photos_backup_last_success_seconds ${END_TS}
# HELP immich_photos_backup_duration_seconds Wall-clock duration of last successful rsync
# TYPE immich_photos_backup_duration_seconds gauge
immich_photos_backup_duration_seconds ${DURATION}
EOF
  mv "${TEXTFILE}.tmp" "${TEXTFILE}"
  exit 0
else
  RC=$?
  echo "=== $(date -u +%FT%TZ) END (FAILED rc=${RC}) ==="
  # Intentionally do NOT touch the textfile metric on failure — the alert
  # rule keys off staleness of the last *successful* run.
  exit "${RC}"
fi
