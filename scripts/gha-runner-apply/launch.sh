#!/usr/bin/env bash
# launch.sh: start apply.py in a detached sibling container on hestia (HOMELAB-106).
#
# Runs INSIDE a gha-runner job (.github/workflows/gha-runner-apply.yml). The
# runner cannot recreate its own container mid-job, so this script only
# schedules the work: it starts a container that outlives the job, enters
# hestia's host namespaces, and runs apply.py with the host python3. apply.py
# waits for this job to end and the runner to go idle before touching it.
#
# Usage: launch.sh <compose-file> [--dry-run]
#
# Privilege: the sibling is --privileged --pid=host. That adds nothing the job
# does not already hold: the runner bind-mounts /var/run/docker.sock, which is
# host root (see the compose file). It runs the runner's CURRENT image, which
# is always present locally and ships nsenter; nothing is pulled.
#
# No secrets pass through here. apply.py reads the runner's ACCESS_TOKEN from
# the stored TrueNAS app config, in-process, on the host.
set -euo pipefail

COMPOSE_FILE="${1:?usage: launch.sh <compose-file> [--dry-run]}"
DRY_RUN=""
[ "${2:-}" = "--dry-run" ] && DRY_RUN=1

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME=gha-runner-apply

DESIRED_IMAGE="$(python3 "$HERE/apply.py" --print-pin "$COMPOSE_FILE")"
echo "desired runner image: $DESIRED_IMAGE"

RUNNER_ID="$(docker ps -q \
  --filter label=com.docker.compose.project=ix-gha-runner \
  --filter label=com.docker.compose.service=runner | head -n1)"
if [ -z "$RUNNER_ID" ]; then
  echo "::error::runner container not found (label com.docker.compose.project=ix-gha-runner)"
  exit 1
fi
RUNNER_IMAGE_ID="$(docker inspect -f '{{.Image}}' "$RUNNER_ID")"
# Inside a job, apply.py waits for THIS runner container to restart (the
# ephemeral runner exits after every job). A manual run from a hestia shell
# (runbook section 6) has no job to wait for.
TRIGGER_STARTED_AT=""
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  TRIGGER_STARTED_AT="$(docker inspect -f '{{.State.StartedAt}}' "$RUNNER_ID")"
fi

# Single flight: one applier at a time. A running one already reconciles to the
# pin it was given; if the pin moved since, the next scheduled run picks it up.
if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || true)" = "true" ]; then
  echo "an applier is already running; leaving it alone. Its log so far:"
  docker logs --tail 20 "$NAME" 2>&1 || true
  exit 0
fi
if docker inspect "$NAME" >/dev/null 2>&1; then
  # Copy the previous run's log into this job's log before removing it, so the
  # outcome of every apply survives in GitHub Actions history.
  echo "::group::previous applier run (exit $(docker inspect -f '{{.State.ExitCode}}' "$NAME"))"
  docker logs --tail 60 "$NAME" 2>&1 || true
  echo "::endgroup::"
  docker rm "$NAME" >/dev/null
fi

APPLY_PY="$(cat "$HERE/apply.py")"
export APPLY_PY DESIRED_IMAGE TRIGGER_STARTED_AT DRY_RUN

# `-e NAME` (no value) copies the variable from this environment, so values
# never appear on a command line. The script is piped to `python3 -` for the
# same reason: the host process list shows `python3 -`, not the source.
docker run -d --name "$NAME" \
  --label homelab.gjcourt/purpose=gha-runner-apply \
  --privileged --pid=host \
  -e APPLY_PY -e DESIRED_IMAGE -e TRIGGER_STARTED_AT -e DRY_RUN \
  --entrypoint /bin/bash \
  "$RUNNER_IMAGE_ID" \
  -c 'printf "%s" "$APPLY_PY" | exec nsenter -t 1 -m -u -i -n -p -- /usr/bin/python3 -' \
  >/dev/null
echo "started $NAME"

# The converged case finishes in seconds; report it here. Drift cannot finish
# while this job holds the runner, so the applier is still waiting at the end.
for _ in $(seq 1 20); do
  if [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" != "true" ]; then
    code="$(docker inspect -f '{{.State.ExitCode}}' "$NAME")"
    docker logs "$NAME" 2>&1
    echo "applier exited $code"
    exit "$code"
  fi
  sleep 3
done
docker logs "$NAME" 2>&1
echo "applier is waiting for this job to end and the runner to go idle; it will apply then."
echo "Outcome: homelabscope_job_last_status{job=\"gha-runner-image-apply\"}, or the next run of this workflow."
