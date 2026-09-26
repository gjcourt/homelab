#!/bin/bash
# Daily PULL of the music library from hestia -> alcatraz: the off-box copy.
#
# hestia (TrueNAS, 10.42.2.10) holds the only other copy of the music library.
# ZFS snapshots on main/family are same-pool, so they protect against deletion
# but not against losing the pool. Once local rips are cleared from winpc, the
# discs they came from are back at SFPL and hestia would be the single site.
# This job makes alcatraz (Synology DSM, 10.42.2.11) a second site.
#
# Direction: runs ON alcatraz as truenas-backup, via a DSM Task Scheduler job.
# alcatraz PULLS for the same reason the photo job does (see
# hosts/alcatraz/immich-photos-pull/ and docs/plans/2026-07-04-alcatraz-photos-pull.md):
# Synology's setuid-root inbound rsync rejects a hestia-initiated push. Here
# alcatraz's rsync is the client writing to its own filesystem, so no inbound
# account check applies. Unlike photos there is no per-user DSM library to
# re-own files for, so this does NOT need root.
#
# Invariants:
#   * NEVER --delete. A file removed or lost on hestia stays here. That is the
#     point of a second site.
#   * A file CHANGED on hestia (a retag rewrites the whole FLAC) is updated here,
#     but the previous version is moved to $DST/.replaced/<run-date>/ first, so
#     a bad write on hestia cannot silently destroy the good copy.
#   * The key used here is pinned on hestia to a read-only rrsync rooted at the
#     music library, so a leaked key can only read music.

set -euo pipefail

# ---- Config -----------------------------------------------------------------
# rrsync confines the client to /mnt/main/family/media/music on hestia, so the
# source path is RELATIVE to that root. "./" is the whole library.
SRC="truenas_admin@10.42.2.10:./"
DST="/volume1/music/library"
SSH_KEY="/volume1/homes/truenas-backup/.ssh/id_ed25519_hestia_music"
KNOWN_HOSTS="/volume1/homes/truenas-backup/.ssh/known_hosts_hestia"
LOG="/volume1/homes/truenas-backup/music-pull/pull-music.log"
# ----------------------------------------------------------------------------

exec > >(tee -a "${LOG}")
exec 2>&1

START_TS=$(date +%s)
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)

# Always emit exactly one END trailer, whatever the exit path, so a monitor
# grepping for `END (success` can tell a finished run from a killed one.
on_exit() {
  local ec=$?
  local dur=$(( $(date +%s) - START_TS ))
  if [[ ${ec} -eq 0 ]]; then
    echo "=== $(date -u +%FT%TZ) END (success, ${dur}s) ==="
  else
    echo "=== $(date -u +%FT%TZ) END (FAILED, rc=${ec}, ${dur}s) ==="
  fi
}
trap on_exit EXIT

echo "=== $(date -u +%FT%TZ) START (hestia -> alcatraz music pull, run ${RUN_ID}) ==="
mkdir -p "${DST}"

# -rlt, not -a: owner/group can't be preserved without root, and don't need to
# be - this is a backup, read back by a human, not served to DSM apps.
rsync -rlt --chmod=D755,F644 \
  --backup --backup-dir="${DST}/.replaced/${RUN_ID}" \
  --exclude='@eaDir' --exclude='.DS_Store' --exclude='Thumbs.db' --exclude='/.replaced' \
  --partial-dir=.rsync-partial \
  --stats \
  --rsh="ssh -T -x -i ${SSH_KEY} -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${KNOWN_HOSTS}" \
  "${SRC}" "${DST}/"
