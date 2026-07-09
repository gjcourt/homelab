#!/usr/bin/env bash
# alcatraz-deploy-compose.sh — apply one Synology (alcatraz) compose file by
# driving the local Docker Engine directly.
#
# Usage:
#   alcatraz-deploy-compose.sh <app-name> <compose-file> [--dry-run]
#
# The alcatraz analogue of scripts/truenas-update-app.sh: where hestia delegates
# container lifecycle to the TrueNAS `app.update` WebSocket API, Synology has no
# such API, so this wrapper runs `docker compose ... up -d` against the Docker
# socket bind-mounted into the self-hosted runner
# (hosts/alcatraz/actions-runner/). Called per matrix entry by
# .github/workflows/alcatraz-deploy.yaml.
#
# --dry-run: validate + render the merged config only (no pull, no up) — parity
# with truenas-update-app.sh's --dry-run.
#
# Designed to run inside the alcatraz self-hosted GHA runner, but works anywhere
# with the compose file + a reachable Docker Engine.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <app-name> <compose-file> [--dry-run]" >&2
  exit 64
fi

APP_NAME="$1"
COMPOSE_FILE="$2"
DRY_RUN="${3:-}"

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "compose file not found: $COMPOSE_FILE" >&2
  exit 66
fi

# Preflight: the apply runs `docker compose` INSIDE the runner container, and
# myoung34/github-runner omits the Compose plugin on arm64 (plan gotcha). Fail
# loudly and early with a clear message rather than a cryptic "compose: not
# found" mid-deploy.
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: 'docker compose' (Compose v2 plugin) is not available in this runner." >&2
  echo "       On aarch64 the stock myoung34/github-runner image omits it — build a" >&2
  echo "       derived image with docker-compose-plugin. See" >&2
  echo "       hosts/alcatraz/actions-runner/README.md (Architecture caveats)." >&2
  exit 69
fi

# Project name must be Compose-safe: lowercase, starts with a letter/number,
# then [a-z0-9_-]. The workflow's filename→name derivation already yields
# kebab-case, but sanitise defensively and FAIL on an invalid name rather than
# silently deploying under a different project (which would orphan containers).
NAME="$(printf '%s' "$APP_NAME" | tr '[:upper:]' '[:lower:]')"
if ! printf '%s' "$NAME" | grep -Eq '^[a-z0-9][a-z0-9_-]*$'; then
  echo "ERROR: invalid Compose project name derived from '$APP_NAME': '$NAME'" >&2
  echo "       Must match ^[a-z0-9][a-z0-9_-]*\$ (lowercase DNS-safe)." >&2
  exit 65
fi

echo "==> app=$NAME file=$COMPOSE_FILE dry_run=${DRY_RUN:-false}"

# Validate + render the merged config first (also catches YAML/interpolation
# errors before anything is created).
echo "--- docker compose config ---"
docker compose -f "$COMPOSE_FILE" -p "$NAME" config

if [ "$DRY_RUN" = "--dry-run" ]; then
  echo "==> dry-run: stopping after config render (no pull, no up)."
  exit 0
fi

# Pull first so an image with no tag/digest for alcatraz's CPU arch fails HERE
# with a clear error, before `up -d` half-applies.
echo "--- docker compose pull ---"
docker compose -f "$COMPOSE_FILE" -p "$NAME" pull

# Reconcile. `up -d` CREATES the project if absent (no manual first-paste, unlike
# hestia) and is a no-op when nothing changed. --remove-orphans cleans up
# containers dropped from the compose.
echo "--- docker compose up -d --remove-orphans ---"
docker compose -f "$COMPOSE_FILE" -p "$NAME" up -d --remove-orphans

echo "--- docker compose ps ---"
docker compose -f "$COMPOSE_FILE" -p "$NAME" ps

echo "==> $NAME applied."
