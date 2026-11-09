#!/usr/bin/env python3
"""gha-runner-apply: reconcile the hestia runner's image to the Git pin (HOMELAB-106).

The self-hosted runner on hestia cannot redeploy itself mid-job, so
deploy-hestia.yml excludes hosts/hestia/actions-runner/. Without this script a
Renovate digest bump sits unapplied in Git until GitHub retires the running
runner version and the runner stops taking jobs (the 2026-08-18 outage).

HOW IT RUNS. .github/workflows/gha-runner-apply.yml runs on the hestia runner,
reads the pin from the compose file, and starts this script in a DETACHED
sibling container (via the runner's docker socket) that outlives the job. The
sibling nsenters into hestia's host namespaces and runs this file with the
host's python3, which ships `truenas_api_client`. So the script talks to the
TrueNAS middleware as root over its local socket, exactly like the manual
method in docs/operations/apps/gha-runner.md section 6, and needs no API key or
token passed in. The GitHub token it uses for the runners API is the runner's
own ACCESS_TOKEN, read from the stored app config in-process and never printed.

WHAT IT DOES (idempotent; every step re-reads live state):
  1. Converged (stored config AND running container both on the pin) -> write
     a success heartbeat and exit 0. This is the common case, every 6h.
  2. Drift -> wait for the job that launched us to end (the ephemeral runner
     container restarts after every job), then wait until the runner is idle:
     GitHub says online and not busy, AND no Runner.Worker process in the
     container, on two samples IDLE_SAMPLE_GAP_S apart.
  3. app.stop -> app.update (stored config, ONLY the image changed, so the
     masked ACCESS_TOKEN / TRUENAS_API_KEY are preserved) -> app.start. The
     stop first sidesteps the app.update no-op quirk (runbook section 7).
  4. Verify the new container runs the pin and the runner is back online on
     GitHub. Success -> heartbeat; failure -> last_status=1, which pages via
     HomelabscopeGhaRunnerApplyFailed.

Only the image is reconciled. Any other compose edit under
hosts/hestia/actions-runner/ is still a manual apply (runbook section 6).

Exit codes: 0 converged/applied, 1 failed (metric written), 2 bad input,
3 deferred (runner never idle before the deadline; next run retries).

Local, non-host use: `apply.py --print-pin <compose>` prints and validates the
pin. That path imports nothing TrueNAS-specific, so CI and the runner job can
use it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

APP = "gha-runner"
SERVICE = "runner"
REPO = "gjcourt/homelab"
RUNNER_NAME = "hestia"
COMPOSE_PROJECT = f"ix-{APP}"
JOB = "gha-runner-image-apply"

TEXTFILE = os.environ.get(
    "TEXTFILE", "/var/lib/node-exporter/textfile/homelabscope-gha-runner-apply.prom"
)
# 6-hourly schedule -> 14h budget, the same tier as the other 6-hour jobs in
# infra/configs/homelabscope/prometheus-rule.yaml (two missed runs + slack).
MAX_AGE_SECONDS = 50400

TRIGGER_TIMEOUT_S = int(os.environ.get("TRIGGER_TIMEOUT_S", "1800"))
IDLE_TIMEOUT_S = int(os.environ.get("IDLE_TIMEOUT_S", str(5 * 3600)))
IDLE_POLL_S = int(os.environ.get("IDLE_POLL_S", "20"))
IDLE_SAMPLE_GAP_S = int(os.environ.get("IDLE_SAMPLE_GAP_S", "10"))
VERIFY_TIMEOUT_S = int(os.environ.get("VERIFY_TIMEOUT_S", "600"))

PIN_RE = re.compile(r"^myoung34/github-runner:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")
IMAGE_LINE_RE = re.compile(r"^\s+image:\s*[\"']?([^\"'\s#]+)[\"']?\s*(?:#.*)?$")

# HELP strings MUST match the other homelabscope textfile writers byte for byte
# (images/homelabscope-heartbeat, images/immich-photos-backup,
# images/github-mirror). node-exporter drops a metric family from every file
# whose HELP differs from the first file it read (PR #1864).
HELP = {
    "homelabscope_job_last_success_seconds": "Unix timestamp of the job's last successful run.",
    "homelabscope_job_last_duration_seconds": "Wall-clock duration of the job's last run (seconds).",
    "homelabscope_job_last_status": "Last run status (0=ok, 1=fail).",
    "homelabscope_job_max_age_seconds": "Staleness budget for this job (seconds).",
}


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}", flush=True)


# ---------------------------------------------------------------- pure logic


def parse_pin(compose_text: str) -> str:
    """Return the runner image pin from the compose text, validated."""
    for line in compose_text.splitlines():
        m = IMAGE_LINE_RE.match(line)
        if m:
            pin = m.group(1)
            if not PIN_RE.match(pin):
                raise ValueError(f"image pin is not myoung34/github-runner:<tag>@sha256:<digest>: {pin!r}")
            return pin
    raise ValueError("no image: line found in compose file")


def plan(config_image: str, running_image: str | None, desired: str) -> str:
    """'converged' only when the stored config AND the live container match.

    A matching config with a stale container is the app.update no-op quirk
    (runbook section 7); treat it as drift so the stop/update/start recreates it.
    """
    if config_image == desired and running_image == desired:
        return "converged"
    return "apply"


def runner_state(runners: list[dict], name: str = RUNNER_NAME) -> tuple[bool, bool]:
    """(online, busy) for the named runner across its registrations."""
    mine = [r for r in runners if r.get("name") == name]
    online = any(r.get("status") == "online" for r in mine)
    busy = any(r.get("busy") for r in mine)
    return online, busy


def is_idle(runners: list[dict], worker_running: bool) -> bool:
    online, busy = runner_state(runners)
    return online and not busy and not worker_running


def previous_success(text: str) -> int | None:
    m = re.search(
        r'^homelabscope_job_last_success_seconds\{job="' + re.escape(JOB) + r'"\}\s+(\d+)',
        text,
        re.M,
    )
    return int(m.group(1)) if m else None


def render_metrics(last_success: int | None, status: int, duration: int) -> str:
    values = {
        "homelabscope_job_last_success_seconds": last_success,
        "homelabscope_job_last_duration_seconds": duration,
        "homelabscope_job_last_status": status,
        "homelabscope_job_max_age_seconds": MAX_AGE_SECONDS,
    }
    out = []
    for name, value in values.items():
        if value is None:
            # Never fabricate a success timestamp: on a first-ever failure the
            # series is simply absent, and last_status=1 carries the signal.
            continue
        out.append(f"# HELP {name} {HELP[name]}")
        out.append(f"# TYPE {name} gauge")
        out.append(f'{name}{{job="{JOB}"}} {value}')
    return "\n".join(out) + "\n"


# ------------------------------------------------------------ host side I/O


def write_metrics(ok: bool, started: float, dry_run: bool) -> None:
    now = int(time.time())
    prev = None
    try:
        with open(TEXTFILE) as f:
            prev = previous_success(f.read())
    except OSError:
        pass
    body = render_metrics(now if ok else prev, 0 if ok else 1, now - int(started))
    if dry_run:
        log(f"dry-run: would write {TEXTFILE} (status={0 if ok else 1})")
        return
    tmp = f"{TEXTFILE}.{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            f.write(body)
        os.chmod(tmp, 0o644)
        os.replace(tmp, TEXTFILE)
    except OSError as e:
        log(f"warning: could not write {TEXTFILE}: {e}")


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


def runner_container() -> str | None:
    out = docker(
        "ps", "-a", "-q",
        "--filter", f"label=com.docker.compose.project={COMPOSE_PROJECT}",
        "--filter", f"label=com.docker.compose.service={SERVICE}",
    )
    ids = out.split()
    return ids[0] if ids else None


def container_info(cid: str | None) -> dict:
    if not cid:
        return {}
    try:
        data = json.loads(docker("inspect", cid))[0]
    except (subprocess.CalledProcessError, ValueError, IndexError):
        return {}
    return {
        "image": data["Config"]["Image"],
        "running": data["State"]["Running"],
        "restarting": data["State"]["Restarting"],
        "started_at": data["State"]["StartedAt"],
    }


def worker_running(cid: str | None) -> bool:
    """True if a job is executing, or if we cannot tell (container restarting)."""
    if not cid:
        return True
    try:
        return "Runner.Worker" in docker("top", cid)
    except subprocess.CalledProcessError:
        return True


def runner_version(cid: str | None) -> str:
    if not cid:
        return "?"
    try:
        logs = subprocess.run(
            ["docker", "logs", "--tail", "50", cid], capture_output=True, text=True, check=False
        )
        m = re.findall(r"Current runner version: '([^']+)'", logs.stdout + logs.stderr)
        return m[-1] if m else "?"
    except OSError:
        return "?"


def github_runners(token: str) -> list[dict]:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/runners?per_page=100",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "homelab-gha-runner-apply",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp).get("runners", [])


def safe_runners(token: str) -> list[dict]:
    try:
        return github_runners(token)
    except Exception as e:  # noqa: BLE001 - network blips must not abort a wait loop
        # The exception text never contains the token (it is a header, not URL).
        log(f"github runners API error: {type(e).__name__}: {e}")
        return []


def wait_for_trigger_end(trigger_started_at: str) -> None:
    if not trigger_started_at:
        return
    deadline = time.time() + TRIGGER_TIMEOUT_S
    while time.time() < deadline:
        info = container_info(runner_container())
        if info.get("started_at") and info["started_at"] != trigger_started_at:
            log("triggering job finished (runner container restarted)")
            return
        time.sleep(5)
    log("triggering container never restarted within timeout; relying on idle checks")


def wait_for_idle(token: str) -> bool:
    deadline = time.time() + IDLE_TIMEOUT_S
    while time.time() < deadline:
        if idle_now(token):
            time.sleep(IDLE_SAMPLE_GAP_S)
            if idle_now(token):
                return True
        time.sleep(IDLE_POLL_S)
    return False


def idle_now(token: str) -> bool:
    return is_idle(safe_runners(token), worker_running(runner_container()))


def verify(token: str, desired: str) -> bool:
    deadline = time.time() + VERIFY_TIMEOUT_S
    while time.time() < deadline:
        cid = runner_container()
        info = container_info(cid)
        online, _ = runner_state(safe_runners(token))
        if info.get("running") and info.get("image") == desired and online:
            log(f"verified: container on pin, runner '{RUNNER_NAME}' online, version {runner_version(cid)}")
            return True
        time.sleep(15)
    info = container_info(runner_container())
    log(f"verify FAILED after {VERIFY_TIMEOUT_S}s: running={info.get('running')} "
        f"image_matches={info.get('image') == desired} online={runner_state(safe_runners(token))[0]}")
    return False


def main() -> int:
    started = time.time()
    desired = os.environ.get("DESIRED_IMAGE", "")
    dry_run = os.environ.get("DRY_RUN", "") in ("1", "true")
    if not PIN_RE.match(desired):
        log(f"DESIRED_IMAGE is missing or malformed: {desired!r}")
        return 2

    from truenas_api_client import Client  # host-only dependency

    with Client() as c:
        cfg = c.call("app.config", APP)
    token = cfg["services"][SERVICE].get("environment", {}).get("ACCESS_TOKEN", "")
    current = cfg["services"][SERVICE]["image"]
    info = container_info(runner_container())
    log(f"desired={desired}")
    log(f"config ={current}")
    log(f"running={info.get('image')}")

    if plan(current, info.get("image"), desired) == "converged":
        log("converged; nothing to do")
        write_metrics(True, started, dry_run)
        return 0
    if not token:
        log("ACCESS_TOKEN missing from the stored app config; cannot check idle state")
        write_metrics(False, started, dry_run)
        return 1

    wait_for_trigger_end(os.environ.get("TRIGGER_STARTED_AT", ""))
    log("waiting for the runner to be idle")
    if not wait_for_idle(token):
        log(f"runner not idle within {IDLE_TIMEOUT_S}s; deferring to the next run")
        return 3

    with Client() as c:
        # Re-read: another applier or an operator may have converged it meanwhile.
        cfg = c.call("app.config", APP)
        info = container_info(runner_container())
        if plan(cfg["services"][SERVICE]["image"], info.get("image"), desired) == "converged":
            log("converged while waiting; nothing to do")
            write_metrics(True, started, dry_run)
            return 0
        if dry_run:
            log("dry-run: runner idle; would app.stop -> app.update(image) -> app.start")
            return 0
        if not idle_now(token):
            log("runner picked up a job at the last moment; deferring to the next run")
            return 3

        cfg["services"][SERVICE]["image"] = desired
        stopped = False
        try:
            log("app.stop")
            c.call("app.stop", APP, job=True)
            stopped = True
            log("app.update (image only; stored secrets preserved)")
            c.call("app.update", APP, {"custom_compose_config": cfg}, job=True)
            state = c.call("app.query", [["id", "=", APP]])[0]["state"]
            if state != "RUNNING":
                log(f"app.start (state was {state})")
                c.call("app.start", APP, job=True)
            stopped = False
        except Exception as e:  # noqa: BLE001
            log(f"apply FAILED: {type(e).__name__}: {e}")
            if stopped:
                try:
                    log("restarting the app on its previous config")
                    c.call("app.start", APP, job=True)
                except Exception as e2:  # noqa: BLE001
                    log(f"app.start after failure also FAILED: {type(e2).__name__}: {e2}")
            write_metrics(False, started, dry_run)
            return 1

    ok = verify(token, desired)
    write_metrics(ok, started, dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--print-pin":
        try:
            with open(sys.argv[2]) as fh:
                print(parse_pin(fh.read()))
        except (OSError, ValueError) as err:
            print(f"error: {err}", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    sys.exit(main())
