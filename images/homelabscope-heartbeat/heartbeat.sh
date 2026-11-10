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
# It ALSO carries a second, unrelated metric family: a per-container probe over
# the local Docker socket, emitting homelabscope_container_*. hestia has no
# cAdvisor and no docker exporter, so there is currently no signal at all when a
# hestia container dies or crash-loops -- the blind spot that let the GitHub
# Actions runner sit dead for days in the 2026-08-18 incident. See
# docs/plans/2026-08-18-hestia-deploy-monitoring-gap.md section A for why this
# extends the heartbeat instead of adding a new exporter: the heartbeat is
# already privileged, already scraped, already alerted on, the Grafana dashboard
# is generic over the family, and -- decisively -- the watcher is already
# watched (HomelabscopeJobMetricAbsent guards the ZFS jobs it writes, so a dead
# heartbeat pages on its own). A new exporter would need that liveness story
# built from scratch.
#
# Every source normalizes into the homelabscope metric family and is written to
# the node-exporter textfile collector dir; the hestia node-exporter (:9100)
# serves it and the infra/configs/homelabscope ScrapeConfig scrapes it. Writes
# are atomic (write .tmp, mv) so node-exporter never reads a half-written file.
#
# Runs as a long-lived container looping every INTERVAL_SECONDS so freshness
# stays current between the once-a-day jobs it watches.
#
# It also RELAYS alcatraz's deployer textfile (HOMELAB-118, plan
# docs/plans/2026-11-09-alcatraz-pull-deployer-plan.md A6), off by default
# (ALCATRAZ_DEPLOY_RELAY=1 turns it on). alcatraz has no metrics path, so the
# heartbeat reads its homelabscope-deploy.prom over the same SSH credential and
# re-publishes it here as homelabscope-deploy-alcatraz.prom, but ONLY after
# validate_relay() accepts every line: alcatraz must not be able to write
# hestia-labelled or arbitrary series, or a HELP text that would make
# node-exporter drop hestia's own family. See collect_alcatraz_deploy.

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

# ---- alcatraz deploy relay (HOMELAB-118) -------------------------------------
# Off until George bootstraps alcatraz-deployer (runbook
# docs/operations/apps/alcatraz-deployer.md): the absent() alert arm for
# job="alcatraz-deployer" is gated on homelabscope_relay_enabled, so turning
# this on is also what arms that alert.
ALCATRAZ_DEPLOY_RELAY="${ALCATRAZ_DEPLOY_RELAY:-0}"
ALCATRAZ_DEPLOY_PROM="${ALCATRAZ_DEPLOY_PROM:-/volume1/docker/alcatraz-deployer/metrics/homelabscope-deploy.prom}"
RELAY_MAX_BYTES="${RELAY_MAX_BYTES:-262144}"
RELAY_HOST="alcatraz"
RELAY_JOB="alcatraz-deployer"
RELAY_SOURCE="alcatraz-deploy"
# HELP text per family the relay accepts, byte for byte. These MUST equal what
# images/hestia-deployer/deployer.py renders (JOB_HELP / DEPLOY_HELP): one
# node-exporter serves hestia's and alcatraz's files, and it drops a family
# whose HELP differs between files (PR #1864). A test pins the parity.
declare -A RELAY_HELP=(
  [homelabscope_job_last_success_seconds]="Unix timestamp of the job's last successful run."
  [homelabscope_job_last_duration_seconds]="Wall-clock duration of the job's last run (seconds)."
  [homelabscope_job_last_status]="Last run status (0=ok, 1=fail)."
  [homelabscope_job_max_age_seconds]="Staleness budget for this job (seconds)."
  [homelabscope_deploy_head_info]="master commit the last deployer tick evaluated."
  [homelabscope_deploy_head_commit_seconds]="Commit time of the master commit the last deployer tick evaluated."
  [homelabscope_deploy_app_state]="Deployer state of each app on the host (1 for the current state)."
  [homelabscope_deploy_app_applied_info]="Last commit the deployer successfully applied to the app."
  [homelabscope_deploy_app_last_apply_seconds]="Unix timestamp of the deployer's last successful apply of the app."
  [homelabscope_deploy_paused]="1 when the deployer is paused (scope=global: PAUSE file; otherwise x-deploy.paused)."
  [homelabscope_deploy_unmanaged_apps]="Count of apps on the host with no compose file in Git."
  [homelabscope_deploy_dry_run]="1 when the deployer runs in dry-run (shadow) mode and applies nothing."
)
# A sample's value, and the only label shapes allowed after host= on a deploy
# sample. Label values: lowercase/uppercase alnum and . _ - only (app names,
# states, commit hex, "global"): no quotes, so no label injection.
RELAY_NUM='-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?'
RELAY_DEPLOY_RE="^(homelabscope_deploy_[a-z_]+)\{host=\"${RELAY_HOST}\"(,(commit|app|state|scope)=\"[A-Za-z0-9._-]*\")*\} ${RELAY_NUM}\$"
RELAY_JOB_RE="^(homelabscope_job_[a-z_]+)\{job=\"${RELAY_JOB}\"\} ${RELAY_NUM}\$"
RELAY_HELP_RE='^# HELP ([a-z_]+) (.*)$'
RELAY_TYPE_RE='^# TYPE ([a-z_]+) gauge$'
RELAY_LAST_STATUS=""
RELAY_LAST_SUCCESS=""
# ----------------------------------------------------------------------------

# ---- Container probe (Docker socket) ---------------------------------------
# Polled far more often than the SSH/ZFS jobs above: it is a handful of local
# unix-socket calls, and a tight interval is what makes State.ExitCode a usable
# signal (see collect_containers). The slow jobs still run on INTERVAL_SECONDS.
CONTAINER_PROBE_ENABLED="${CONTAINER_PROBE_ENABLED:-1}"
CONTAINER_INTERVAL_SECONDS="${CONTAINER_INTERVAL_SECONDS:-60}"
DOCKER_SOCK="${DOCKER_SOCK:-/var/run/docker.sock}"
# Optional ERE of container names to skip. Default empty = report every
# container the daemon knows about, running or not. Exists so the operator can
# suppress churn (e.g. buildx builder containers the runner spawns through the
# socket it already mounts) without rebuilding the image.
CONTAINER_EXCLUDE_REGEX="${CONTAINER_EXCLUDE_REGEX:-}"
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

# validate_relay <file>: 0 if every line is one the relay may publish.
# On the first bad line prints "line N: <why>" to stderr (never the line: it is
# untrusted input) and returns 1. Blank lines are allowed.
validate_relay() {
  local f="$1" n=0 line name size
  size="$(wc -c < "${f}")" || return 1
  if (( size > RELAY_MAX_BYTES )); then
    echo "file larger than ${RELAY_MAX_BYTES} bytes" >&2
    return 1
  fi
  while IFS= read -r line || [[ -n "${line}" ]]; do
    n=$((n + 1))
    [[ -z "${line}" ]] && continue
    if [[ "${line}" =~ ${RELAY_HELP_RE} ]]; then
      name="${BASH_REMATCH[1]}"
      if [[ -z "${RELAY_HELP[${name}]+x}" || "${BASH_REMATCH[2]}" != "${RELAY_HELP[${name}]}" ]]; then
        echo "line ${n}: HELP for an unknown family or with unexpected text" >&2
        return 1
      fi
    elif [[ "${line}" =~ ${RELAY_TYPE_RE} ]]; then
      if [[ -z "${RELAY_HELP[${BASH_REMATCH[1]}]+x}" ]]; then
        echo "line ${n}: TYPE for an unknown family" >&2
        return 1
      fi
    elif [[ "${line}" =~ ${RELAY_DEPLOY_RE} || "${line}" =~ ${RELAY_JOB_RE} ]]; then
      if [[ -z "${RELAY_HELP[${BASH_REMATCH[1]}]+x}" ]]; then
        echo "line ${n}: sample of an unknown family" >&2
        return 1
      fi
    else
      echo "line ${n}: not a line the relay accepts" >&2
      return 1
    fi
  done < "${f}"
  return 0
}

# emit_relay_status <status 0|1|2>: homelabscope_relay_* for the alcatraz
# relay (0=accepted, 1=rejected by validation, 2=fetch failed). The cutover's
# HomelabDeployRelayRejected alert reads last_status == 1.
emit_relay_status() {
  local status="$1" out="${TEXTFILE_DIR}/homelabscope-relay.prom"
  {
    echo "# HELP homelabscope_relay_enabled 1 when the heartbeat relays another host's deployer textfile."
    echo "# TYPE homelabscope_relay_enabled gauge"
    echo "homelabscope_relay_enabled{source=\"${RELAY_SOURCE}\"} 1"
    echo "# HELP homelabscope_relay_last_status Last relay attempt (0=accepted, 1=rejected by validation, 2=fetch failed)."
    echo "# TYPE homelabscope_relay_last_status gauge"
    echo "homelabscope_relay_last_status{source=\"${RELAY_SOURCE}\"} ${status}"
    if [[ -n "${RELAY_LAST_SUCCESS}" ]]; then
      echo "# HELP homelabscope_relay_last_success_seconds Unix timestamp of the last accepted relay."
      echo "# TYPE homelabscope_relay_last_success_seconds gauge"
      echo "homelabscope_relay_last_success_seconds{source=\"${RELAY_SOURCE}\"} ${RELAY_LAST_SUCCESS}"
    fi
  } > "${out}.tmp"
  mv "${out}.tmp" "${out}"
}

# relay_fetch <dest>: the remote textfile, at most RELAY_MAX_BYTES+1 bytes.
relay_fetch() {
  ssh -T -x -i "${ALCATRAZ_KEY}" \
      -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="${ALCATRAZ_KNOWN_HOSTS}" \
      -o ConnectTimeout=15 -o BatchMode=yes \
      "${ALCATRAZ_SSH}" \
      "head -c $((RELAY_MAX_BYTES + 1)) -- '${ALCATRAZ_DEPLOY_PROM}'" > "$1"
}

# collect_alcatraz_deploy: fetch, validate, publish atomically. A failed fetch
# or a rejected file leaves the LAST accepted file in place, so
# homelabscope_job_last_success_seconds{job="alcatraz-deployer"} ages and
# HomelabscopeJobStale fires, rather than the series going absent.
collect_alcatraz_deploy() {
  [[ "${ALCATRAZ_DEPLOY_RELAY}" == "1" ]] || return 0
  local out="${TEXTFILE_DIR}/homelabscope-deploy-${RELAY_HOST}.prom" tmp status why
  # Not *.prom: node-exporter never reads the unvalidated copy.
  tmp="$(mktemp "${TEXTFILE_DIR}/.relay-${RELAY_HOST}.XXXXXX")" || return 1
  if ! relay_fetch "${tmp}" 2>/dev/null; then
    status=2
  elif ! why="$(validate_relay "${tmp}" 2>&1)"; then
    status=1
  else
    chmod 0644 "${tmp}"
    mv "${tmp}" "${out}"
    status=0
    RELAY_LAST_SUCCESS="$(date +%s)"
  fi
  rm -f "${tmp}"
  if [[ "${status}" != "${RELAY_LAST_STATUS}" ]]; then
    case "${status}" in
      0) log "relay ${RELAY_SOURCE}: accepted" ;;
      1) log "relay ${RELAY_SOURCE}: REJECTED (${why}); keeping the last accepted file" ;;
      2) log "relay ${RELAY_SOURCE}: fetch failed (ssh, or no file yet); keeping the last accepted file" ;;
    esac
    RELAY_LAST_STATUS="${status}"
  fi
  emit_relay_status "${status}"
  [[ "${status}" == "0" ]]
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

# ---------------------------------------------------------------------------
# Container probe -- homelabscope_container_* over the Docker socket.
#
# Deliberately generic over EVERY container the daemon knows about (running or
# exited), not a curated watchlist: the plan's open question about a "must be
# running" list is answered by the alert side, not here. `x-deploy.archived` is
# known-untrustworthy as a should-be-running source (thermalscope is archived in
# the repo and up=1 in reality), so the collector reports what IS and lets the
# PrometheusRule decide what MUST be.
#
# WHAT WE CAN SEE, AND WHAT WE CANNOT
#
# The runner container is EPHEMERAL. Its image entrypoint tests `[ -n "$EPHEMERAL" ]`,
# so the compose's `EPHEMERAL: "false"` -- a non-empty string -- still activates
# `--ephemeral`. The runner therefore completes ONE job, deregisters, exits 0,
# and is restarted by `unless-stopped`: roughly one restart PER JOB. RestartCount
# was observed going 0 -> 9 across a single 10-job deploy.
#
# That breaks a naive restart-rate alert. `increase(restarts_total[1h]) > 5` --
# the threshold the plan sketches -- is tripped by a perfectly healthy busy
# afternoon. Restart rate alone cannot tell "restarting and doing work" from
# "restarting and completing nothing" (the 11894-restart crash loop).
#
# A true completed-jobs counter is NOT cheaply observable from polled Docker
# state, and this collector does not fake one. `docker inspect` exposes only the
# LAST exit -- .State.ExitCode and .State.FinishedAt -- not a history. Between
# two polls N restarts may have occurred with mixed outcomes, and inventing a
# per-poll clean-exit counter from a single observed exit code would attribute
# all N to the last one. The only exact source is the `/events` stream
# (`die` carries an exitCode attribute), which means a second long-lived
# streaming process and cross-restart counter persistence -- out of scope here,
# and recorded as the follow-up option if exit code proves insufficient.
#
# What we emit instead are two EXACT, non-fabricated observations that let an
# alert make the distinction:
#
#   homelabscope_container_last_exit_code  .State.ExitCode -- how the previous
#       instance of this container ended. An ephemeral runner that finished a
#       job reads 0; a runner dying on "version deprecated" reads non-zero. So
#       "crash-looping" is `restarts high AND last_exit_code != 0`, and a busy
#       deploy is `restarts high AND last_exit_code == 0`.
#
#   homelabscope_container_started_seconds  .State.StartedAt as an epoch --
#       how long the CURRENT instance has been up. A container doing work runs
#       for the length of a job (minutes); a crash loop turns over in seconds.
#       `time() - started_seconds` is the flap detector, independent of exit
#       codes entirely, and it needs no history.
#
# Both are point-in-time samples, hence CONTAINER_INTERVAL_SECONDS=60 rather
# than the 600s the SSH/ZFS jobs use: at 60s a container restarting every few
# seconds is caught mid-failure on essentially every sample, while a multi-minute
# job is not misread as a flap.
#
# homelabscope_container_restarts_total is Docker's own RestartCount, which is
# monotonic per container and resets only when the container is recreated --
# a genuine counter, safe under increase()/rate(). It is also already the
# "starts" signal: starts = restarts + 1 for a given container instance, so a
# separate homelabscope_container_starts_total series would carry no
# information RestartCount does not already carry.
# ---------------------------------------------------------------------------

# docker_api <path> -- GET against the Docker Engine API over the unix socket.
# Unversioned path: the daemon answers with its own current API version, so this
# cannot break on a TrueNAS Docker upgrade the way a pinned /v1.41 prefix would.
docker_api() {
  curl -sS --fail --max-time 10 --unix-socket "${DOCKER_SOCK}" "http://localhost$1"
}

# Encoding for homelabscope_container_health. Emitted ONLY for containers that
# actually declare a healthcheck -- a container with none has no health, and
# emitting 0 ("unhealthy") for it would make every unmonitored container look
# broken.
#   1 = healthy   0 = unhealthy   2 = starting (inside start_period)
health_value() {
  case "$1" in
    healthy)   echo 1 ;;
    unhealthy) echo 0 ;;
    starting)  echo 2 ;;
    *)         return 1 ;;
  esac
}

CONTAINER_PROBE_LAST_OK=""

# emit_family <name> <type> <help> <samples>
# Prints a HELP/TYPE header plus its samples, and prints NOTHING when there are
# no samples: a bare TYPE line with no series is a needless edge case for the
# textfile parser, and samples are grouped per family (rather than interleaved
# per container) because that is the canonical exposition layout.
emit_family() {
  local name="$1" type="$2" help="$3" samples="$4"
  [[ -z "${samples}" ]] && return 0
  echo "# HELP ${name} ${help}"
  echo "# TYPE ${name} ${type}"
  printf '%s' "${samples}"
}

collect_containers() {
  local out="${TEXTFILE_DIR}/homelabscope-containers.prom"
  local ids id json probe_ok=1 name running restarts health started exitcode
  local hv epoch
  local s_running="" s_restarts="" s_exit="" s_started="" s_health=""

  # An empty container list is a successful probe with zero series, not a
  # failure — on this host that state is itself the alarm, and the absent()
  # guard is what says so.
  ids="$(docker_api '/containers/json?all=1' 2>/dev/null | jq -r '.[].Id' 2>/dev/null)" || probe_ok=0

  if [[ "${probe_ok}" -eq 1 ]]; then
    for id in ${ids}; do
      json="$(docker_api "/containers/${id}/json" 2>/dev/null)" || continue
      IFS=$'\t' read -r name running restarts health started exitcode < <(
        jq -r '[
          (.Name // "" | ltrimstr("/")),
          (if .State.Running then 1 else 0 end),
          (.RestartCount // 0),
          (.State.Health.Status // "none"),
          (.State.StartedAt // ""),
          (.State.ExitCode // 0)
        ] | @tsv' <<<"${json}" 2>/dev/null
      )
      [[ -z "${name}" ]] && continue
      if [[ -n "${CONTAINER_EXCLUDE_REGEX}" ]] && [[ "${name}" =~ ${CONTAINER_EXCLUDE_REGEX} ]]; then
        continue
      fi

      s_running+="homelabscope_container_running{name=\"${name}\"} ${running}"$'\n'
      s_restarts+="homelabscope_container_restarts_total{name=\"${name}\"} ${restarts}"$'\n'
      s_exit+="homelabscope_container_last_exit_code{name=\"${name}\"} ${exitcode}"$'\n'

      # Docker reports StartedAt as 0001-01-01T00:00:00Z for a container that
      # has never run -- not a real epoch, so leave the series absent.
      if [[ -n "${started}" && "${started}" != 0001-* ]]; then
        epoch="$(date -u -d "${started}" +%s 2>/dev/null)"
        [[ -n "${epoch}" ]] && \
          s_started+="homelabscope_container_started_seconds{name=\"${name}\"} ${epoch}"$'\n'
      fi

      if hv="$(health_value "${health}")"; then
        s_health+="homelabscope_container_health{name=\"${name}\"} ${hv}"$'\n'
      fi
    done
  fi

  # Log only on transition so a persistent failure doesn't flood the log.
  if [[ "${probe_ok}" != "${CONTAINER_PROBE_LAST_OK}" ]]; then
    if [[ "${probe_ok}" -eq 1 ]]; then
      log "container-probe: docker socket reachable at ${DOCKER_SOCK}"
    else
      log "container-probe: cannot reach docker socket ${DOCKER_SOCK} — emitting probe_success 0"
    fi
    CONTAINER_PROBE_LAST_OK="${probe_ok}"
  fi

  # Atomic write, same discipline as emit_job: node-exporter must never read a
  # half-written file. Written unconditionally, including on probe failure, so
  # probe_success tells "docker is unreachable" apart from "every container is
  # gone" — two states that would otherwise look identical (both drop the
  # per-container series).
  {
    echo "# HELP homelabscope_container_probe_success Whether the last Docker socket probe succeeded (1=yes, 0=no)."
    echo "# TYPE homelabscope_container_probe_success gauge"
    echo "homelabscope_container_probe_success ${probe_ok}"
    emit_family homelabscope_container_running gauge \
      "Whether the container is currently running (1=yes, 0=no)." "${s_running}"
    emit_family homelabscope_container_restarts_total counter \
      "Docker RestartCount for the container (monotonic; resets on recreate)." "${s_restarts}"
    emit_family homelabscope_container_last_exit_code gauge \
      "Exit code of the container's previous instance (0 while it has never exited)." "${s_exit}"
    emit_family homelabscope_container_started_seconds gauge \
      "Unix timestamp the current container instance started." "${s_started}"
    emit_family homelabscope_container_health gauge \
      "Docker healthcheck state (1=healthy, 0=unhealthy, 2=starting); absent when the container declares no healthcheck." "${s_health}"
  } > "${out}.tmp"
  mv "${out}.tmp" "${out}"
}

main() {
mkdir -p "${TEXTFILE_DIR}"
log "homelabscope-heartbeat starting; interval=${INTERVAL_SECONDS}s textfile=${TEXTFILE_DIR}"
log "container-probe: enabled=${CONTAINER_PROBE_ENABLED} interval=${CONTAINER_INTERVAL_SECONDS}s sock=${DOCKER_SOCK}"
log "alcatraz deploy relay: enabled=${ALCATRAZ_DEPLOY_RELAY}"

# Two cadences in one loop: the SSH/ZFS jobs watch once-a-day work and stay on
# INTERVAL_SECONDS; the container probe is local socket calls and runs on the
# much tighter CONTAINER_INTERVAL_SECONDS (see the comment block above for why
# the tighter cadence is load-bearing, not cosmetic). last_slow=0 forces the
# slow jobs to run on the first pass.
last_slow=0
while true; do
  now="$(date +%s)"
  if (( now - last_slow >= INTERVAL_SECONDS )); then
    collect_alcatraz_pull || true
    collect_zfs_snapshots || true
    collect_alcatraz_deploy || true
    last_slow="${now}"
  fi
  if [[ "${CONTAINER_PROBE_ENABLED}" == "1" ]]; then
    collect_containers || true
  fi
  sleep "${CONTAINER_INTERVAL_SECONDS}"
done
}

# Sourcing the file (the relay tests do) defines the functions only.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main
fi
