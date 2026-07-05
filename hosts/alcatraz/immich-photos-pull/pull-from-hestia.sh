#!/bin/bash
# Daily additive PULL of the Immich photo library from hestia -> alcatraz.
#
# Direction: this script runs ON alcatraz (Synology DSM, 10.42.2.11) as root
# via a DSM Task Scheduler job, and PULLS from hestia (TrueNAS, 10.42.2.10),
# which is the source of truth. hestia holds every photo — phone uploads that
# were pulled up from alcatraz by the hestia-side backup container, PLUS
# direct-to-hestia SD-card imports (see scripts/import-sd-photos.sh). This job
# copies back to alcatraz anything hestia has that alcatraz's per-user
# DSM Photos libraries lack, so alcatraz stays a full second copy and DSM
# Photos can index the SD-card imports.
#
# Why the PULL direction (and NOT a hestia->alcatraz push): Synology's
# /bin/rsync is setuid-root and, in inbound server mode, authenticates the
# REAL uid against the DSM account database. A hestia-initiated push therefore
# has no working invocation:
#   * `sudo rsync`        -> real uid = root  -> root is a disabled DSM
#                            account -> rejected ("user has disabled/expired").
#   * `sudo -u mara rsync`-> real uid = mara  -> non-admin, rejected (the
#                            check gates on the administrators group).
#   * bare `truenas-backup`/admin rsync -> passes the account check, but an
#                            admin that isn't mara cannot write mara's private
#                            0700 homes/<user>/Photos with correct ownership.
# No sudoers rule squares this — it's Synology's inbound-rsync security model.
# Reversing the direction sidesteps it entirely: here alcatraz's rsync is the
# CLIENT writing to its OWN local filesystem as local root, so no inbound
# account check applies and local root can chown the received files to the
# owning DSM user. The rsync SERVER runs on hestia (plain Linux, no Synology
# setuid patch). Full root-cause + decision record:
#   docs/plans/2026-07-04-alcatraz-photos-pull.md
#
# Invariant: ADDITIVE ONLY. --ignore-existing means an alcatraz copy of a file
# is NEVER overwritten (alcatraz's phone-upload originals win); only files
# alcatraz lacks are added. There is NO --delete in this direction, ever —
# deleting on alcatraz would destroy phone-upload originals or DSM Photos state.

set -euo pipefail

# ---- Config (edit here) -----------------------------------------------------
# "name:uid" per DSM user. The uid is used for the post-rsync chown so files
# land owned by the DSM account (gid is always users=100). DSM uids:
# mara=1027, george=1028; both users are also in gid 1023(http) but 100(users)
# is what DSM Photos and the homes ACL key on.
USERS=("mara:1027" "george:1028")
GID_USERS=100

# hestia rsync server (source of truth). truenas_admin (uid 950) has read on
# the photo dirs; the authorized_keys entry there restricts this key to a
# read-only rrsync rooted at the photos path (abbreviated as
# command="rrsync -ro /mnt/main/family/images/photos" here; the real
# authorized_keys line wraps it in `sudo -n --preserve-env=SSH_ORIGINAL_COMMAND`
# — see README for the exact, load-bearing form). Because
# rrsync confines the client to that root, source paths are RELATIVE to it
# (e.g. "mara/") — an ABSOLUTE path gets the root prepended a second time and
# fails ("change_dir ...photos/mnt/.../photos/mara: No such file"). The
# authorizing rrsync root on hestia is: /mnt/main/family/images/photos
SRC_HOST="truenas_admin@10.42.2.10"

# alcatraz per-user DSM Photos libraries (destination, local).
DST_BASE="/volume1/homes"                     # <DST_BASE>/<user>/Photos/

# SSH identity used to reach hestia. Generate as a DSM admin (or the
# truenas-backup account) and drop the private key here, mode 600; the matching
# public key goes into truenas_admin@hestia:~/.ssh/authorized_keys as a
# read-only-rsync forced command (see README). Persist known_hosts alongside it
# so accept-new only prompts on the very first run.
SSH_KEY="/volume1/homes/truenas-backup/.ssh/id_ed25519_hestia"
KNOWN_HOSTS="/volume1/homes/truenas-backup/.ssh/known_hosts_hestia"

LOG="/var/log/immich-photos-pull.log"
# ----------------------------------------------------------------------------

exec > >(tee -a "${LOG}")
exec 2>&1

START_TS=$(date +%s)
echo "=== $(date -u +%FT%TZ) START (hestia -> alcatraz pull) ==="

# chacha20-poly1305 + no SSH compression matches the hestia-side backup's
# high-throughput configuration. -T (no pty) and -x (no X11) keep the channel
# minimal. StrictHostKeyChecking=accept-new pins the host key on first contact
# and fails closed if it ever changes; UserKnownHostsFile persists that pin so
# an unattended Task Scheduler run never blocks on an interactive prompt.
RSYNC_RSH="ssh -T -x -i ${SSH_KEY} \
           -o StrictHostKeyChecking=accept-new \
           -o UserKnownHostsFile=${KNOWN_HOSTS} \
           -c chacha20-poly1305@openssh.com -o Compression=no"

FAILED=0
for entry in "${USERS[@]}"; do
  user="${entry%%:*}"
  uid="${entry##*:}"
  src="${SRC_HOST}:${user}/"   # relative to the rrsync root (see SRC_HOST note)
  dst="${DST_BASE}/${user}/Photos/"
  mkdir -p "${dst}"
  echo "--- $(date -u +%FT%TZ) pull ${user} (uid=${uid}): ${src} -> ${dst} ---"

  # -a: archive (recursive, perms/times/symlinks preserved).
  # --ignore-existing: additive — never clobber alcatraz's phone-upload copies;
  #   only pull files alcatraz is missing. NO --delete: this is a backfill, not
  #   a mirror.
  # Excludes: @eaDir (Synology indexer metadata), .DS_Store (macOS),
  #   Thumbs.db (Windows) — junk that should never propagate.
  # `|| rc=$?` (not `if ! rsync`) so ${rc} captures rsync's REAL exit code for
  # the log (23=partial, 12=protocol, etc. — the codes the header cares about);
  # a plain `if ! rsync` would make $? the negation's status (always 0). The
  # `|| ...` list also suppresses set -e, so one user's failure never aborts the
  # loop — we record it and skip to the next user.
  rc=0
  rsync -a --ignore-existing \
        --exclude='@eaDir' \
        --exclude='.DS_Store' \
        --exclude='Thumbs.db' \
        --rsh="${RSYNC_RSH}" \
        --stats \
        "${src}" "${dst}" || rc=$?
  if [[ ${rc} -ne 0 ]]; then
    echo "!!! ${user} pull rsync failed rc=${rc}"
    FAILED=$((FAILED + 1))
    # Don't chown a half/failed transfer's tree — skip to the next user.
    continue
  fi

  # This Task Scheduler job runs as root SPECIFICALLY so this chown works:
  # rsync-received files may land root-owned (client runs as root), but DSM
  # Photos will only index files owned by the account. Re-own the whole tree
  # to <uid>:users so newly pulled files match alcatraz's own uploads.
  # Idempotent (a no-op re-chown just bumps ctime); runs over the full tree
  # every night, so cost scales with inode count, not with what was pulled.
  # `|| rc=$?` for the same real-exit-code + set -e reasons as the rsync above.
  rc=0
  chown -R "${uid}:${GID_USERS}" "${dst}" || rc=$?
  if [[ ${rc} -ne 0 ]]; then
    echo "!!! ${user} chown ${uid}:${GID_USERS} ${dst} failed rc=${rc}"
    FAILED=$((FAILED + 1))
    continue
  fi
  echo "--- $(date -u +%FT%TZ) ${user} OK ---"
done

END_TS=$(date +%s)
DURATION=$((END_TS - START_TS))

if [[ ${FAILED} -eq 0 ]]; then
  echo "=== $(date -u +%FT%TZ) END (success, ${DURATION}s) ==="
  exit 0
else
  echo "=== $(date -u +%FT%TZ) END (FAILED, ${FAILED} of ${#USERS[@]} users, ${DURATION}s) ==="
  # Non-zero exit so DSM Task Scheduler emails the run output (if configured).
  exit 1
fi
