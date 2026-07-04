#!/bin/bash
# Daily DUPLEX sync of the Immich photo library between alcatraz and hestia.
# Pull (alcatraz -> hestia): brings phone uploads into hestia (the source of truth).
# Push-back (hestia -> alcatraz, --ignore-existing): copies anything hestia has that
# alcatraz lacks (e.g. direct SD-card imports) so alcatraz stays a full backup.
# No --delete in either direction -> both sides converge to the UNION; neither wipes
# the other. hestia is authoritative; alcatraz is the phone-upload target + backup.
#
# Pulls per-user Photos dirs from alcatraz (Synology NAS, 10.42.2.11) into the
# local ZFS dataset main/family/images/photos. Snapshots are managed by a
# TrueNAS periodic-snapshot task scheduled after this rsync completes.
#
# Source-path note: on alcatraz, /volume1/family/images/photos/<user> is a
# symlink into /volume1/homes/<user>/Photos (the real bytes live in each
# user's home). truenas-backup has share-level read on family/ but per-file
# ACLs from each user's Synology account deny file open via the family path —
# so we sync from the homes path directly, mapping into the canonical hestia
# layout family/images/photos/<user>/.
#
# Mode-fix note: Synology DSM Photos uploads new files with POSIX mode 0700
# (owner-only). truenas-backup is in gid=100(users), which matches the file
# group, so POSIX checks the group bit (`---`) and denies before falling
# through to "other" — rsync hits send_files Permission denied (13) on every
# new file. The fix runs a recursive chmod on the source via --rsync-path
# before each rsync. See docs in hosts/hestia/immich-photos-backup/README.md
# (section "Sudoers entry on alcatraz") for the required NOPASSWD sudoers
# entry on alcatraz — without it `sudo -n` fails immediately, rsync exits
# with code 12 (protocol data stream error from remote command dying before
# protocol start), the run is recorded FAILED in the log, and the metric
# stays stale. Loud failure is intentional — silent perm-denied was the
# original bug.
#
# Deployed as a TrueNAS Custom App; cron at 04:00 daily is fired by the
# container's busybox crond (see hosts/hestia/immich-photos-backup/).
#
# Reports backup freshness to node-exporter's textfile collector at
# /var/lib/node-exporter/textfile/immich-backup.prom so the
# `ImmichPhotoBackupStale` alert in infra/configs/alerts/prometheus-rules.yaml
# can fire when daily runs stop landing.

set -euo pipefail

USERS=(george mara)
DST_BASE="/mnt/main/family/images/photos"
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
# configuration. No --delete: hestia is the SOURCE OF TRUTH (superset). The pull is
# additive, so phone uploads flow in but direct-to-hestia imports (e.g. an SD-card
# import via import-sd-photos.sh) are NEVER wiped. See docs/plans/2026-06-01-hestia-photos-sot.md.
#
# Host-key handling: when run from cron there's no stdin to answer a
# `Are you sure you want to continue connecting?` prompt — without
# StrictHostKeyChecking=accept-new the first run inside a fresh container
# (no known_hosts) hangs forever and rsync eventually fails with code 12.
# We point UserKnownHostsFile at a bind-mounted host path so the accepted
# host key persists across container restarts (otherwise it lives only in
# the container's writable layer and is lost on restart).
#
# Excludes:
#   @eaDir         Synology indexer metadata + xattr backups
#                  (@eaDir/<file>@SynoEAStream). Useless on hestia, bloats
#                  the backup, and litters the tree with one dir per photo
#                  dir. Synology recreates them on alcatraz as needed.
#   .DS_Store      macOS Finder metadata; same deal.
#   Thumbs.db      Windows thumbnail cache.
# (Excluded junk is simply not copied. With no --delete, any junk from an earlier
#  run stays on hestia -- harmless, and never touches real photos.)
RSYNC_RSH="ssh -T -i ${SSH_KEY} \
           -o StrictHostKeyChecking=accept-new \
           -o UserKnownHostsFile=${KNOWN_HOSTS} \
           -c chacha20-poly1305@openssh.com -o Compression=no -x"

FAILED=0
for user in "${USERS[@]}"; do
  src="truenas-backup@10.42.2.11:/volume1/homes/${user}/Photos/"
  dst="${DST_BASE}/${user}/"
  mkdir -p "${dst}"
  echo "--- $(date -u +%FT%TZ) sync ${user}: ${src} -> ${dst} ---"
  # See "Mode-fix note" in the header. The chmod runs on the remote (alcatraz)
  # before rsync's own server-side invocation, fixing 0700 uploads in-place so
  # truenas-backup can read them. `&&` so a chmod failure aborts the run with
  # a clear remote-command-exited error rather than silently degrading.
  #
  # `sudo -n` (non-interactive) is load-bearing: without it, an ssh session
  # without a controlling tty will either fail with an opaque "no tty present"
  # or hang trying to open /dev/tty if NOPASSWD isn't in effect — the inverse
  # of the loud-failure mode we want. -n exits immediately with
  # "sudo: a password is required", which surfaces in the cron log.
  REMOTE_RSYNC="sudo -n /bin/chmod -R g+rX,o+rX /volume1/homes/${user}/Photos && rsync"
  if ! rsync -avh \
        --exclude='@eaDir' \
        --exclude='.DS_Store' \
        --exclude='Thumbs.db' \
        --rsh="${RSYNC_RSH}" \
        --rsync-path="${REMOTE_RSYNC}" \
        --stats \
        "${src}" "${dst}"; then
    rc=$?
    echo "!!! ${user} PULL rsync failed rc=${rc}"
    FAILED=$((FAILED + 1))
  fi

  # --- duplex push-back: hestia -> alcatraz, copy only what alcatraz lacks ---
  # --ignore-existing never overwrites alcatraz's copy (no conflicts, no --delete);
  # it only adds missing files (e.g. SD-card imports made directly on hestia).
  # Remote rsync runs via `sudo -n rsync` so it can write into the user's home and
  # --chown the files to the right owner. This needs a truenas-backup sudoers entry
  # for rsync on alcatraz (see README, "Sudoers entry on alcatraz"). A push-back
  # failure is a WARNING only -- the pull above is what the staleness alert keys off.
  if ! rsync -avh --ignore-existing --chown="${user}:users" \
        --exclude='@eaDir' \
        --exclude='.DS_Store' \
        --exclude='Thumbs.db' \
        --rsh="${RSYNC_RSH}" \
        --rsync-path="sudo -n rsync" \
        "${dst}" "${src}"; then
    echo "!!! ${user} push-back (hestia->alcatraz backup) failed rc=$? -- pull OK; check alcatraz sudoers for rsync"
  fi
done

END_TS=$(date +%s)
DURATION=$((END_TS - START_TS))

if [[ ${FAILED} -eq 0 ]]; then
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
  echo "=== $(date -u +%FT%TZ) END (FAILED, ${FAILED} of ${#USERS[@]} users, ${DURATION}s) ==="
  # Intentionally do NOT touch the textfile metric on failure — the alert
  # rule keys off staleness of the last *successful* run.
  exit 1
fi
