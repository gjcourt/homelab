#!/bin/bash
# homelabscope-heartbeat — hestia-side collector for scheduled jobs that can't
# run their own exporter.
#
# Two jobs are surfaced here, both READ-ONLY (this collector mutates nothing):
#
#   alcatraz-photos-pull   The nightly hestia -> alcatraz additive pull runs ON
#                          alcatraz (Synology DSM Task Scheduler), so it can't
#                          write to hestia's textfile dir. We SSH to alcatraz,
#                          read the world-readable pull log, and parse the last
#                          `=== <ts> END (success, Ns) ===` trailer for the last
#                          success time + duration.
#
#   zfs-snapshot-main-*    TrueNAS periodic-snapshot tasks have no metric. The
#                          newest snapshot's creation time per dataset IS the
#                          last-success time, so we read `zfs list -t snapshot`
#                          (falling back to the .zfs/snapshot control dir if the
#                          zfs userland can't talk to /dev/zfs in-container).
#
# Every source normalizes into the homelabscope metric family and is written to
# the node-exporter textfile collector dir; the hestia node-exporter (:9100)
# serves it and the infra/configs/homelabscope ScrapeConfig scrapes it. Writes
# are atomic (write .tmp, mv) so node-exporter never reads a half-written file.
#
# Runs as a long-lived container looping every INTERVAL_SECONDS so freshness
# stays current between the once-a-day jobs it watches.

set -uo pipefail

# ---- Config (env-overridable) ----------------------------------------------
INTERVAL_SECONDS="${INTERVAL_SECONDS:-600}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node-exporter/textfile}"

# alcatraz pull log (read over SSH). Key + user match the immich-photos-backup
# app's existing alcatraz credential (bind-mounted from the host).
ALCATRAZ_SSH="${ALCATRAZ_SSH:-truenas-backup@10.42.2.11}"
ALCATRAZ_KEY="${ALCATRAZ_KEY:-/root/.ssh/id_ed25519_alcatraz}"
ALCATRAZ_KNOWN_HOSTS="${ALCATRAZ_KNOWN_HOSTS:-/root/.ssh/known_hosts}"
ALCATRAZ_LOG="${ALCATRAZ_LOG:-/var/log/immich-photos-pull.log}"

# ZFS datasets to watch. "dataset:jobname:mountpoint" — mountpoint is used only
# for the .zfs/snapshot fallback when `zfs list` is unavailable in-container.
ZFS_TARGETS="${ZFS_TARGETS:-main/family:zfs-snapshot-main-family:/mnt/main/family main/homes:zfs-snapshot-main-homes:/mnt/main/homes}"
# Timezone the TrueNAS snapshot schedule names its snapshots in (auto-<ts>);
# only used by the .zfs/snapshot fallback to turn a snapshot NAME into an epoch.
ZFS_SNAP_TZ="${ZFS_SNAP_TZ:-America/Los_Angeles}"

# Staleness budgets (seconds). All watched jobs here are daily → 30h tolerates
# one fully-missed window before HomelabscopeJobStale fires.
MAX_AGE_DAILY="${MAX_AGE_DAILY:-108000}"
# ----------------------------------------------------------------------------

log() { echo "$(date -u +%FT%TZ) $*"; }

# emit_job <job> <last_success_epoch> <duration_seconds> <status> <max_age>
# Atomically writes the full homelabscope family for one job to its own .prom.
emit_job() {
  local job="$1" success="$2" duration="$3" status="$4" max_age="$5"
  local out="${TEXTFILE_DIR}/homelabscope-${job}.prom"
  cat > "${out}.tmp" <<EOF
# HELP homelabscope_job_last_success_seconds Unix timestamp of the job's last successful run.
# TYPE homelabscope_job_last_success_seconds gauge
homelabscope_job_last_success_seconds{job="${job}"} ${success}
# HELP homelabscope_job_last_duration_seconds Wall-clock duration of the job's last run (seconds).
# TYPE homelabscope_job_last_duration_seconds gauge
homelabscope_job_last_duration_seconds{job="${job}"} ${duration}
# HELP homelabscope_job_last_status Last run status (0=ok, 1=fail).
# TYPE homelabscope_job_last_status gauge
homelabscope_job_last_status{job="${job}"} ${status}
# HELP homelabscope_job_max_age_seconds Staleness budget for this job (seconds).
# TYPE homelabscope_job_max_age_seconds gauge
homelabscope_job_max_age_seconds{job="${job}"} ${max_age}
EOF
  mv "${out}.tmp" "${out}"
}

collect_alcatraz_pull() {
  local job="alcatraz-photos-pull" line ts dur epoch
  # Last successful END trailer, e.g.:
  #   === 2026-07-05T01:11:09Z END (success, 52s) ===
  line="$(ssh -T -x -i "${ALCATRAZ_KEY}" \
            -o StrictHostKeyChecking=accept-new \
            -o UserKnownHostsFile="${ALCATRAZ_KNOWN_HOSTS}" \
            -o ConnectTimeout=15 -o BatchMode=yes \
            "${ALCATRAZ_SSH}" \
            "grep -a 'END (success' '${ALCATRAZ_LOG}' 2>/dev/null | tail -1")"
  if [[ -z "${line}" ]]; then
    log "alcatraz-pull: no successful END line found (ssh failed or job never succeeded) — leaving series absent"
    return 1
  fi
  # Extract the ISO8601 UTC timestamp (field 2) and the duration integer.
  ts="$(awk '{print $2}' <<<"${line}")"
  dur="$(sed -n 's/.*(success, \([0-9][0-9]*\)s).*/\1/p' <<<"${line}")"
  epoch="$(date -u -d "${ts}" +%s 2>/dev/null)"
  if [[ -z "${epoch}" || -z "${ts}" ]]; then
    log "alcatraz-pull: could not parse timestamp from: ${line}"
    return 1
  fi
  emit_job "${job}" "${epoch}" "${dur:-0}" 0 "${MAX_AGE_DAILY}"
  log "alcatraz-pull: last success ${ts} (epoch ${epoch}, ${dur:-?}s)"
}

# newest_snapshot_epoch <dataset> <mountpoint> -> echoes epoch, or empty.
newest_snapshot_epoch() {
  local dataset="$1" mountpoint="$2" creation snap name epoch
  # Primary: ask ZFS directly. -Hp = tab-separated, parseable epoch creation;
  # -s creation sorts oldest→newest so the last line is the newest snapshot.
  creation="$(zfs list -t snapshot -Hp -o creation -s creation "${dataset}" 2>/dev/null | tail -1)"
  if [[ -n "${creation}" ]]; then
    echo "${creation}"
    return 0
  fi
  # Fallback: the .zfs/snapshot control directory (no zfs userland needed).
  # Snapshot names sort chronologically (auto-YYYY-MM-DD_HH-MM-...), so the
  # lexically-last name is the newest; parse its embedded timestamp in the
  # schedule's TZ. Brittle to the naming schema — documented assumption.
  if [[ -d "${mountpoint}/.zfs/snapshot" ]]; then
    snap="$(ls -1 "${mountpoint}/.zfs/snapshot" 2>/dev/null | sort | tail -1)"
    if [[ -n "${snap}" ]]; then
      name="$(sed -n 's/^auto-\([0-9-]*\)_\([0-9]*\)-\([0-9]*\).*/\1 \2:\3/p' <<<"${snap}")"
      if [[ -n "${name}" ]]; then
        epoch="$(TZ="${ZFS_SNAP_TZ}" date -d "${name}" +%s 2>/dev/null)"
        [[ -n "${epoch}" ]] && { echo "${epoch}"; return 0; }
      fi
    fi
  fi
  return 1
}

collect_zfs_snapshots() {
  local target dataset job mountpoint epoch
  for target in ${ZFS_TARGETS}; do
    dataset="${target%%:*}"
    job="$(cut -d: -f2 <<<"${target}")"
    mountpoint="${target##*:}"
    epoch="$(newest_snapshot_epoch "${dataset}" "${mountpoint}")"
    if [[ -z "${epoch}" ]]; then
      log "zfs: no snapshot found for ${dataset} (job ${job}) — leaving series absent"
      continue
    fi
    # Snapshots are instantaneous; duration is meaningless → 0.
    emit_job "${job}" "${epoch}" 0 0 "${MAX_AGE_DAILY}"
    log "zfs: ${dataset} newest snapshot epoch ${epoch} (job ${job})"
  done
}

mkdir -p "${TEXTFILE_DIR}"
log "homelabscope-heartbeat starting; interval=${INTERVAL_SECONDS}s textfile=${TEXTFILE_DIR}"
while true; do
  collect_alcatraz_pull || true
  collect_zfs_snapshots || true
  sleep "${INTERVAL_SECONDS}"
done
