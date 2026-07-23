#!/bin/bash
# Mirror ALL of a GitHub account's repos (every branch + tag) into $DEST as bare
# `git clone --mirror` clones; refresh with `git remote update --prune` on later
# runs (picks up new/changed refs, drops deleted branches). Private repos are
# included — the token owns them.
#
# Token handling: read from a mounted file (never baked into the image) and
# passed per-command via `http.extraHeader`, so it is NEVER persisted into any
# repo's git config on disk (the stored remote URL is token-less).
#
# Emits homelabscope textfile metrics (infra/configs/homelabscope/) so the
# generic HomelabscopeJobStale / …MetricAbsent alerts cover this job for free.
set -uo pipefail

GITHUB_USER="${GITHUB_USER:-gjcourt}"
DEST="${DEST:-/mirror}"
TOKEN_FILE="${TOKEN_FILE:-/run/secrets/github-token}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node-exporter/textfile}"
TEXTFILE="${TEXTFILE_DIR}/github-mirror.prom"
MAX_AGE_SECONDS="${MAX_AGE_SECONDS:-172800}" # 48h staleness budget (daily job + slack)

log() { echo "[$(date -Is)] $*"; }

start_ts=$(date +%s)
log "=== github-mirror START (user=${GITHUB_USER}, dest=${DEST}) ==="

if [ ! -r "${TOKEN_FILE}" ]; then
  log "FATAL: token file ${TOKEN_FILE} not readable"
  exit 1
fi
token="$(tr -d '[:space:]' <"${TOKEN_FILE}")"
auth="Authorization: Bearer ${token}"

mkdir -p "${DEST}"

# List every repo the token owns (private included), paginated 100/page.
repos=""
page=1
while :; do
  resp="$(curl -fsS -H "${auth}" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/user/repos?per_page=100&page=${page}&affiliation=owner&sort=full_name")" || {
    log "FATAL: GitHub API listing failed on page ${page}"
    exit 1
  }
  count="$(printf '%s' "${resp}" | jq 'length')"
  [ "${count}" -eq 0 ] && break
  repos="${repos}$(printf '%s' "${resp}" | jq -r '.[].full_name')
"
  page=$((page + 1))
done
total="$(printf '%s\n' "${repos}" | grep -c . || true)"
log "found ${total} repos"

ok=0
fail=0
while IFS= read -r full; do
  [ -n "${full}" ] || continue
  name="${full#*/}"
  dir="${DEST}/${name}.git"
  if [ -d "${dir}" ]; then
    if git --git-dir="${dir}" -c http.extraHeader="${auth}" remote update --prune >/dev/null 2>&1; then
      ok=$((ok + 1))
      log "updated  ${full}"
    else
      fail=$((fail + 1))
      log "FAILED   update ${full}"
    fi
  else
    # Token-less URL is what gets stored; the header authenticates this command only.
    if git -c http.extraHeader="${auth}" clone --mirror "https://github.com/${full}.git" "${dir}" >/dev/null 2>&1; then
      ok=$((ok + 1))
      log "cloned   ${full}"
    else
      fail=$((fail + 1))
      log "FAILED   clone ${full}"
    fi
  fi
done <<EOF
${repos}
EOF

end_ts=$(date +%s)
dur=$((end_ts - start_ts))
status=0
[ "${fail}" -gt 0 ] && status=1
log "=== DONE: ${ok} ok, ${fail} failed, ${dur}s ==="

# homelabscope textfile metrics — best-effort. last_success updates on every
# COMPLETED run (a few per-repo failures shouldn't trip the "job hasn't run"
# staleness alert); last_status carries the fail signal separately. A read-only
# collector dir must never fail the backup, hence the guards.
if mkdir -p "${TEXTFILE_DIR}" 2>/dev/null; then
  tmp="${TEXTFILE}.$$"
  if {
    echo "# HELP github_mirror_repos_total Repos discovered this run."
    echo "# TYPE github_mirror_repos_total gauge"
    echo "github_mirror_repos_total ${total}"
    echo "# HELP github_mirror_repos_failed Repos that failed this run."
    echo "# TYPE github_mirror_repos_failed gauge"
    echo "github_mirror_repos_failed ${fail}"
    echo "# HELP homelabscope_job_last_success_seconds Unix timestamp of the job's last successful run."
    echo "# TYPE homelabscope_job_last_success_seconds gauge"
    echo "homelabscope_job_last_success_seconds{job=\"github-mirror\"} ${end_ts}"
    echo "# HELP homelabscope_job_last_duration_seconds Wall-clock duration of the job's last run (seconds)."
    echo "# TYPE homelabscope_job_last_duration_seconds gauge"
    echo "homelabscope_job_last_duration_seconds{job=\"github-mirror\"} ${dur}"
    echo "# HELP homelabscope_job_last_status Last run status (0=ok, 1=fail)."
    echo "# TYPE homelabscope_job_last_status gauge"
    echo "homelabscope_job_last_status{job=\"github-mirror\"} ${status}"
    echo "# HELP homelabscope_job_max_age_seconds Per-job staleness budget (seconds)."
    echo "# TYPE homelabscope_job_max_age_seconds gauge"
    echo "homelabscope_job_max_age_seconds{job=\"github-mirror\"} ${MAX_AGE_SECONDS}"
  } >"${tmp}" 2>/dev/null; then
    mv "${tmp}" "${TEXTFILE}" 2>/dev/null || log "note: could not move metric into ${TEXTFILE}"
  else
    rm -f "${tmp}" 2>/dev/null || true
    log "note: could not write ${TEXTFILE}"
  fi
fi

# Exit 0 on partial failures (the loop + staleness metric handle those); reserve
# non-zero for FATAL conditions handled above.
exit 0
