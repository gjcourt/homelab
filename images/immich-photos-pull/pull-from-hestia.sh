#!/bin/bash
# Daily additive PULL of the Immich photo library from hestia -> alcatraz.
#
# Direction: this script runs ON alcatraz (Synology DSM, 10.42.2.11) as root.
# It is baked into ghcr.io/gjcourt/immich-photos-pull and fired nightly by the
# container's busybox crond (see images/immich-photos-pull/crontab); the
# container runs as root so the post-rsync chown below can re-own received
# files to each DSM account. It PULLS from hestia (TrueNAS, 10.42.2.10), which
# is the source of truth. hestia holds every photo — phone uploads that were
# pulled up from alcatraz by the hestia-side backup container, PLUS
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
# command="rrsync -ro /mnt/main/family/media/photos" here; the real
# authorized_keys line wraps it in `sudo -n --preserve-env=SSH_ORIGINAL_COMMAND`
# — see README for the exact, load-bearing form). Because
# rrsync confines the client to that root, source paths are RELATIVE to it
# (e.g. "mara/") — an ABSOLUTE path gets the root prepended a second time and
# fails ("change_dir ...photos/mnt/.../photos/mara: No such file"). The
# authorizing rrsync root on hestia is: /mnt/main/family/media/photos
SRC_HOST="truenas_admin@10.42.2.10"

# alcatraz per-user DSM Photos libraries (destination, local).
DST_BASE="/volume1/homes"                     # <DST_BASE>/<user>/Photos/

# SSH identity used to reach hestia. Generate as a DSM admin (or the
# truenas-backup account) and drop the private key here, mode 600; the matching
# public key goes into truenas_admin@hestia:~/.ssh/authorized_keys as a
# read-only-rsync forced command (see README). Persist known_hosts alongside it
# so accept-new only prompts on the very first run. Both are bind-mounted into
# the container at these exact paths (see docker-compose.yml).
SSH_KEY="/volume1/homes/truenas-backup/.ssh/id_ed25519_hestia"
KNOWN_HOSTS="/volume1/homes/truenas-backup/.ssh/known_hosts_hestia"

LOG="/var/log/immich-photos-pull.log"
# ----------------------------------------------------------------------------

exec > >(tee -a "${LOG}")
exec 2>&1

FAILED=0
START_TS=$(date +%s)
# Scratch file holding the list of files rsync actually transferred this run
# (see the --out-format capture below). Cleaned up by the EXIT trap.
XFER_LIST="$(mktemp)"

# Guarantee the `=== ... END (...) ===` trailer ALWAYS logs, even if a per-user
# leg is slow, errors out under `set -e`, or the run is signalled (SIGTERM).
# The hestia-side homelabscope-heartbeat writer greps this log for the
# `END (success, Ns)` trailer to publish the job's freshness metric — a run
# that exits WITHOUT the trailer silently goes "stale" in monitoring even
# though it may have done real work. This trap closes that gap: it runs on any
# normal or error exit and emits exactly one trailer, keyed off the script's
# real exit code plus the FAILED counter. (SIGKILL still can't be trapped, but
# the per-file chown further down removes the O(library-size) `chown -R` step
# that used to blow past the DSM runtime cap and get the process killed mid-run
# — the original reason a run could finish the rsync yet never log an END line.)
on_exit() {
  local ec=$?
  local end_ts dur
  end_ts=$(date +%s)
  dur=$((end_ts - START_TS))
  if [[ ${ec} -eq 0 && ${FAILED} -eq 0 ]]; then
    echo "=== $(date -u +%FT%TZ) END (success, ${dur}s) ==="
  else
    echo "=== $(date -u +%FT%TZ) END (FAILED, ${FAILED} of ${#USERS[@]} users, rc=${ec}, ${dur}s) ==="
  fi
  rm -f "${XFER_LIST}"
}
trap on_exit EXIT

echo "=== $(date -u +%FT%TZ) START (hestia -> alcatraz pull) ==="

# chacha20-poly1305 + no SSH compression matches the hestia-side backup's
# high-throughput configuration. -T (no pty) and -x (no X11) keep the channel
# minimal. StrictHostKeyChecking=accept-new pins the host key on first contact
# and fails closed if it ever changes; UserKnownHostsFile persists that pin so
# an unattended run never blocks on an interactive prompt.
RSYNC_RSH="ssh -T -x -i ${SSH_KEY} \
           -o StrictHostKeyChecking=accept-new \
           -o UserKnownHostsFile=${KNOWN_HOSTS} \
           -c chacha20-poly1305@openssh.com -o Compression=no"

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
  # --out-format='%n': print ONE line per transferred item (path relative to
  #   ${dst}). We redirect this list to ${XFER_LIST} (stdout only; rsync's
  #   errors still go to fd 2 -> the tee'd log) so the chown below can touch
  #   ONLY the newly-pulled files instead of re-walking the whole library.
  #   (This replaces the old --stats output, which is why there is no --stats.)
  # Excludes: @eaDir (Synology indexer metadata), .DS_Store (macOS),
  #   Thumbs.db (Windows) — junk that should never propagate.
  # `|| rc=$?` (not `if ! rsync`) so ${rc} captures rsync's REAL exit code for
  # the log (23=partial, 12=protocol, etc.). The `|| ...` list also suppresses
  # set -e, so one user's failure never aborts the loop — we record it and skip
  # to the next user.
  rc=0
  rsync -a --ignore-existing \
        --out-format='%n' \
        --exclude='@eaDir' \
        --exclude='.DS_Store' \
        --exclude='Thumbs.db' \
        --rsh="${RSYNC_RSH}" \
        "${src}" "${dst}" > "${XFER_LIST}" || rc=$?
  if [[ ${rc} -ne 0 ]]; then
    echo "!!! ${user} pull rsync failed rc=${rc}"
    FAILED=$((FAILED + 1))
    # Don't chown a half/failed transfer — skip to the next user.
    continue
  fi

  # This container runs as root SPECIFICALLY so this chown works: rsync-received
  # files land root-owned (client runs as root), but DSM Photos only indexes
  # files owned by the account. Re-own JUST the newly-transferred files to
  # <uid>:100 so they match alcatraz's own uploads.
  #
  # Why only the new files (not `chown -R ${dst}`): george's library is ~289k
  # files; a nightly `chown -R` is O(library size) and took minutes, which blew
  # past the DSM runtime cap and got the run killed BEFORE the END trailer
  # logged (heartbeat then went stale despite the rsync succeeding). rsync only
  # adds a few thousand files a night, so we chown exactly that transferred set.
  #
  # Why NUMERIC (${uid}:${GID_USERS}, not name:users): the DSM users
  # (mara=1027, george=1028) are NOT present in this container's /etc/passwd, so
  # a name-based chown (or rsync --chown=user:users) would fail to resolve.
  # Numeric ids need no passwd entry and set exactly the ownership DSM keys on.
  #
  # -h chowns symlinks themselves rather than dereferencing. Paths in
  # ${XFER_LIST} are relative to ${dst}; sed prefixes ${dst} (which contains no
  # '#', so '#' is a safe delimiter) and NUL-delimiting into a single xargs
  # keeps the cost O(new files). `|| crc=$?` preserves rsync-style real-exit
  # capture under set -e.
  if [[ -s "${XFER_LIST}" ]]; then
    new_count="$(wc -l < "${XFER_LIST}" | tr -d ' ')"
    echo "--- $(date -u +%FT%TZ) ${user}: chown ${new_count} new item(s) -> ${uid}:${GID_USERS} ---"
    crc=0
    sed "s#^#${dst}#" "${XFER_LIST}" | tr '\n' '\0' \
      | xargs -0 -r chown -h "${uid}:${GID_USERS}" || crc=$?
    if [[ ${crc} -ne 0 ]]; then
      echo "!!! ${user} chown of new files failed rc=${crc}"
      FAILED=$((FAILED + 1))
      continue
    fi
  else
    echo "--- $(date -u +%FT%TZ) ${user}: no new files transferred ---"
  fi
  echo "--- $(date -u +%FT%TZ) ${user} OK ---"
done

# The EXIT trap (above) logs the END trailer for BOTH outcomes; here we only set
# the process exit code so cron/monitoring sees success vs failure.
if [[ ${FAILED} -eq 0 ]]; then
  exit 0
else
  exit 1
fi
