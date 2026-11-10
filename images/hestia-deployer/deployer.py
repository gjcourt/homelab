#!/usr/bin/env python3
"""hestia-deployer: Git-pull deployer for hestia's TrueNAS Custom Apps (HOMELAB-111).

Plan: docs/plans/2026-11-09-hestia-pull-deployer-plan.md. Runbook:
docs/operations/apps/hestia-deployer.md.

THIS IS PHASE 1: SHADOW. The deployer reads Git, reads TrueNAS, works out what
it WOULD do, and reports it in logs and metrics. It never changes an app.
deploy-hestia.yml and the self-hosted runner keep deploying until Phase 2.
"Never changes an app" is structural, not a convention:

  - APPLY_ENABLED["truenas"] is False in this file. Phase 2 flips it in its
    own PR. Each backend has its own switch (alcatraz plan A1): arming the
    TrueNAS backend never arms the compose one.
  - Every middleware call goes through GuardedClient, which allows only the
    read methods in READ_METHODS unless mutations were enabled at construction.
    Nothing in this file enables them while APPLY_ENABLED["truenas"] is False.
  - apply_app() raises DryRunViolation before touching the client when
    DRY_RUN is on or APPLY_ENABLED["truenas"] is False, and reconcile() never
    calls it.
  - Docker is reached only through docker_read(), which allows `ps` / `inspect`.
  - DRY_RUN defaults to ON: only DRY_RUN=0 (or false) turns it off.

DRY_RUN gates changes to SCALE apps only. The deployer's own files (the git
cache, desired.json, state.json and the textfile metrics) are written in
every mode: they are what the shadow phase exists to produce.

TWO HALVES, ONE FILE. deployer.sh runs, every INTERVAL_SECONDS:

  1. `deployer.py fetch` IN THE CONTAINER (alpine python3 + py3-yaml + git).
     git ls-remote resolves master once. If the SHA moved, a depth-1 fetch of
     that one commit lands in a bare cache under DATA_DIR. Discovery (the
     deploy-hestia.yml rule, plus x-deploy validation) then reads every
     hosts/hestia/**/docker-compose*.yml AT THAT ONE SHA and writes
     DATA_DIR/desired.json. Git content is public, so nothing secret is in it.
     No REST API, no raw.githubusercontent.com (CDN-cached, not
     commit-consistent), no credential.

  2. `deployer.py reconcile` ON THE HOST, piped to hestia's python3 through
     `nsenter -t 1`. That python3 ships truenas_api_client, so it talks to the
     middleware as root over its local socket and needs no API key (the
     gha-runner-watchdog method). This half imports nothing but the standard
     library and truenas_api_client: no yaml. For each app it:
       - app.query: missing? not a Custom App?
       - app.config: the stored compose dict
       - secret merge (plan D4): a Git "" over a stored non-empty value is
         carried over only for keys in x-deploy.preserve-env; any other such
         key BLOCKS the app. Stored values stay in memory: never logged, never
         written to state or metrics.
       - semantic diff of normalised dicts (plan D3); logs changed PATHS only,
         image values excepted (they are public)
       - running-image drift: each service's live container vs stored image
         (the app.update no-op quirk detector, generalised)
     and writes DATA_DIR/state.json and TEXTFILE_DIR/homelabscope-deploy.prom.

Exit codes: 0 evaluated, 1 could not evaluate (metric written), 2 bad input.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import time

# ------------------------------------------------------------------ constants

# One switch per backend (alcatraz plan A1, HOMELAB-118). Phase 1 (shadow).
# hestia's Phase 2 (HOMELAB-112) flips "truenas" only, in the same PR that sets
# DRY_RUN=0 in hosts/hestia/hestia-deployer/docker-compose.yml and moves
# deploy-hestia.yml to workflow_dispatch. "compose" (alcatraz) stays False
# until alcatraz's own cutover PR. Tests assert both are False.
APPLY_ENABLED = {"truenas": False, "compose": False}


def apply_enabled(backend: str = "truenas") -> bool:
    """The per-backend switch. An unknown backend is never enabled."""
    return APPLY_ENABLED.get(backend) is True


# Host parameters (alcatraz plan A1). Every default is hestia's value, so a
# container with none of these set behaves exactly as before. The env names
# carry a DEPLOYER_ prefix: bare HOST / JOB are set by shells (zsh) and some
# base images, and a stray value would stop the deployer at import. They reach the
# reconcile half on the host through nsenter, like DRY_RUN and DATA_DIR.
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_PREFIX_RE = re.compile(r"^hosts/[a-z0-9]([a-z0-9-]*[a-z0-9])?/$")


class ConfigError(ValueError):
    """A host parameter from the environment is malformed."""


def env_param(name: str, default: str, pattern, env=None) -> str:
    """os.environ[name] or default, checked against pattern.

    The value ends up in metric labels, log lines and git pathspecs, so it is
    validated rather than escaped. The error names the variable, never the
    value (an operator could have pasted something private into it).
    """
    env = os.environ if env is None else env
    val = env.get(name)
    if val is None or val == "":
        return default
    if not pattern.match(val):
        raise ConfigError(f"{name} is malformed (want {pattern.pattern})")
    return val


def excluded_prefixes_from_env(hosts_prefix: str, self_name: str, env=None) -> tuple[str, ...]:
    """DEPLOYER_EXCLUDED_PREFIXES: comma-separated repo paths under hosts_prefix.

    Default: the host's runner directory and the deployer's own directory,
    which for hestia is exactly the pre-HOMELAB-118 constant.
    """
    env = os.environ if env is None else env
    raw = env.get("DEPLOYER_EXCLUDED_PREFIXES")
    if raw is None or raw.strip() == "":
        return (f"{hosts_prefix}actions-runner/", f"{hosts_prefix}{self_name}/")
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.startswith(hosts_prefix) or not part.endswith("/") or ".." in part.split("/"):
            raise ConfigError(f"DEPLOYER_EXCLUDED_PREFIXES entries must be directories under {hosts_prefix}")
        out.append(part)
    return tuple(out)


REPO_URL = os.environ.get("REPO_URL", "https://github.com/gjcourt/homelab")
BRANCH_REF = "refs/heads/master"
# The `host` label on every homelabscope_deploy_* sample (alcatraz plan A6).
HOST = env_param("DEPLOYER_HOST", "hestia", _HOST_RE)
HOSTS_PREFIX = env_param("DEPLOYER_HOSTS_PREFIX", "hosts/hestia/", _PREFIX_RE)
COMPOSE_GLOB = "docker-compose*.yml"

SELF_NAME = env_param("DEPLOYER_SELF_NAME", "hestia-deployer", _HOST_RE)
# Never managed: the runner (it deploys itself; deploy-hestia.yml excludes it
# too) and this deployer (self-apply is Phase 2, plan D5).
EXCLUDED_PREFIXES = excluded_prefixes_from_env(HOSTS_PREFIX, SELF_NAME)
EXCLUDED_NAMES = (SELF_NAME,)
# SCALE names of the excluded apps whose compose does not carry them
# (hosts/hestia/actions-runner/ derives "actions-runner"; the app is gha-runner).
# They have a compose in Git, so they are not "unmanaged".
EXCLUDED_SCALE_APPS = ("gha-runner", SELF_NAME)

DATA_DIR = os.environ.get("DATA_DIR", "/mnt/main/apps/hestia-deployer")
TEXTFILE_DIR = os.environ.get("TEXTFILE_DIR", "/var/lib/node-exporter/textfile")
DEPLOY_TEXTFILE = "homelabscope-deploy.prom"

JOB = env_param("DEPLOYER_JOB", "hestia-deployer", _HOST_RE)
# Plan D9: max_age 1800s (6 ticks of 300s).
MAX_AGE_SECONDS = 1800
# Plan D5 / open question 5: at most 3 apps per tick, in name order.
MAX_APPLIES_PER_TICK = int(os.environ.get("MAX_APPLIES_PER_TICK", "3"))
# The deployer's own eyes: never restarted in the same tick as each other.
EYES = ("node-exporter", "homelabscope-heartbeat")

STATES = ("synced", "pending", "failed", "blocked", "paused", "archived", "missing", "invalid")
APP_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# The only middleware methods a dry-run deployer may call.
READ_METHODS = frozenset({"app.query", "app.config"})
# The only docker subcommands this file runs.
DOCKER_READ = frozenset({"ps", "inspect"})

# HELP strings MUST match the other homelabscope textfile writers byte for byte
# (images/homelabscope-heartbeat/heartbeat.sh and friends). node-exporter drops a
# metric family from every file whose HELP differs from the first file it read
# (PR #1864). tests/test_deployer.py checks this against heartbeat.sh.
JOB_HELP = {
    "homelabscope_job_last_success_seconds": "Unix timestamp of the job's last successful run.",
    "homelabscope_job_last_duration_seconds": "Wall-clock duration of the job's last run (seconds).",
    "homelabscope_job_last_status": "Last run status (0=ok, 1=fail).",
    "homelabscope_job_max_age_seconds": "Staleness budget for this job (seconds).",
}
# Only this file writes these families. A test pins them. They are
# host-neutral (alcatraz plan A6): hestia's file and alcatraz's relayed file
# land in ONE node-exporter, which needs identical HELP text and identical
# label names per family. Every sample carries host="<HOST>".
DEPLOY_HELP = {
    "homelabscope_deploy_head_info": "master commit the last deployer tick evaluated.",
    "homelabscope_deploy_head_commit_seconds": "Commit time of the master commit the last deployer tick evaluated.",
    "homelabscope_deploy_app_state": "Deployer state of each app on the host (1 for the current state).",
    "homelabscope_deploy_app_applied_info": "Last commit the deployer successfully applied to the app.",
    "homelabscope_deploy_app_last_apply_seconds": "Unix timestamp of the deployer's last successful apply of the app.",
    "homelabscope_deploy_paused": "1 when the deployer is paused (scope=global: PAUSE file; otherwise x-deploy.paused).",
    "homelabscope_deploy_unmanaged_apps": "Count of apps on the host with no compose file in Git.",
    "homelabscope_deploy_dry_run": "1 when the deployer runs in dry-run (shadow) mode and applies nothing.",
}


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}", flush=True)


def dry_run_from_env(env=None) -> bool:
    """DRY_RUN is ON unless explicitly 0/false. Unset means dry-run."""
    env = os.environ if env is None else env
    return str(env.get("DRY_RUN", "1")).strip().lower() not in ("0", "false")


def mutations_allowed(dry_run: bool, backend: str = "truenas") -> bool:
    return apply_enabled(backend) and not dry_run


# ================================================================ discovery
# Pure functions. `files` maps repo path -> compose text at ONE commit.


def is_compose_path(path: str) -> bool:
    """The deploy-hestia.yml glob: hosts/hestia/**/docker-compose*.yml."""
    return path.startswith(HOSTS_PREFIX) and fnmatch.fnmatchcase(os.path.basename(path), COMPOSE_GLOB)


def default_name(path: str) -> str:
    """docker-compose-<name>.yml -> <name>, else the parent directory name."""
    fname = os.path.basename(path)
    if fname.startswith("docker-compose-") and fname.endswith(".yml"):
        return fname[len("docker-compose-"):-len(".yml")]
    return os.path.basename(os.path.dirname(path))


def nested_x_keys(node, path: str = "") -> list[str]:
    """Paths of x-* keys anywhere BELOW the top level."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else str(k)
            if path and str(k).startswith("x-"):
                found.append(p)
            found.extend(nested_x_keys(v, p))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found.extend(nested_x_keys(v, f"{path}[{i}]"))
    return found


def _jsonable(obj):
    """Round-trip through JSON so desired.json holds exactly what is compared.

    YAML can produce non-JSON scalars (an unquoted date parses to
    datetime.date). x-* keys are stripped before this, which removes the known
    case (x-deploy.archived-at); anything else becomes its string form.
    """
    return json.loads(json.dumps(obj, default=str))


def parse_entry(path: str, text: str, load_yaml) -> dict:
    """One discovery entry. status: managed | archived | excluded | invalid."""
    entry = {"file": path, "name": default_name(path), "status": "managed", "reason": "",
             "paused": False, "preserve_env": [], "create": False, "compose": None}

    if path.startswith(EXCLUDED_PREFIXES):
        entry.update(status="excluded", reason="never managed by the deployer")
        # Still try to read a name override so unmanaged-app accounting is right.
    try:
        doc = load_yaml(text)
    except Exception as e:  # noqa: BLE001 - any YAML error is an invalid file
        if entry["status"] != "excluded":
            entry.update(status="invalid", reason=f"YAML parse error: {type(e).__name__}")
        return entry
    if doc is None:
        doc = {}
    if not isinstance(doc, dict):
        if entry["status"] != "excluded":
            entry.update(status="invalid", reason="top level is not a mapping")
        return entry

    meta = doc.get("x-deploy")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        if entry["status"] != "excluded":
            entry.update(status="invalid", reason="x-deploy is not a mapping")
        return entry

    name = meta.get("name")
    if name is not None:
        if not isinstance(name, str) or not name:
            if entry["status"] == "excluded":
                return entry
            entry.update(status="invalid", reason=f"x-deploy.name must be a non-empty string, got {name!r}")
            return entry
        entry["name"] = name
    if entry["status"] == "excluded":
        return entry
    if entry["name"] in EXCLUDED_NAMES:
        entry.update(status="excluded", reason="never managed by the deployer")
        return entry
    if not APP_NAME_RE.match(entry["name"]):
        entry.update(status="invalid", reason=f"app name {entry['name']!r} is not a valid TrueNAS app name")
        return entry

    # Strict YAML bools, as deploy-hestia.yml: a quoted "false" is a non-empty
    # string and would otherwise be truthy.
    for key in ("archived", "paused", "create"):
        val = meta.get(key)
        if val is not None and not isinstance(val, bool):
            entry.update(status="invalid", reason=f"x-deploy.{key} must be a YAML bool, got {val!r}")
            return entry

    if meta.get("archived") is True:
        entry.update(status="archived", reason=str(meta.get("archived-reason") or "no reason given"))
        return entry

    preserve = meta.get("preserve-env", [])
    if preserve is None:
        preserve = []
    if not isinstance(preserve, list) or not all(isinstance(k, str) and k for k in preserve):
        entry.update(status="invalid", reason="x-deploy.preserve-env must be a list of env var names")
        return entry
    entry["preserve_env"] = sorted(set(preserve))
    entry["paused"] = meta.get("paused") is True
    entry["create"] = meta.get("create") is True

    services = doc.get("services")
    if not isinstance(services, dict) or not services:
        entry.update(status="invalid", reason="no services mapping")
        return entry
    for svc, body in services.items():
        if not isinstance(body, dict):
            entry.update(status="invalid", reason=f"services.{svc} is not a mapping")
            return entry
    nested = nested_x_keys(doc)
    if nested:
        entry.update(status="invalid", reason="x-* keys below the top level: " + ", ".join(nested))
        return entry

    # Desired = the Git compose with top-level x-* stripped
    # (scripts/truenas-update-app.sh does the same before app.update).
    compose = {k: v for k, v in doc.items() if not str(k).startswith("x-")}
    entry["compose"] = _jsonable(compose)
    return entry


def discover(files: dict[str, str], load_yaml) -> list[dict]:
    """Entries for every compose file, sorted by name, duplicates invalidated.

    Two ACTIVE (managed/invalid-by-content) files claiming one name are both
    invalid: applying either would fight the other. An archived file that
    shares a name with an active one (or with an earlier archived file) is
    marked `shadowed`: reconcile skips it, so each app name yields exactly one
    metric series and it reflects the live file.
    """
    entries = [parse_entry(p, t, load_yaml) for p, t in sorted(files.items()) if is_compose_path(p)]
    active: dict[str, list[dict]] = {}
    for e in entries:
        if e["status"] in ("managed", "invalid", "excluded"):
            active.setdefault(e["name"], []).append(e)
    for name, group in active.items():
        if len(group) > 1:
            files_ = ", ".join(x["file"] for x in group)
            for e in group:
                if e["status"] != "excluded":
                    e.update(status="invalid", reason=f"duplicate app name {name!r}: {files_}", compose=None)
    seen_archived: set[str] = set()
    for e in entries:
        if e["status"] == "archived":
            if e["name"] in active or e["name"] in seen_archived:
                e["shadowed"] = True
            seen_archived.add(e["name"])
    return sorted(entries, key=lambda e: (e["name"], e["file"]))


# ================================================================ fetch (container)


def git(*args: str, git_dir: str | None = None) -> str:
    cmd = ["git"]
    if git_dir:
        cmd += ["--git-dir", git_dir]
    cmd += list(args)
    # cwd=/ so a repository around the working directory never leaks in.
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120, cwd="/").stdout


def ls_remote_master(url: str = REPO_URL) -> str:
    out = git("ls-remote", url, BRANCH_REF)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == BRANCH_REF and SHA_RE.match(parts[0]):
            return parts[0]
    raise ValueError(f"ls-remote returned no {BRANCH_REF}")


def fetch_commit(git_dir: str, url: str = REPO_URL) -> str:
    """Depth-1 fetch of master's head into the bare cache; returns its SHA.

    Uses FETCH_HEAD rather than trusting the ls-remote SHA: if master moved
    between the two calls, the tick evaluates whatever single commit arrived.
    """
    if not os.path.isdir(git_dir):
        git("init", "--bare", "-q", git_dir)
    git("fetch", "-q", "--depth", "1", "--no-tags", url, BRANCH_REF, git_dir=git_dir)
    sha = git("rev-parse", "FETCH_HEAD^{commit}", git_dir=git_dir).strip()
    if not SHA_RE.match(sha):
        raise ValueError(f"unexpected FETCH_HEAD {sha!r}")
    # A ref keeps the evaluated commit reachable across git's auto-gc.
    git("update-ref", "refs/deployer/head", sha, git_dir=git_dir)
    return sha


def read_tree(git_dir: str, sha: str) -> dict[str, str]:
    names = git("ls-tree", "-r", "--name-only", sha, "--", HOSTS_PREFIX, git_dir=git_dir).splitlines()
    return {p: git("show", f"{sha}:{p}", git_dir=git_dir) for p in names if is_compose_path(p)}


def commit_time(git_dir: str, sha: str) -> int:
    return int(git("log", "-1", "--format=%ct", sha, git_dir=git_dir).strip())


def fetch_main(data_dir: str = DATA_DIR, url: str = REPO_URL) -> int:
    """Container half of a tick. Writes desired.json for exactly one commit."""
    import yaml  # container-only dependency (py3-yaml)

    git_dir = os.path.join(data_dir, "cache.git")
    desired_path = os.path.join(data_dir, "desired.json")
    prev = load_json(desired_path)
    try:
        head = ls_remote_master(url)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        # Hold the last SHA; reconcile still runs the local drift check.
        log(f"fetch: ls-remote failed ({type(e).__name__}); keeping {str(prev.get('sha', 'none'))[:7]}")
        return 1
    if prev.get("sha") == head and prev.get("version") == DESIRED_VERSION:
        return 0
    try:
        sha = fetch_commit(git_dir, url)
        files = read_tree(git_dir, sha)
        ctime = commit_time(git_dir, sha)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log(f"fetch: git fetch failed ({type(e).__name__}); keeping {str(prev.get('sha', 'none'))[:7]}")
        return 1
    entries = discover(files, yaml.safe_load)
    doc = {"version": DESIRED_VERSION, "sha": sha, "commit_time": ctime,
           "fetched_at": int(time.time()), "apps": entries}
    atomic_write(desired_path, json.dumps(doc, sort_keys=True))
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    log(f"fetch: master {sha[:7]} -> {len(entries)} compose files {json.dumps(counts, sort_keys=True)}")
    for e in entries:
        if e["status"] == "invalid":
            log(f"fetch: INVALID {e['file']} ({e['name']}): {e['reason']}")
        if e.get("shadowed"):
            log(f"fetch: archived {e['file']} shares the name {e['name']!r} with an active file; ignored")
    return 0


DESIRED_VERSION = 1


# ================================================================ compare (pure)


def _kv_list_to_map(items) -> dict:
    out = {}
    for item in items:
        s = str(item)
        if "=" in s:
            k, v = s.split("=", 1)
            out[k] = v
        else:
            out[s] = None
    return out


def _scalar_str(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def normalize_service(svc: dict) -> dict:
    """Shape-only normalisation of one service (plan D3).

    environment / labels: list form [K=V] -> map {K: V}; values as strings
    (compose stringifies `FOO: 1` and `FOO: true`). A bare `- K` (no `=`)
    stays None, which compose treats as "take it from the shell".
    Add a case here, with a test, for every false positive the shadow finds.
    """
    out = dict(svc)
    for key in ("environment", "labels"):
        val = out.get(key)
        if isinstance(val, list):
            val = _kv_list_to_map(val)
        if isinstance(val, dict):
            out[key] = {str(k): _scalar_str(v) for k, v in val.items()}
    return out


def normalize(compose: dict) -> dict:
    out = {k: v for k, v in compose.items() if not str(k).startswith("x-")}
    services = out.get("services")
    if isinstance(services, dict):
        out["services"] = {name: normalize_service(s) if isinstance(s, dict) else s
                           for name, s in services.items()}
    return out


def merge_secrets(desired: dict, stored: dict, preserve: list[str]) -> tuple[dict, list[str]]:
    """Plan D4. Returns (merged desired, blocked "service.KEY" names).

    Both inputs are normalised first. For each env key that is empty in Git
    ("" , or None from `FOO:` / a bare `- FOO`) and non-empty in stored: listed in preserve -> stored value carried over;
    not listed -> blocked (the deployer never blanks a stored value). A key
    absent from Git is a removal and is not consulted. Values are never
    returned in the blocked list.
    """
    merged = normalize(desired)
    stored_n = normalize(stored)
    blocked: list[str] = []
    allow = set(preserve)
    for svc_name, svc in (merged.get("services") or {}).items():
        env = svc.get("environment")
        if not isinstance(env, dict):
            continue
        s_env = ((stored_n.get("services") or {}).get(svc_name) or {}).get("environment")
        if not isinstance(s_env, dict):
            continue
        for key, val in env.items():
            if val not in ("", None):
                continue
            stored_val = s_env.get(key)
            if stored_val in (None, ""):
                continue
            if key in allow:
                env[key] = stored_val
            else:
                blocked.append(f"{svc_name}.{key}")
    return merged, sorted(blocked)


def diff_paths(a, b, path: str = "") -> list[str]:
    """Dotted paths where a and b differ. Never returns values."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}.{k}" if path else str(k)
            if k not in a or k not in b:
                out.append(p)
            else:
                out.extend(diff_paths(a[k], b[k], p))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(diff_paths(x, y, f"{path}[{i}]"))
        return out
    return [] if a == b else [path or "<root>"]


IMAGE_PATH_RE = re.compile(r"^services\.([^.\[]+)\.image$")


def describe_paths(paths: list[str], desired: dict, stored: dict) -> list[str]:
    """Log-safe description: paths only, except images (public) show old -> new."""
    out = []
    for p in paths:
        m = IMAGE_PATH_RE.match(p)
        if m:
            svc = m.group(1)
            old = ((stored.get("services") or {}).get(svc) or {}).get("image")
            new = ((desired.get("services") or {}).get(svc) or {}).get("image")
            out.append(f"{p}: {old} -> {new}")
        else:
            out.append(p)
    return out


def image_drift(stored: dict, containers: dict[str, dict], stopped: bool = False) -> list[str]:
    """Services whose live container does not run the stored image.

    containers: service name -> {"image", "running", "restarting"} or missing.
    A stored image the container does not run is the app.update no-op quirk
    (docs/operations/2026-05-14-truenas-app-update-quirk.md).

    stopped: the TrueNAS app is STOPPED on purpose. The image comparison still
    applies to any container that exists, but a missing or not-running
    container is expected, not drift. The deployer never starts a stopped app.
    """
    out = []
    for svc, body in sorted((stored.get("services") or {}).items()):
        want = body.get("image") if isinstance(body, dict) else None
        info = containers.get(svc)
        if not info:
            if not stopped:
                out.append(f"{svc}: no container")
        elif info.get("image") != want:
            out.append(f"{svc}: container runs {info.get('image')}, stored {want}")
        elif not stopped and (not info.get("running") or info.get("restarting")):
            out.append(f"{svc}: container not running")
    return out


def evaluate_app(entry: dict, scale_app: dict | None, stored, containers: dict[str, dict]) -> dict:
    """Pure per-app decision. Returns {state, reasons, changes, drift, blocked, merged}.

    `merged` is the would-be app.update payload; it may carry preserved stored
    values, so the caller must never log or persist it.
    """
    res = {"app": entry["name"], "file": entry["file"], "state": "invalid", "reasons": [],
           "changes": [], "drift": [], "blocked": [], "merged": None, "stopped": False}
    status = entry["status"]
    if status == "archived":
        res["state"] = "archived"
        return res
    if status == "invalid":
        res["reasons"] = [entry["reason"]]
        return res
    if scale_app is None:
        res["state"] = "missing"
        res["reasons"] = ["no TrueNAS app with this name" +
                          ("; x-deploy.create is not acted on until Phase 5" if entry.get("create") else "")]
        return res
    if not scale_app.get("custom_app"):
        res["reasons"] = ["TrueNAS app is not a Custom App; never touched"]
        return res
    if not isinstance(stored, dict) or not isinstance(stored.get("services"), dict):
        # TrueNAS API drift: an unknown shape is invalid, never a guess (plan Risks).
        res["reasons"] = [f"unrecognised app.config shape ({type(stored).__name__})"]
        return res
    bad = sorted(str(k) for k, v in stored["services"].items() if not isinstance(v, dict))
    if bad:
        res["reasons"] = [f"unrecognised app.config shape: services {', '.join(bad)} not mappings"]
        return res

    merged, blocked = merge_secrets(entry["compose"], stored, entry.get("preserve_env", []))
    stored_n = normalize(stored)
    res["merged"] = merged
    res["blocked"] = blocked
    res["changes"] = describe_paths(diff_paths(merged, stored_n), merged, stored_n)
    res["stopped"] = scale_app.get("state") == "STOPPED"
    res["drift"] = image_drift(stored, containers, stopped=res["stopped"])

    if entry.get("paused"):
        res["state"] = "paused"
    elif blocked:
        res["state"] = "blocked"
        res["reasons"] = [f"Git has \"\" over a stored value for {k}; add it to x-deploy.preserve-env or "
                          "move the secret to a host file" for k in blocked]
    elif res["changes"] or res["drift"]:
        res["state"] = "pending"
    else:
        res["state"] = "synced"
    return res


def plan_applies(results: list[dict], cap: int = MAX_APPLIES_PER_TICK) -> tuple[list[str], list[str]]:
    """Which pending apps a tick would apply (name order, <= cap, eyes apart)."""
    pending = sorted(r["app"] for r in results if r["state"] == "pending")
    chosen: list[str] = []
    deferred: list[str] = []
    for name in pending:
        if len(chosen) >= cap:
            deferred.append(name)
        elif name in EYES and any(c in EYES for c in chosen):
            deferred.append(name)
        else:
            chosen.append(name)
    # The deployer itself always last (it is excluded in Phase 1, so this is moot).
    chosen.sort(key=lambda n: (n == SELF_NAME, n))
    return chosen, deferred


# ================================================================ metrics (pure)


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def previous_success(text: str) -> int | None:
    m = re.search(r'^homelabscope_job_last_success_seconds\{job="' + re.escape(JOB) + r'"\}\s+(\d+)', text, re.M)
    return int(m.group(1)) if m else None


def render_metrics(*, ok: bool, now: int, prev_success: int | None, duration: int, dry_run: bool,
                   sha: str | None = None, commit_seconds: int | None = None,
                   results: list[dict] | None = None, global_paused: bool = False,
                   unmanaged: int | None = None, applied: dict | None = None) -> str:
    """The whole homelabscope-deploy.prom body. Absent values emit no sample."""
    results = results or []
    applied = applied or {}
    fams: list[tuple[str, list[tuple[str, object]]]] = []

    def job(name, value):
        fams.append((name, [] if value is None else [(f'job="{JOB}"', value)]))

    job("homelabscope_job_last_success_seconds", now if ok else prev_success)
    job("homelabscope_job_last_duration_seconds", duration)
    job("homelabscope_job_last_status", 0 if ok else 1)
    job("homelabscope_job_max_age_seconds", MAX_AGE_SECONDS)

    # alcatraz plan A6: host="<HOST>" first on every homelabscope_deploy_*
    # sample (the relay's validator keys on it). homelabscope_job_* keeps its
    # job-only label set: other textfile writers share those families.
    h = f'host="{_esc(HOST)}"'

    def dep(name, samples):
        fams.append((name, [(f"{h},{labels}" if labels else h, value) for labels, value in samples]))

    dep("homelabscope_deploy_dry_run", [("", 1 if dry_run else 0)])
    if sha:
        dep("homelabscope_deploy_head_info", [(f'commit="{_esc(sha[:7])}"', 1)])
    if commit_seconds is not None:
        dep("homelabscope_deploy_head_commit_seconds", [("", commit_seconds)])

    # One series per app. Duplicate-name files are all `invalid`, so a clash
    # resolves to invalid; never emit the same label set twice.
    by_app: dict[str, str] = {}
    for r in results:
        if r["app"] in by_app and by_app[r["app"]] != r["state"]:
            by_app[r["app"]] = "invalid"
        else:
            by_app.setdefault(r["app"], r["state"])
    states = []
    paused = [('scope="global"', 1 if global_paused else 0)]
    for app, st in sorted(by_app.items()):
        states.append((f'app="{_esc(app)}",state="{st}"', 1))
        if st == "paused":
            paused.append((f'scope="{_esc(app)}"', 1))
    dep("homelabscope_deploy_app_state", states)
    dep("homelabscope_deploy_paused", paused)

    dep("homelabscope_deploy_app_applied_info",
        [(f'app="{_esc(a)}",commit="{_esc(v["commit"][:7])}"', 1) for a, v in sorted(applied.items())])
    dep("homelabscope_deploy_app_last_apply_seconds",
        [(f'app="{_esc(a)}"', int(v["at"])) for a, v in sorted(applied.items())])
    if unmanaged is not None:
        dep("homelabscope_deploy_unmanaged_apps", [("", unmanaged)])

    out = []
    for name, samples in fams:
        if not samples:
            # Never fabricate: on a first-ever failure last_success is absent.
            continue
        help_ = JOB_HELP.get(name) or DEPLOY_HELP[name]
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} gauge")
        for labels, value in samples:
            out.append(f"{name}{{{labels}}} {value}" if labels else f"{name} {value}")
    return "\n".join(out) + "\n"


# ================================================================ host side I/O


class DryRunViolation(RuntimeError):
    """A mutating call was attempted while mutations are disabled."""


class GuardedClient:
    """Wraps truenas_api_client.Client. Default-deny for anything but reads."""

    def __init__(self, client, allow_mutations: bool = False):
        if allow_mutations and not apply_enabled("truenas"):
            raise DryRunViolation('mutations requested but APPLY_ENABLED["truenas"] is False (Phase 1 shadow)')
        self._client = client
        self._allow = allow_mutations

    def call(self, method: str, *args, **kwargs):
        if method not in READ_METHODS and not self._allow:
            raise DryRunViolation(f"refusing {method}: hestia-deployer is in dry-run")
        return self._client.call(method, *args, **kwargs)


def apply_app(client: GuardedClient, name: str, payload: dict, *, dry_run: bool) -> bool:
    """app.stop -> app.update(payload) -> app.start (plan D5). Phase 2 only.

    Refuses outright in dry-run or while APPLY_ENABLED["truenas"] is False,
    before any call is made. reconcile() does not call this in Phase 1.
    """
    if dry_run or not apply_enabled("truenas"):
        raise DryRunViolation(f"refusing to apply {name}: dry-run={dry_run} "
                              f"APPLY_ENABLED={apply_enabled('truenas')}")
    stopped = False
    try:
        # An app STOPPED on purpose is updated but never started.
        was_running = client.call("app.query", [["id", "=", name]])[0]["state"] != "STOPPED"
        if was_running:
            client.call("app.stop", name, job=True)
            stopped = True
        client.call("app.update", name, {"custom_compose_config": payload}, job=True)
        state = client.call("app.query", [["id", "=", name]])[0]["state"]
        if was_running and state != "RUNNING":
            client.call("app.start", name, job=True)
        return True
    except Exception as e:  # noqa: BLE001
        # Type only: middleware / pydantic errors can echo submitted values,
        # and the payload may carry preserved secrets.
        log(f"{name}: apply FAILED: {type(e).__name__}")
        if stopped:
            try:
                client.call("app.start", name, job=True)
            except Exception as e2:  # noqa: BLE001
                log(f"{name}: app.start after failure also FAILED: {type(e2).__name__}")
        return False


def atomic_write(path: str, body: str, mode: int = 0o644) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w") as f:
            f.write(body)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as e:
        log(f"warning: could not write {path}: {e}")


def load_json(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def docker_read(*args: str) -> str:
    if not args or args[0] not in DOCKER_READ:
        raise DryRunViolation(f"refusing docker {args[:1]}: read-only")
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=60).stdout


class DockerUnavailable(RuntimeError):
    """docker ps / inspect failed: the app cannot be evaluated this tick."""


def app_containers(name: str, docker=docker_read) -> dict[str, dict]:
    """service -> {image, running, restarting} for TrueNAS project ix-<name>.

    Raises DockerUnavailable rather than returning {} on a docker error, so a
    broken docker CLI reads as "unevaluated", not as every service drifting.
    """
    try:
        ids = docker("ps", "-a", "-q", "--filter", f"label=com.docker.compose.project=ix-{name}").split()
        if not ids:
            return {}
        data = json.loads(docker("inspect", *ids))
        if not isinstance(data, list):
            raise ValueError("docker inspect did not return a list")
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        raise DockerUnavailable(type(e).__name__) from None
    out: dict[str, dict] = {}
    for c in data:
        svc = (c.get("Config", {}).get("Labels") or {}).get("com.docker.compose.service")
        if not svc:
            continue
        info = {"image": c.get("Config", {}).get("Image"),
                "running": bool(c.get("State", {}).get("Running")),
                "restarting": bool(c.get("State", {}).get("Restarting"))}
        # Prefer a running container if a stale one shares the service label.
        if svc not in out or (info["running"] and not out[svc]["running"]):
            out[svc] = info
    return out


def reconcile(client, *, docker=docker_read, data_dir: str = DATA_DIR, textfile_dir: str = TEXTFILE_DIR,
              dry_run: bool = True, now: float | None = None) -> int:
    """Host half of a tick. `client` is anything with .call(method, *args)."""
    started = time.time() if now is None else now
    dry_run = dry_run or not apply_enabled("truenas")
    guarded = client if isinstance(client, GuardedClient) else GuardedClient(client, mutations_allowed(dry_run))
    prom = os.path.join(textfile_dir, DEPLOY_TEXTFILE)
    prev_text = ""
    try:
        with open(prom) as f:
            prev_text = f.read()
    except OSError:
        pass
    prev_ok = previous_success(prev_text)
    state = load_json(os.path.join(data_dir, "state.json"))
    applied = state.get("applied") or {}
    global_paused = os.path.exists(os.path.join(data_dir, "PAUSE"))

    def fail(msg: str) -> int:
        log(f"cannot evaluate: {msg}")
        atomic_write(prom, render_metrics(ok=False, now=int(started), prev_success=prev_ok,
                                          duration=int(time.time() - started), dry_run=dry_run,
                                          global_paused=global_paused, applied=applied))
        return 1

    desired = load_json(os.path.join(data_dir, "desired.json"))
    if desired.get("version") != DESIRED_VERSION or not SHA_RE.match(str(desired.get("sha", ""))):
        return fail("no usable desired.json yet (fetch has not succeeded)")
    entries = [e for e in desired.get("apps", []) if not e.get("shadowed")]
    sha = desired["sha"]

    try:
        scale = guarded.call("app.query")
        if not isinstance(scale, list):
            raise ValueError(f"app.query returned {type(scale).__name__}")
        scale_by_name = {a.get("name") or a.get("id"): a for a in scale if isinstance(a, dict)}
    except DryRunViolation:
        raise
    except Exception as e:  # noqa: BLE001 - middleware down or reshaped
        return fail(f"app.query failed: {type(e).__name__}")

    in_git = {e["name"] for e in desired.get("apps", [])} | set(EXCLUDED_SCALE_APPS)
    unmanaged = sorted(n for n in scale_by_name if n not in in_git)

    results = []
    unevaluated: list[str] = []
    for e in entries:
        if e["status"] == "excluded":
            continue
        app = scale_by_name.get(e["name"])
        try:
            stored = None
            containers: dict[str, dict] = {}
            if e["status"] == "managed" and app is not None and app.get("custom_app"):
                try:
                    stored = guarded.call("app.config", e["name"])
                except DryRunViolation:
                    raise
                except Exception as ex:  # noqa: BLE001
                    log(f"{e['name']}: app.config failed: {type(ex).__name__}")
                    stored = None
                containers = app_containers(e["name"], docker)
            results.append(evaluate_app(e, app, stored, containers))
        except DryRunViolation:
            raise
        except DockerUnavailable as ex:
            # Neither synced nor pending: no app_state series this tick, and
            # the heartbeat reports a failed tick (last_status=1).
            log(f"{e['name']}: unevaluated this tick: docker {ex}")
            unevaluated.append(e["name"])
        except Exception as ex:  # noqa: BLE001 - one bad app must not stop the tick
            # Type only: the exception text could carry a stored value.
            results.append({"app": e["name"], "file": e["file"], "state": "invalid",
                            "reasons": [f"evaluation error: {type(ex).__name__}"], "changes": [],
                            "drift": [], "blocked": [], "merged": None, "stopped": False})

    would, deferred = plan_applies(results)
    mode = "DRY-RUN" if dry_run else "APPLY"
    counts: dict[str, int] = {}
    for r in results:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    log(f"tick {mode} sha={sha[:7]} {json.dumps(counts, sort_keys=True)} "
        f"pending={[r['app'] for r in results if r['state'] == 'pending']} "
        f"would_apply={would} deferred={deferred} global_pause={global_paused} unmanaged={unmanaged}"
        + (f" unevaluated={unevaluated}" if unevaluated else ""))
    for r in results:
        if r["state"] in ("synced", "archived"):
            continue
        detail = []
        if r["changes"]:
            detail.append("changes: " + "; ".join(r["changes"]))
        if r["drift"]:
            detail.append("drift: " + "; ".join(r["drift"]))
        if r["reasons"]:
            detail.append("reason: " + "; ".join(r["reasons"]))
        if r.get("stopped"):
            detail.append("app STOPPED: would update, never start")
        log(f"  {r['app']} [{r['state']}] ({r['file']}) " + " | ".join(detail))

    # Phase 1: nothing is applied. In Phase 2 the apply loop over `would`
    # (apply_app + verify + per-(app, sha) backoff + self-apply) goes here.

    state["last_tick"] = {"at": int(started), "sha": sha,
                          "states": {r["app"]: r["state"] for r in results},
                          "would_apply": would, "unevaluated": unevaluated}
    state["applied"] = applied
    # results carry merged payloads (possibly secret values): persist states only.
    atomic_write(os.path.join(data_dir, "state.json"), json.dumps(state, sort_keys=True), mode=0o600)
    atomic_write(prom, render_metrics(
        ok=not unevaluated, now=int(started), prev_success=prev_ok, duration=int(time.time() - started),
        dry_run=dry_run, sha=sha, commit_seconds=desired.get("commit_time"),
        results=results, global_paused=global_paused, unmanaged=len(unmanaged), applied=applied))
    return 1 if unevaluated else 0


def reconcile_main() -> int:
    from truenas_api_client import Client  # host-only dependency

    dry_run = dry_run_from_env()
    with Client() as c:
        return reconcile(c, dry_run=dry_run)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch":
        sys.exit(fetch_main())
    if cmd == "reconcile":
        sys.exit(reconcile_main())
    print("usage: deployer.py fetch|reconcile", file=sys.stderr)
    sys.exit(2)
