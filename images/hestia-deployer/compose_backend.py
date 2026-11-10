#!/usr/bin/env python3
"""compose_backend: the docker-compose backend of hestia-deployer (HOMELAB-118).

Plan: docs/plans/2026-11-09-alcatraz-pull-deployer-plan.md (A1-A7). alcatraz
runs the hestia-deployer image with DEPLOYER_BACKEND=compose; deployer.sh then
runs this file IN THE CONTAINER for both halves of a tick. There is no nsenter
and no host python: the docker CLI talks to the host engine over the mounted
socket. deployer.py stays one stdlib-only file for hestia's
`nsenter ... python3 - reconcile < deployer.py`; this file imports it for the
shared pieces (fetch, discovery, diff_paths, plan_applies, metrics, the log
format).

THIS IS A-PHASE 1: SHADOW. It reads Git and the docker engine, works out what
it WOULD do, and reports it. It never changes a container. That is
structural:

  - deployer.APPLY_ENABLED["compose"] is False. hestia's cutover flips
    "truenas" only; alcatraz's own cutover PR flips this one.
  - DRY_RUN defaults ON (deployer.dry_run_from_env).
  - Every docker call goes through docker_read(), an allowlist of exact argv
    shapes: ps, inspect, image inspect, version, compose version, and
    `compose -f - -p <name> --project-directory <dir> config` with only
    --hash / --format json / --no-interpolate. Anything else (compose pull,
    compose up, run, rm, stop, ...) raises DryRunViolation before a process
    starts.
  - apply_compose() raises before its first subprocess call while mutations
    are off, and reconcile() never calls it.

WHAT IT COMPARES (plan A4). Desired = `docker compose config --hash '*'` of
the Git compose (with the ownership label added to every service); actual =
each container's com.docker.compose.config-hash label. That is the hash
`up -d` compares, so "pending" means exactly "up -d would recreate".

SECRETS. Everything `config` prints is held in memory only: never logged,
never written. docker inspect is asked for labels, image and state through
--format, so Config.Env never leaves the engine. The log-safe "what changed"
is a fingerprint tree (every leaf replaced by sha256(value)[:12], images in
clear) diffed against the last synced tree in state.json. Subprocess errors
are logged by exception type only. Interpolation (${VAR}), env_file,
secrets/configs `file:`, include and extends are refused in v1: they would
read values from the container's filesystem or environment.

Exit codes: 0 evaluated, 1 could not evaluate (metric written), 2 bad input.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import posixpath
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deployer as d  # noqa: E402

BACKEND = "compose"

OWNER_LABEL = "homelab.burntbytes.com/deployed-by"
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
HASH_LABEL = "com.docker.compose.config-hash"

# The protected-path guard, ported from .github/workflows/alcatraz-deploy.yaml
# (plan A3). It now runs per app in the deployer, on what would be applied.
#   HARD_DENY_EXACT: a bare filesystem / volume root, never mountable.
#   HARD_DENY_PREFIX: these and everything under them, never mountable, even
#     through x-deploy.allow-host-paths.
#   PROTECTED_PREFIX: mountable only when listed in x-deploy.allow-host-paths.
#   /volume1/docker/<name>: the app's own subtree, always allowed.
HARD_DENY_EXACT = frozenset({"/", "/volume1", "/volume2", "/volumeUSB1"})
HARD_DENY_PREFIX = ("/etc", "/proc", "/sys", "/boot", "/dev", "/var/run", "/run", "/volume1/@iSCSI")
PROTECTED_PREFIX = ("/volume1/homes", "/volume1/family", "/volume1/photo", "/volume1/music", "/volume1/video")
OWN_ROOT = "/volume1/docker"
# The one exemption, in code (plan A3): the deployer itself may mount the
# docker socket. Nothing else on the deny lists.
SELF_SOCKET = "/var/run/docker.sock"

# docker inspect fields the deployer reads (plan A4.2). Never .Config.Env.
INSPECT_FORMAT = ("{{json .Name}}\t{{json .Config.Labels}}\t{{json .Config.Image}}\t"
                  "{{json .State.Running}}\t{{json .State.Restarting}}\t{{.RestartCount}}")
PS_FORMAT = '{{.Names}}\t{{.Label "' + PROJECT_LABEL + '"}}'

# Environment passed to the docker CLI. Nothing else: compose interpolates
# from its environment, and the deployer's own env must never reach a config.
CHILD_ENV_KEYS = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_API_VERSION", "TZ")


def log(msg: str) -> None:
    d.log(msg)


# ================================================================ validation (pure)


def _is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def bind_sources(compose: dict) -> list[tuple[str, str]]:
    """(where, host source) for every bind mount in the compose.

    Short syntax "src:dst[:mode]": a src starting with / . or ~ is a bind
    (named volumes have none; a lone "/path" is an anonymous volume). Long
    syntax: type bind (or no type with a path-like source). Top-level volumes
    of the local driver with o=bind and a device are binds too.
    """
    out = []
    for svc, body in sorted((compose.get("services") or {}).items()):
        if not isinstance(body, dict):
            continue
        vols = body.get("volumes") or []
        if not isinstance(vols, list):
            out.append((f"services.{svc}.volumes", "<not a list>"))
            continue
        for i, vol in enumerate(vols):
            where = f"services.{svc}.volumes[{i}]"
            if isinstance(vol, str):
                if ":" not in vol:
                    continue
                src = vol.split(":", 1)[0]
                if src[:1] in ("/", ".", "~"):
                    out.append((where, src))
            elif isinstance(vol, dict):
                src = vol.get("source")
                typ = vol.get("type")
                if typ == "bind" or (typ is None and isinstance(src, str) and src[:1] in ("/", ".", "~")):
                    out.append((where, src if isinstance(src, str) else "<no source>"))
    for vname, vbody in sorted((compose.get("volumes") or {}).items()):
        opts = (vbody or {}).get("driver_opts") if isinstance(vbody, dict) else None
        if isinstance(opts, dict) and "bind" in str(opts.get("o", "")).split(","):
            dev = opts.get("device")
            out.append((f"volumes.{vname}.driver_opts.device", dev if isinstance(dev, str) else "<no device>"))
    return out


def _strings(node, path=""):
    """(path, string) for every string key and value in the tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else str(k)
            if isinstance(k, str):
                yield p, k
            yield from _strings(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def validate(compose: dict, meta: dict, name: str, self_name: str | None = None) -> list[str]:
    """Plan A3. Reasons this app is invalid on the compose backend; [] if fine.

    Reasons name paths and host directories (Git content, public), never a
    value from the environment or the host.
    """
    self_name = d.SELF_NAME if self_name is None else self_name
    reasons: list[str] = []

    adopt = meta.get("adopt")
    if adopt is not None and not isinstance(adopt, bool):
        reasons.append(f"x-deploy.adopt must be a YAML bool, got {adopt!r}")
    allow_raw = meta.get("allow-host-paths") or []
    if not isinstance(allow_raw, list) or not all(isinstance(a, str) and a.startswith("/") for a in allow_raw):
        reasons.append("x-deploy.allow-host-paths must be a list of absolute paths")
        allow_raw = []
    allow = [posixpath.normpath(a) for a in allow_raw]

    # No interpolation, no files read inside the container (plan A3).
    for p, s in _strings(compose):
        if "$" in s.replace("$$", ""):
            reasons.append(f"{p}: ${{VAR}} interpolation is refused in v1")
    for top in ("include",):
        if top in compose:
            reasons.append(f"{top}: refused in v1 (reads files inside the deployer container)")
    for kind in ("secrets", "configs"):
        for k, v in sorted((compose.get(kind) or {}).items()) if isinstance(compose.get(kind), dict) else []:
            if isinstance(v, dict) and ("file" in v or "environment" in v):
                reasons.append(f"{kind}.{k}: file/environment sources are refused in v1")

    for svc, body in sorted((compose.get("services") or {}).items()):
        if not isinstance(body, dict):
            continue
        if "env_file" in body:
            reasons.append(f"services.{svc}.env_file: refused in v1 (values would land in docker inspect)")
        if "build" in body:
            reasons.append(f"services.{svc}.build: refused (no working tree; images only)")
        if "extends" in body:
            reasons.append(f"services.{svc}.extends: refused in v1")
        if body.get("privileged") is True:
            reasons.append(f"services.{svc}.privileged: refused")
        if body.get("network_mode") == "host":
            reasons.append(f"services.{svc}.network_mode: host is refused")
        if body.get("pid") == "host":
            reasons.append(f"services.{svc}.pid: host is refused")
        if body.get("cap_add"):
            reasons.append(f"services.{svc}.cap_add: refused")
        labels = body.get("labels")
        if isinstance(labels, list):
            labels = d._kv_list_to_map(labels)
        if isinstance(labels, dict) and OWNER_LABEL in labels:
            reasons.append(f"services.{svc}.labels: {OWNER_LABEL} is set by the deployer")

    own = f"{OWN_ROOT}/{name}"
    for where, src in bind_sources(compose):
        if src.startswith("<"):
            reasons.append(f"{where}: unparseable bind mount")
            continue
        if not src.startswith("/"):
            # The compose CLI runs inside the deployer container: a relative or
            # ~ source would resolve against the container's filesystem.
            reasons.append(f"{where}: relative or ~ bind source {src} (use an absolute host path)")
            continue
        hp = posixpath.normpath(src)
        if name == self_name and hp == SELF_SOCKET:
            continue
        if _is_under(hp, own):
            continue
        if hp in HARD_DENY_EXACT or any(_is_under(hp, p) for p in HARD_DENY_PREFIX):
            reasons.append(f"{where}: {hp} is hard-denied (never mountable)")
        elif any(_is_under(hp, p) for p in PROTECTED_PREFIX) and not any(_is_under(hp, a) for a in allow):
            reasons.append(f"{where}: {hp} is protected (add it to x-deploy.allow-host-paths if intended)")
    return reasons


def discover(files: dict[str, str], load_yaml, self_name: str | None = None) -> list[dict]:
    """deployer.discover() plus the compose validator, per app.

    A failure makes that one app `invalid`, never the whole tick. Adds
    `adopt` and `allow_host_paths` to each managed entry (Git content).
    """
    self_name = d.SELF_NAME if self_name is None else self_name
    entries = d.discover(files, load_yaml)
    for e in entries:
        if e["status"] != "managed":
            continue
        try:
            doc = load_yaml(files[e["file"]]) or {}
        except Exception as ex:  # noqa: BLE001 - parsed once already; belt and braces
            e.update(status="invalid", reason=f"YAML parse error: {type(ex).__name__}", compose=None)
            continue
        meta = doc.get("x-deploy") or {}
        reasons = validate(e["compose"], meta, e["name"], self_name)
        if reasons:
            e.update(status="invalid", reason="; ".join(reasons), compose=None)
            continue
        e["adopt"] = meta.get("adopt") is True
        e["allow_host_paths"] = list(meta.get("allow-host-paths") or [])
    return entries


def with_owner_label(compose: dict, owner: str) -> dict:
    """The compose with OWNER_LABEL=owner on every service (plan A4.5).

    Part of what is hashed on the predict side and applied on the apply side,
    so a deployer-owned container and a hand-made one never hash alike.
    """
    out = copy.deepcopy(compose)
    for body in (out.get("services") or {}).values():
        if not isinstance(body, dict):
            continue
        labels = body.get("labels")
        if isinstance(labels, list):
            labels = d._kv_list_to_map(labels)
        labels = dict(labels or {})
        labels[OWNER_LABEL] = owner
        body["labels"] = labels
    return out


def fingerprint(node, path: str = ""):
    """The config with every leaf replaced by sha256(value)[:12]; images clear.

    diff_paths() over two fingerprints names what changed without a value
    ever being held on disk (plan A4.4).
    """
    if isinstance(node, dict):
        return {k: fingerprint(v, f"{path}.{k}" if path else str(k)) for k, v in node.items()}
    if isinstance(node, list):
        return [fingerprint(v, f"{path}[{i}]") for i, v in enumerate(node)]
    if d.IMAGE_PATH_RE.match(path) and isinstance(node, str):
        return node
    return "h:" + hashlib.sha256(json.dumps(node, sort_keys=True).encode()).hexdigest()[:12]


def parse_hashes(text: str) -> dict[str, str]:
    """`config --hash '*'` output: one "<service> <hash>" line per service."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
        elif parts:
            raise ValueError("unexpected config --hash line")
    return out


# ================================================================ evaluate (pure)


def evaluate_compose_app(entry: dict, hashes: dict[str, str] | None, containers: list[dict], *,
                         owner: str, tree=None, baseline=None, config_error: str | None = None) -> dict:
    """Per-app decision. Same result shape as deployer.evaluate_app().

    hashes: service -> desired config hash. containers: from
    project_containers(). baseline: the fingerprint tree of the last synced
    (later: last applied) config, or None.
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
    if config_error:
        res["reasons"] = [config_error]
        return res
    if not hashes:
        res["reasons"] = ["docker compose config produced no services"]
        return res
    if not containers:
        res["state"] = "missing"
        res["reasons"] = ["no containers for this compose project" +
                          ("; x-deploy.create is not acted on until cutover" if entry.get("create") else "")]
        return res

    by_svc: dict[str, list[dict]] = {}
    for c in containers:
        by_svc.setdefault(c.get("service") or "", []).append(c)
    services = set(hashes)
    stopped = services <= set(by_svc) and not any(c["running"] or c["restarting"] for c in containers)
    res["stopped"] = stopped

    drift = []
    for svc in sorted(services):
        cs = by_svc.get(svc)
        if not cs:
            drift.append(f"{svc}: no container")
            continue
        for c in sorted(cs, key=lambda c: c["name"]):
            if c.get("hash") != hashes[svc]:
                drift.append(f"{svc}: config hash differs ({c['name']})")
            elif not stopped and (not c["running"] or c["restarting"]):
                drift.append(f"{svc}: container not running ({c['name']})")
    for svc in sorted(set(by_svc) - services):
        for c in sorted(by_svc[svc], key=lambda c: c["name"]):
            drift.append(f"{svc or '<no service label>'}: orphan container {c['name']} (up --remove-orphans removes it)")
    res["drift"] = drift

    if drift and isinstance(baseline, dict) and isinstance(tree, dict):
        res["changes"] = d.describe_paths(d.diff_paths(tree, baseline), tree, baseline)

    owned = all(c.get("owner") == owner for c in containers)
    if entry.get("paused"):
        res["state"] = "paused"
    elif not owned and not entry.get("adopt"):
        res["state"] = "blocked"
        res["reasons"] = ["project exists and is not deployer-owned; set x-deploy.adopt: true for one deploy"]
    elif drift:
        res["state"] = "pending"
        if not owned:
            res["reasons"] = ["x-deploy.adopt: would take ownership"]
    else:
        res["state"] = "synced"
    return res


# ================================================================ docker (guarded I/O)


def check_read_argv(args) -> None:
    """Raise DryRunViolation unless args is one of the allowed read shapes."""
    a = [str(x) for x in args]
    if not a:
        raise d.DryRunViolation("refusing docker with no subcommand")
    if a[0] in ("ps", "inspect", "version"):
        return
    if a[:2] == ["image", "inspect"]:
        return
    if a[0] != "compose":
        raise d.DryRunViolation(f"refusing docker {a[0]}: read-only")
    if a[1:] in (["version"], ["version", "--short"]):
        return
    i = 1
    seen = set()
    while i < len(a) and a[i] in ("-f", "-p", "--project-directory"):
        flag = a[i]
        if flag in seen or i + 1 >= len(a):
            raise d.DryRunViolation("refusing docker compose: malformed global flags")
        if flag == "-f" and a[i + 1] != "-":
            raise d.DryRunViolation("refusing docker compose -f <file>: stdin only")
        seen.add(flag)
        i += 2
    if seen != {"-f", "-p", "--project-directory"} or i >= len(a) or a[i] != "config":
        raise d.DryRunViolation(f"refusing docker compose {a[i] if i < len(a) else ''}: read-only")
    i += 1
    while i < len(a):
        if a[i] == "--no-interpolate":
            i += 1
        elif a[i] == "--hash" and i + 1 < len(a):
            i += 2
        elif a[i] == "--format" and i + 1 < len(a) and a[i + 1] == "json":
            i += 2
        else:
            raise d.DryRunViolation("refusing docker compose config: unexpected flag")


def _child_env() -> dict[str, str]:
    return {k: os.environ[k] for k in CHILD_ENV_KEYS if k in os.environ}


def docker_read(*args: str, stdin: str | None = None) -> str:
    """The only way this file runs docker. Allowlisted argv, minimal env."""
    check_read_argv(args)
    return subprocess.run(["docker", *args], input=stdin, check=True, capture_output=True, text=True,
                          timeout=120, env=_child_env(), cwd="/").stdout


def apply_compose(name: str, *, dry_run: bool, docker=docker_read) -> bool:
    """pull -> up -d --remove-orphans --wait -> settle (plan A5). Cutover only.

    Refuses before any subprocess call while mutations are off. The apply
    sequence itself lands with alcatraz's cutover (A-Phase 3).
    """
    if not d.mutations_allowed(dry_run, BACKEND):
        raise d.DryRunViolation(f"refusing to apply {name}: dry-run={dry_run} "
                                f"APPLY_ENABLED={d.apply_enabled(BACKEND)}")
    raise NotImplementedError("the compose apply path lands with alcatraz's cutover (A-Phase 3)")


def compose_args(name: str, project_dir: str, *config_flags: str) -> list[str]:
    return ["compose", "-f", "-", "-p", name, "--project-directory", project_dir, "config", *config_flags]


def render_config(entry: dict, docker, project_dir: str, owner: str):
    """(hashes, fingerprint tree, config_error) for one managed entry.

    The rendered config stays in this function's locals; only hashes and the
    fingerprint (of the interpolated, normalised form: what `up` applies)
    leave it.
    """
    body = json.dumps(with_owner_label(entry["compose"], owner), sort_keys=True)
    name = entry["name"]
    try:
        hashes = parse_hashes(docker(*compose_args(name, project_dir, "--hash", "*"), stdin=body))
        raw = json.loads(docker(*compose_args(name, project_dir, "--format", "json", "--no-interpolate"), stdin=body))
        interp = json.loads(docker(*compose_args(name, project_dir, "--format", "json"), stdin=body))
    except d.DryRunViolation:
        raise
    except FileNotFoundError as e:
        raise d.DockerUnavailable(type(e).__name__) from None
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        # Type only: compose's stderr can quote the file.
        return None, None, f"docker compose config failed: {type(e).__name__}"
    # The two forms are not comparable whole: the interpolated one is also
    # normalised (defaults filled in, keys reordered; measured with compose
    # 5.6.1). So the check is on the uninterpolated form, as compose itself
    # parsed it: no string may still hold an unescaped $ (plan A3: an unset
    # ${VAR} becomes "" silently). validate() already refused these in Git;
    # this is the same rule on what compose actually read.
    leaked = [p for p, v in _strings(raw) if "$" in v.replace("$$", "")]
    if leaked:
        return None, None, f"interpolation refused in v1 ({', '.join(sorted(leaked)[:3])})"
    return hashes, fingerprint(interp), None


def _container_info(line: str) -> dict:
    name, labels, image, running, restarting, restarts = line.split("\t")
    labels = json.loads(labels) or {}
    return {"name": json.loads(name).lstrip("/"), "service": labels.get(SERVICE_LABEL),
            "hash": labels.get(HASH_LABEL), "owner": labels.get(OWNER_LABEL), "image": json.loads(image),
            "running": json.loads(running) is True, "restarting": json.loads(restarting) is True,
            "restart_count": int(restarts)}


def project_containers(name: str, docker=docker_read) -> list[dict]:
    """Containers of compose project <name>: labels, image and state only."""
    try:
        ids = docker("ps", "-a", "-q", "--no-trunc", "--filter", f"label={PROJECT_LABEL}={name}").split()
        if not ids:
            return []
        out = docker("inspect", "--type", "container", "--format", INSPECT_FORMAT, *ids)
        return [_container_info(ln) for ln in out.splitlines() if ln.strip()]
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as e:
        raise d.DockerUnavailable(type(e).__name__) from None


def inventory(docker=docker_read) -> tuple[set[str], list[str]]:
    """(compose project names, names of containers with no project label)."""
    try:
        out = docker("ps", "-a", "--no-trunc", "--format", PS_FORMAT)
    except (subprocess.SubprocessError, OSError) as e:
        raise d.DockerUnavailable(type(e).__name__) from None
    projects, loose = set(), []
    for line in out.splitlines():
        if not line.strip():
            continue
        cname, _, project = line.partition("\t")
        if project.strip():
            projects.add(project.strip())
        else:
            loose.append(cname.strip())
    return projects, sorted(loose)


def ensure_project_dir(data_dir: str) -> str:
    """A fixed, empty --project-directory: compose reads .env from it."""
    path = os.path.join(data_dir, "compose-project")
    os.makedirs(path, mode=0o755, exist_ok=True)
    if os.listdir(path):
        raise ValueError("compose project directory is not empty")
    return path


# ================================================================ reconcile


def reconcile(*, docker=docker_read, data_dir: str = d.DATA_DIR, textfile_dir: str = d.TEXTFILE_DIR,
              dry_run: bool = True, now: float | None = None, owner: str | None = None,
              self_name: str | None = None) -> int:
    """One shadow tick: read Git's desired.json and the engine, report."""
    owner = d.SELF_NAME if owner is None else owner
    self_name = d.SELF_NAME if self_name is None else self_name
    started = time.time() if now is None else now
    dry_run = dry_run or not d.apply_enabled(BACKEND)
    prom = os.path.join(textfile_dir, d.DEPLOY_TEXTFILE)
    prev_text = ""
    try:
        with open(prom) as f:
            prev_text = f.read()
    except OSError:
        pass
    prev_ok = d.previous_success(prev_text)
    state = d.load_json(os.path.join(data_dir, "state.json"))
    applied = state.get("applied") or {}
    baselines = state.get("baseline") or {}
    global_paused = os.path.exists(os.path.join(data_dir, "PAUSE"))

    def fail(msg: str) -> int:
        log(f"cannot evaluate: {msg}")
        d.atomic_write(prom, d.render_metrics(ok=False, now=int(started), prev_success=prev_ok,
                                              duration=int(time.time() - started), dry_run=dry_run,
                                              global_paused=global_paused, applied=applied))
        return 1

    desired = d.load_json(os.path.join(data_dir, "desired.json"))
    if desired.get("version") != d.DESIRED_VERSION or not d.SHA_RE.match(str(desired.get("sha", ""))):
        return fail("no usable desired.json yet (fetch has not succeeded)")
    entries = [e for e in desired.get("apps", []) if not e.get("shadowed")]
    sha = desired["sha"]
    try:
        project_dir = ensure_project_dir(data_dir)
    except (OSError, ValueError) as e:
        return fail(f"compose project directory unusable: {type(e).__name__}")
    try:
        projects, loose = inventory(docker)
    except d.DockerUnavailable as e:
        return fail(f"docker ps failed: {e}")

    in_git = {e["name"] for e in desired.get("apps", [])} | set(d.EXCLUDED_SCALE_APPS)
    unmanaged = sorted(p for p in projects if p not in in_git) + [f"container:{n}" for n in loose]

    results = []
    unevaluated: list[str] = []
    for e in entries:
        if e["status"] == "excluded":
            continue
        try:
            if e["status"] != "managed":
                results.append(evaluate_compose_app(e, None, [], owner=owner))
                continue
            # Re-validate at evaluation time: guard what would be applied.
            meta = {"adopt": e.get("adopt", False), "allow-host-paths": e.get("allow_host_paths", [])}
            reasons = validate(e["compose"], meta, e["name"], self_name)
            if reasons:
                results.append(evaluate_compose_app({**e, "status": "invalid", "reason": "; ".join(reasons)},
                                                    None, [], owner=owner))
                continue
            hashes, tree, err = render_config(e, docker, project_dir, owner)
            containers = project_containers(e["name"], docker) if not err else []
            base = (baselines.get(e["name"]) or {}).get("tree")
            r = evaluate_compose_app(e, hashes, containers, owner=owner, tree=tree, baseline=base, config_error=err)
            if r["state"] == "synced" and tree is not None:
                baselines[e["name"]] = {"sha": sha, "tree": tree}
            results.append(r)
        except d.DryRunViolation:
            raise
        except d.DockerUnavailable as ex:
            log(f"{e['name']}: unevaluated this tick: docker {ex}")
            unevaluated.append(e["name"])
        except Exception as ex:  # noqa: BLE001 - one bad app must not stop the tick
            results.append({"app": e["name"], "file": e["file"], "state": "invalid",
                            "reasons": [f"evaluation error: {type(ex).__name__}"], "changes": [],
                            "drift": [], "blocked": [], "merged": None, "stopped": False})

    would, deferred = d.plan_applies(results)
    d.log_tick(dry_run=dry_run, sha=sha, results=results, would=would, deferred=deferred,
               global_paused=global_paused, unmanaged=unmanaged, unevaluated=unevaluated,
               stopped_note="project STOPPED: would update with up --no-start, never start")

    # A-Phase 1: nothing is applied. At cutover the apply loop over `would`
    # (apply_compose + verify + per-(app, sha) backoff + self-apply) goes here.

    live = {r["app"] for r in results}
    state["last_tick"] = {"at": int(started), "sha": sha, "backend": BACKEND,
                          "states": {r["app"]: r["state"] for r in results},
                          "would_apply": would, "unevaluated": unevaluated}
    state["applied"] = applied
    state["baseline"] = {k: v for k, v in baselines.items() if k in live}
    d.atomic_write(os.path.join(data_dir, "state.json"), json.dumps(state, sort_keys=True), mode=0o600)
    d.atomic_write(prom, d.render_metrics(
        ok=not unevaluated, now=int(started), prev_success=prev_ok, duration=int(time.time() - started),
        dry_run=dry_run, sha=sha, commit_seconds=desired.get("commit_time"),
        results=results, global_paused=global_paused, unmanaged=len(unmanaged), applied=applied))
    return 1 if unevaluated else 0


def version_main(docker=docker_read) -> int:
    """Log the docker client/engine and compose versions once at startup (plan A8)."""
    try:
        v = docker("version", "--format", "{{.Client.Version}} {{.Server.Version}}").strip()
        c = docker("compose", "version", "--short").strip()
        log(f"compose backend: docker client/engine {v}, compose {c}")
        return 0
    except (subprocess.SubprocessError, OSError) as e:
        log(f"compose backend: docker version failed ({type(e).__name__})")
        return 1


def fetch_main() -> int:
    return d.fetch_main(discover_fn=discover)


def reconcile_main() -> int:
    return reconcile(dry_run=d.dry_run_from_env())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch":
        sys.exit(fetch_main())
    if cmd == "reconcile":
        sys.exit(reconcile_main())
    if cmd == "version":
        sys.exit(version_main())
    print("usage: compose_backend.py fetch|reconcile|version", file=sys.stderr)
    sys.exit(2)
