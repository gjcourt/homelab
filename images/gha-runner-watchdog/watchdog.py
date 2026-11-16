#!/usr/bin/env python3
"""gha-runner-watchdog: keep the hestia runner on its Git pin, and alive (HOMELAB-108).

WHY IT RUNS OUTSIDE THE RUNNER. The self-hosted runner on hestia cannot
redeploy itself mid-job, so deploy-hestia.yml excludes
hosts/hestia/actions-runner/. HOMELAB-106 applied image bumps from a workflow
that ran ON that runner, which healed every fault except the ones that matter
most: a runner that cannot take jobs (deprecated version, corrupted container,
lost registration) could not schedule its own repair. This script runs from
its own always-on Custom App (hosts/hestia/gha-runner-watchdog/), so it keeps
working when the runner does not.

HOW IT RUNS. watchdog.sh, the container's entrypoint, runs one TICK of this
file every INTERVAL_SECONDS. Each tick nsenters into hestia's host namespaces
and runs with the host python3, which ships `truenas_api_client`, so it talks
to the TrueNAS middleware as root over its local socket (the same method as
docs/operations/apps/gha-runner.md section 6.2) and needs no API key. The
GitHub token it uses for the runners API is the runner's own ACCESS_TOKEN,
read from the stored app config in-process and never printed or logged.

ONE TICK (idempotent; every step re-reads live state):
  1. Desired image = the pin in hosts/hestia/actions-runner/docker-compose.yml
     on master, read from the public raw URL. Git is the source of truth.
  2. Ask GitHub for the runner's state with ACCESS_TOKEN. Record whether the
     token works and when it expires (the github-authentication-token-
     expiration response header) in homelabscope_gha_runner_token_*.
     A rejected token (401) is reported and nothing else is attempted:
     recreating the runner cannot fix it. George rotates it (runbook 6.3).
  3. DRIFT (stored config or live container differ from the pin): apply when
     the runner is idle (GitHub online and not busy, AND no Runner.Worker in
     the container, on two samples IDLE_SAMPLE_GAP_S apart). Not idle ->
     defer to the next tick; nothing blocks for long.
  4. DEAD (on the pin, but GitHub has reported it offline or missing for
     UNHEALTHY_GRACE_S, and no job is executing): recreate it on the pin, at
     most once per RECOVERY_COOLDOWN_S. The ephemeral runner re-registers
     itself at start with ACCESS_TOKEN, so a fresh container also replaces a
     retired registration.
  Apply and recover are the same operation: app.stop -> app.update (stored
  config, ONLY the image set, so the masked ACCESS_TOKEN / TRUENAS_API_KEY are
  preserved) -> app.start, then verify the container runs the pin and GitHub
  reports it online. The stop first sidesteps the app.update no-op quirk.
  5. Heartbeat: homelabscope_job_*{job="gha-runner-image-apply"}.
     last_success moves only when the runner is on the pin and online (or
     inside its grace window); last_status=1 on a failed apply/recovery, a
     runner still dead after a recovery, or a rejected token.

Exit codes: 0 ok / deferred, 1 failed (metric written), 2 bad input.

Local, non-host use: `watchdog.py --print-pin <compose>` prints and validates
the pin. That path imports nothing TrueNAS-specific.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP = "gha-runner"
SERVICE = "runner"
REPO = "gjcourt/homelab"
RUNNER_NAME = "hestia"
COMPOSE_PROJECT = f"ix-{APP}"
# Kept from HOMELAB-106 so the alert rules and the absent() guard carry over.
JOB = "gha-runner-image-apply"

PIN_URL = os.environ.get(
    "PIN_URL",
    f"https://raw.githubusercontent.com/{REPO}/master/hosts/hestia/actions-runner/docker-compose.yml",
)
TEXTFILE_DIR = os.environ.get("TEXTFILE_DIR", "/var/lib/node-exporter/textfile")
JOB_TEXTFILE = os.path.join(TEXTFILE_DIR, "homelabscope-gha-runner-apply.prom")
TOKEN_TEXTFILE = os.path.join(TEXTFILE_DIR, "homelabscope-gha-runner-token.prom")
STATE_FILE = os.environ.get("STATE_FILE", "/var/lib/gha-runner-watchdog/state.json")

# 5-minute ticks. The budget is about how long the runner may sit off its pin
# or dead before HomelabscopeJobStale pages, not about the tick rate: a runner
# busy with back-to-back jobs legitimately defers an apply for a while.
MAX_AGE_SECONDS = 21600

IDLE_SAMPLE_GAP_S = int(os.environ.get("IDLE_SAMPLE_GAP_S", "10"))
VERIFY_TIMEOUT_S = int(os.environ.get("VERIFY_TIMEOUT_S", "600"))
# Between jobs the ephemeral runner deregisters, restarts and registers again,
# so "offline" for a minute is normal. Three ticks of it is not.
UNHEALTHY_GRACE_S = int(os.environ.get("UNHEALTHY_GRACE_S", "900"))
RECOVERY_COOLDOWN_S = int(os.environ.get("RECOVERY_COOLDOWN_S", "3600"))

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
# Only this file writes these families.
TOKEN_HELP = {
    "homelabscope_gha_runner_token_valid": "1 if GitHub accepted the hestia runner ACCESS_TOKEN on the last check, else 0.",
    "homelabscope_gha_runner_token_expiry_seconds": "Unix timestamp at which the hestia runner ACCESS_TOKEN expires (absent if it has no expiry).",
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


def on_pin(config_image: str, running_image: str | None, desired: str) -> bool:
    """True only when the stored config AND the live container match.

    A matching config with a stale container is the app.update no-op quirk
    (runbook section 7); that is drift, so stop/update/start recreates it.
    """
    return config_image == desired and running_image == desired


def runner_state(runners: list[dict], name: str = RUNNER_NAME) -> tuple[bool, bool]:
    """(online, busy) for the named runner across its registrations."""
    mine = [r for r in runners if r.get("name") == name]
    online = any(r.get("status") == "online" for r in mine)
    busy = any(r.get("busy") for r in mine)
    return online, busy


def is_idle(runners: list[dict], worker_running: bool) -> bool:
    online, busy = runner_state(runners)
    return online and not busy and not worker_running


def parse_expiry(header: str | None) -> int | None:
    """Unix time from GitHub's github-authentication-token-expiration header.

    Seen forms: '2026-12-01 08:00:00 UTC' and '2026-12-01 00:00:00 -0800'.
    Absent (None) for tokens without an expiry.
    """
    if not header:
        return None
    text = header.strip().replace(" UTC", " +0000")
    try:
        return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z").timestamp())
    except ValueError:
        return None


def decide(
    *,
    drift: bool,
    online: bool,
    idle: bool,
    worker: bool,
    now: float,
    unhealthy_since: float | None,
    last_recovery: float | None,
) -> str:
    """What this tick should do, from observed state. One of:

    apply    on drift, when idle
    defer    on drift, runner busy (or dead with a job running); try next tick
    recover  on the pin, offline past the grace window, no job, not in cooldown
    wait     on the pin, offline but inside the grace window
    cooldown on the pin, still dead, a recovery ran within the cooldown
    ok       on the pin and online
    """
    if drift:
        if idle:
            return "apply"
        # A dead runner is never "idle" (GitHub says offline), but nothing is
        # running on it either: apply the pin rather than wait for an idle
        # that cannot come. This is the deprecated-runner case.
        if not online and not worker and unhealthy_since is not None and now - unhealthy_since >= UNHEALTHY_GRACE_S:
            return "apply"
        return "defer"
    if online:
        return "ok"
    if unhealthy_since is None or now - unhealthy_since < UNHEALTHY_GRACE_S:
        return "wait"
    if worker:
        return "defer"
    if last_recovery is not None and now - last_recovery < RECOVERY_COOLDOWN_S:
        return "cooldown"
    return "recover"


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


def render_token_metrics(valid: bool, expiry: int | None) -> str:
    out = []
    for name, value in (
        ("homelabscope_gha_runner_token_valid", 1 if valid else 0),
        ("homelabscope_gha_runner_token_expiry_seconds", expiry),
    ):
        if value is None:
            continue
        out.append(f"# HELP {name} {TOKEN_HELP[name]}")
        out.append(f"# TYPE {name} gauge")
        out.append(f'{name}{{runner="{RUNNER_NAME}"}} {value}')
    return "\n".join(out) + "\n"


# ------------------------------------------------------------ host side I/O


def atomic_write(path: str, body: str) -> None:
    tmp = f"{path}.{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w") as f:
            f.write(body)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as e:
        log(f"warning: could not write {path}: {e}")


def write_job_metrics(ok: bool, started: float, dry_run: bool) -> None:
    now = int(time.time())
    prev = None
    try:
        with open(JOB_TEXTFILE) as f:
            prev = previous_success(f.read())
    except OSError:
        pass
    if dry_run:
        log(f"dry-run: would write {JOB_TEXTFILE} (status={0 if ok else 1})")
        return
    atomic_write(JOB_TEXTFILE, render_metrics(now if ok else prev, 0 if ok else 1, now - int(started)))


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state: dict, dry_run: bool) -> None:
    if not dry_run:
        atomic_write(STATE_FILE, json.dumps(state))


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
    }


def worker_running(cid: str | None) -> bool:
    """True if a job is executing. A missing or restarting container runs none."""
    if not cid:
        return False
    try:
        return "Runner.Worker" in docker("top", cid)
    except subprocess.CalledProcessError:
        return False


def runner_version(cid: str | None) -> str:
    if not cid:
        return "?"
    logs = subprocess.run(["docker", "logs", "--tail", "50", cid], capture_output=True, text=True, check=False)
    m = re.findall(r"Current runner version: '([^']+)'", logs.stdout + logs.stderr)
    return m[-1] if m else "?"


def fetch_pin() -> str:
    req = urllib.request.Request(PIN_URL, headers={"User-Agent": "homelab-gha-runner-watchdog"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return parse_pin(resp.read().decode())


class TokenRejected(Exception):
    pass


def github_runners(token: str) -> tuple[list[dict], int | None]:
    """(runners, token expiry). Raises TokenRejected on 401, OSError otherwise."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/runners?per_page=100",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "homelab-gha-runner-watchdog",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            expiry = parse_expiry(resp.headers.get("github-authentication-token-expiration"))
            return json.load(resp).get("runners", []), expiry
    except urllib.error.HTTPError as e:
        # The token is a header, never part of the URL or the error text.
        if e.code == 401:
            raise TokenRejected() from None
        raise OSError(f"GitHub runners API HTTP {e.code}") from None


def safe_runners(token: str) -> list[dict]:
    try:
        return github_runners(token)[0]
    except (OSError, TokenRejected, ValueError) as e:
        log(f"github runners API error: {type(e).__name__}: {e}")
        return []


def idle_now(token: str) -> bool:
    return is_idle(safe_runners(token), worker_running(runner_container()))


def recreate(c, desired: str, dry_run: bool) -> bool:
    """app.stop -> app.update(image only) -> app.start. False on failure."""
    cfg = c.call("app.config", APP)
    if dry_run:
        log("dry-run: would app.stop -> app.update(image) -> app.start")
        return True
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
        return True
    except Exception as e:  # noqa: BLE001
        log(f"recreate FAILED: {type(e).__name__}: {e}")
        if stopped:
            try:
                log("restarting the app on its previous config")
                c.call("app.start", APP, job=True)
            except Exception as e2:  # noqa: BLE001
                log(f"app.start after failure also FAILED: {type(e2).__name__}: {e2}")
        return False


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


def tick() -> int:
    started = time.time()
    dry_run = os.environ.get("DRY_RUN", "") in ("1", "true")

    try:
        desired = fetch_pin()
    except (OSError, ValueError) as e:
        # No desired state, no action. A long outage goes stale and pages.
        log(f"cannot read the Git pin from {PIN_URL}: {type(e).__name__}: {e}")
        return 2

    from truenas_api_client import Client  # host-only dependency

    with Client() as c:
        cfg = c.call("app.config", APP)
        token = cfg["services"][SERVICE].get("environment", {}).get("ACCESS_TOKEN", "")
        current = cfg["services"][SERVICE]["image"]
        cid = runner_container()
        info = container_info(cid)

        if not token:
            log("ACCESS_TOKEN missing from the stored app config")
            if not dry_run:
                atomic_write(TOKEN_TEXTFILE, render_token_metrics(False, None))
            write_job_metrics(False, started, dry_run)
            return 1
        try:
            runners, expiry = github_runners(token)
        except TokenRejected:
            log("GitHub rejected ACCESS_TOKEN (401). Rotate it: runbook section 6.3. Not touching the runner.")
            if not dry_run:
                atomic_write(TOKEN_TEXTFILE, render_token_metrics(False, None))
            write_job_metrics(False, started, dry_run)
            return 1
        except (OSError, ValueError) as e:
            log(f"GitHub runners API unavailable ({type(e).__name__}: {e}); no decision this tick")
            return 0
        if not dry_run:
            atomic_write(TOKEN_TEXTFILE, render_token_metrics(True, expiry))

        now = time.time()
        state = load_state()
        online, _ = runner_state(runners)
        if online:
            state.pop("unhealthy_since", None)
        else:
            state.setdefault("unhealthy_since", now)
        worker = worker_running(cid)
        drift = not on_pin(current, info.get("image"), desired)
        idle = is_idle(runners, worker)
        if drift and idle:
            # Second sample: GitHub can hand out a job between two looks.
            time.sleep(IDLE_SAMPLE_GAP_S)
            idle = idle_now(token)
        action = decide(
            drift=drift, online=online, idle=idle, worker=worker, now=now,
            unhealthy_since=state.get("unhealthy_since"), last_recovery=state.get("last_recovery"),
        )
        log(f"desired={desired} config={current} running={info.get('image')} "
            f"online={online} worker={worker} drift={drift} -> {action}")

        if action in ("apply", "recover"):
            if action == "recover":
                state["last_recovery"] = now
            save_state(state, dry_run)
            if not recreate(c, desired, dry_run):
                write_job_metrics(False, started, dry_run)
                return 1
            if dry_run:
                return 0
            ok = verify(token, desired)
            if ok:
                state.pop("unhealthy_since", None)
                save_state(state, dry_run)
            write_job_metrics(ok, started, dry_run)
            return 0 if ok else 1

        save_state(state, dry_run)
        if action in ("ok", "wait"):
            write_job_metrics(True, started, dry_run)
        elif action == "cooldown":
            log("runner still offline after a recovery; leaving it for the operator (runbook 6.2)")
            write_job_metrics(False, started, dry_run)
            return 1
        # defer: leave the heartbeat alone; staleness pages if it never clears.
        return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--print-pin":
        try:
            with open(sys.argv[2]) as fh:
                print(parse_pin(fh.read()))
        except (OSError, ValueError) as err:
            print(f"error: {err}", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    sys.exit(tick())
