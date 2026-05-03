#!/usr/bin/env bash
# truenas-update-app.sh — apply a Custom App compose YAML to TrueNAS via the
# WebSocket JSON-RPC API.
#
# Usage:
#   truenas-update-app.sh <app-name> <compose-file> [--dry-run]
#
# Required env:
#   TRUENAS_API_KEY  — API key from SCALE UI → Settings → API Keys.
# Optional env:
#   TRUENAS_HOST     — default: host.docker.internal (set by the runner's compose
#                      via extra_hosts). Override to test from a different host.
#
# --dry-run: connect, authenticate, query the current app, print the compose
# diff, and exit without calling app.update.
#
# Designed to run inside the self-hosted GHA runner on hestia
# (hosts/hestia/actions-runner/), but works from anywhere with network access
# to the TrueNAS API.
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

if [ -z "${TRUENAS_API_KEY:-}" ]; then
  echo "TRUENAS_API_KEY env var is required" >&2
  exit 78
fi

# Install websockets if missing. ubuntu-noble's python3 doesn't include it.
if ! python3 -c "import websockets" 2>/dev/null; then
  echo "+ pip install --quiet --user websockets" >&2
  python3 -m pip install --quiet --user --break-system-packages websockets >&2
fi

export APP_NAME COMPOSE_FILE DRY_RUN
export TRUENAS_HOST="${TRUENAS_HOST:-host.docker.internal}"

exec python3 - <<'PYEOF'
import asyncio
import json
import os
import ssl
import sys

import websockets


APP_NAME = os.environ["APP_NAME"]
COMPOSE_FILE = os.environ["COMPOSE_FILE"]
DRY_RUN = os.environ.get("DRY_RUN") == "--dry-run"
TRUENAS_HOST = os.environ["TRUENAS_HOST"]
TRUENAS_API_KEY = os.environ["TRUENAS_API_KEY"]
URI = f"wss://{TRUENAS_HOST}/api/current"

JOB_POLL_INTERVAL_S = 3
JOB_TIMEOUT_S = 600  # 10 min — `app.update` recreates the container


def log(msg: str) -> None:
    print(msg, flush=True)


async def call(ws, method: str, params, *, msg_id: int):
    await ws.send(json.dumps({
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": params,
    }))
    while True:
        resp = json.loads(await ws.recv())
        if resp.get("id") == msg_id:
            return resp


async def wait_for_job(ws, job_id: int) -> None:
    deadline = asyncio.get_event_loop().time() + JOB_TIMEOUT_S
    last_progress = None
    poll_id = 1000
    while True:
        if asyncio.get_event_loop().time() > deadline:
            sys.exit(f"timed out after {JOB_TIMEOUT_S}s waiting for job {job_id}")
        poll_id += 1
        resp = await call(ws, "core.get_jobs", [[["id", "=", job_id]]], msg_id=poll_id)
        if "error" in resp and resp["error"]:
            sys.exit(f"core.get_jobs failed: {resp['error']}")
        jobs = resp.get("result") or []
        if not jobs:
            await asyncio.sleep(JOB_POLL_INTERVAL_S)
            continue
        job = jobs[0]
        state = job.get("state")
        progress = job.get("progress", {}).get("description") or ""
        if progress and progress != last_progress:
            log(f"  [{state}] {progress}")
            last_progress = progress
        if state == "SUCCESS":
            log(f"job {job_id}: SUCCESS")
            return
        if state in ("FAILED", "ABORTED"):
            err = job.get("error") or job.get("exception") or "(no error message)"
            sys.exit(f"job {job_id}: {state}\n{err}")
        await asyncio.sleep(JOB_POLL_INTERVAL_S)


async def main() -> None:
    with open(COMPOSE_FILE) as f:
        new_yaml = f.read()

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    log(f"connecting to {URI}")
    async with websockets.connect(URI, ssl=ssl_ctx, max_size=10 * 1024 * 1024) as ws:
        # Auth.
        auth = await call(ws, "auth.login_with_api_key", [TRUENAS_API_KEY], msg_id=1)
        if not auth.get("result"):
            sys.exit(f"auth failed: {auth.get('error') or auth}")
        log("authenticated")

        # Confirm the app exists and capture its current compose for diffing.
        query = await call(ws, "app.query", [[["id", "=", APP_NAME]]], msg_id=2)
        result = query.get("result") or []
        if not result:
            sys.exit(f"app '{APP_NAME}' not found on TrueNAS")
        app = result[0]
        if not app.get("custom_app"):
            sys.exit(f"app '{APP_NAME}' is not a Custom App — refusing to update")

        # Compose YAML field name varies across TrueNAS versions. Try the
        # common shapes and report what we find for the operator's benefit.
        current_yaml = (
            app.get("custom_compose_config")
            or app.get("custom_compose_config_string")
            or (app.get("config") or {}).get("custom_compose_config")
            or (app.get("config") or {}).get("custom_compose_config_string")
            or ""
        )

        if not current_yaml:
            log(
                "warning: couldn't locate current compose YAML on the app object; "
                "available top-level keys: " + ", ".join(sorted(app.keys()))
            )

        if current_yaml.strip() == new_yaml.strip():
            log(f"no change for '{APP_NAME}' — current compose matches file. skipping update.")
            return

        log(f"compose change detected for '{APP_NAME}' "
            f"({len(current_yaml)} → {len(new_yaml)} bytes)")

        if DRY_RUN:
            log("--dry-run set; not calling app.update")
            return

        # Apply.
        # app.update(id, config) — the second positional arg maps to the
        # `config` parameter.  custom_compose_config must be a dict with a
        # 'compose' key containing the YAML string (TrueNAS SCALE validation).
        log(f"calling app.update for '{APP_NAME}'")
        update = await call(
            ws,
            "app.update",
            [APP_NAME, {"custom_compose_config": {"compose": new_yaml}}],
            msg_id=3,
        )
        if "error" in update and update["error"]:
            sys.exit(f"app.update failed: {update['error']}")

        # `app.update` returns a job id. Poll until terminal.
        result = update.get("result")
        if isinstance(result, int):
            await wait_for_job(ws, result)
        else:
            log(f"app.update returned non-int result: {result!r} (assuming sync success)")

        log(f"'{APP_NAME}' updated successfully")


asyncio.run(main())
PYEOF
