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
  - Every docker call goes through docker_read(), which allows a short list
    of EXACT argv shapes (check_read_argv). Anything else (compose pull,
    compose up, run, rm, stop, ...) raises DryRunViolation before a process
    starts.
  - apply_compose() raises before its first subprocess call while mutations
    are off, and reconcile() never calls it.

VALIDATION IS AN ALLOWLIST, ON WHAT COMPOSE PARSED (plan A3). Two passes:

  1. validate_git(): the raw Git file, for what parsing hides: ${VAR}
     interpolation, bare env keys (compose fills them from ITS environment),
     the reserved ownership label, x-deploy keys, and the key allowlists (so
     env_file / label_file / include / extends never reach the compose CLI,
     which would read those files inside this container).
  2. validate_config(): `docker compose config --format json`, i.e. what `up`
     would apply: long-syntax volumes with their real sources, booleans
     converted ("true" -> true). Service keys, top-level keys, volume types,
     top-level volumes and networks are each checked against an allowlist.
     Bind sources must be the app's own /volume1/docker/<name> subtree or an
     x-deploy.allow-host-paths entry, and those entries may never overlap a
     denied root (the deployer's DATA_DIR, /volume1/docker, system paths).

THE ALLOW-LIST COMES FROM GIT. reconcile() reads the evaluated commit from the
bare git cache (refs/deployer/head, checked against desired.json's sha) and
rediscovers every app from it. desired.json only says WHICH commit; a tampered
desired.json cannot widen an app's allow-list.

WHAT IT COMPARES (plan A4). Desired = `docker compose config --hash '*'` of
the Git compose with the ownership label added to every service; actual =
each container's com.docker.compose.config-hash label. That is the hash
`up -d` compares, so "pending" means "up -d would recreate". It also hashes
the compose WITHOUT the label: a project made by hand from the same file
reads `adoptable` ("matches Git apart from ownership"), not just `blocked`.
Limits: top-level networks/volumes are not in any service hash (detected
against the baseline instead), and a mutable tag that moves on the registry
changes no hash (pin images by digest).

SECRETS. Everything `config` prints is held in memory only: never logged,
never written. docker inspect is asked for labels, image and state through
--format, so Config.Env never leaves the engine. The log-safe "what changed"
is a fingerprint tree (every leaf replaced by sha256(value)[:12], images in
clear) diffed against a baseline tree in state.json. Subprocess errors are
logged by exception type only.

Exit codes: 0 evaluated, 1 could not evaluate (metric written), 2 bad input.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deployer as d  # noqa: E402

BACKEND = "compose"
# deployer.STATES plus the compose-only state (plan A4.5, review of #1890).
STATES = d.STATES + ("adoptable",)

OWNER_LABEL = "homelab.burntbytes.com/deployed-by"
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
HASH_LABEL = "com.docker.compose.config-hash"
ONEOFF_FILTER = "label=com.docker.compose.oneoff=False"

# ------------------------------------------------------------------ mount policy
# An allowlist of mountable roots (plan A3, hardened in review of #1890):
#   - /volume1/docker/<name>: the app's own subtree;
#   - x-deploy.allow-host-paths entries from Git, each of which must not
#     overlap (be under, or contain) any denied root below;
#   - for the deployer itself only: the docker socket.
# Everything else is refused, including other apps' subtrees.
OWN_ROOT = "/volume1/docker"
SELF_SOCKET = "/var/run/docker.sock"
DENY_EXACT = frozenset({"/", "/volume1", "/volume2", "/volumeUSB1"})
DENY_ROOTS = ("/etc", "/proc", "/sys", "/boot", "/dev", "/var/run", "/run", "/root", "/var/lib",
              "/volume1/docker", "/volume2", "/volumeUSB1", "/volume1/@iSCSI", "/volume1/@docker",
              "/volume1/@appstore")

# ------------------------------------------------------------------ key allowlists
TOP_KEYS = frozenset({"name", "services", "networks", "volumes"})
SERVICE_KEYS = frozenset({
    "image", "command", "entrypoint", "environment", "labels", "container_name", "hostname", "domainname",
    "restart", "user", "working_dir", "healthcheck", "ports", "expose", "networks", "network_mode", "volumes",
    "tmpfs", "mem_limit", "mem_reservation", "memswap_limit", "mem_swappiness", "cpus", "cpu_shares",
    "pids_limit", "ulimits", "stop_signal", "stop_grace_period", "init", "read_only", "dns", "dns_search",
    "extra_hosts", "logging", "depends_on", "deploy", "shm_size", "platform", "pull_policy", "tty",
    "stdin_open", "cap_drop",
})
NETWORK_MODES = ("none", "bridge", "default")
DEPLOY_RESOURCE_KEYS = {"limits": frozenset({"cpus", "memory", "pids"}),
                        "reservations": frozenset({"cpus", "memory"})}
VOLUME_TYPES = frozenset({"bind", "volume", "tmpfs"})
MOUNT_KEYS = {"bind": frozenset({"type", "source", "target", "read_only", "bind"}),
              "volume": frozenset({"type", "source", "target", "read_only", "volume"}),
              "tmpfs": frozenset({"type", "target", "read_only", "tmpfs"})}
BIND_OPTS = frozenset({"create_host_path", "propagation"})
BIND_PROPAGATION = (None, "rprivate", "private")
VOLUME_OPTS = frozenset({"nocopy", "subpath"})
TOP_VOLUME_KEYS = frozenset({"name", "labels", "driver"})
TOP_NETWORK_KEYS = frozenset({"name", "ipam", "labels", "internal", "driver", "enable_ipv6", "attachable"})

# docker inspect fields the deployer reads (plan A4.2). Never .Config.Env.
INSPECT_FORMAT = ("{{json .Name}}\t{{json .Config.Labels}}\t{{json .Config.Image}}\t"
                  "{{json .State.Running}}\t{{json .State.Restarting}}\t{{.RestartCount}}")
PS_FORMAT = '{{.Names}}\t{{.Label "' + PROJECT_LABEL + '"}}'
VERSION_FORMAT = "{{.Client.Version}} {{.Server.Version}}"
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
CONFIG_FLAGS = (["--hash", "*"], ["--format", "json"], ["--format", "json", "--no-interpolate"])

# Environment passed to the docker CLI. Nothing else: compose interpolates
# from its environment, and the deployer's own env must never reach a config.
CHILD_ENV_KEYS = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_API_VERSION", "TZ")


def log(msg: str) -> None:
    d.log(msg)


def _is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def _overlaps(a: str, b: str) -> bool:
    return _is_under(a, b) or _is_under(b, a)


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


# ================================================================ validation (pure)


def allow_entry_reasons(entry, data_dir: str) -> list[str]:
    """Reasons an x-deploy.allow-host-paths entry is itself unacceptable."""
    if not isinstance(entry, str) or not entry.startswith("/"):
        return ["x-deploy.allow-host-paths entries must be absolute paths"]
    p = posixpath.normpath(entry)
    if p in DENY_EXACT or p.startswith("/volume1/@") or any(_overlaps(p, r) for r in DENY_ROOTS) \
            or _overlaps(p, posixpath.normpath(data_dir)):
        return [f"x-deploy.allow-host-paths: {p} overlaps a denied root (never mountable)"]
    return []


def validate_meta(meta: dict, data_dir: str) -> tuple[list[str], list[str]]:
    """(reasons, normalised allow-list) for the compose-only x-deploy keys."""
    reasons = []
    adopt = meta.get("adopt")
    if adopt is not None and not isinstance(adopt, bool):
        reasons.append(f"x-deploy.adopt must be a YAML bool, got {adopt!r}")
    raw = meta.get("allow-host-paths")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        return reasons + ["x-deploy.allow-host-paths must be a list of absolute paths"], []
    allow = []
    for a in raw:
        bad = allow_entry_reasons(a, data_dir)
        reasons.extend(bad)
        if not bad:
            allow.append(posixpath.normpath(a))
    return reasons, allow


def validate_git(compose: dict, meta: dict, data_dir: str | None = None) -> list[str]:
    """Pass 1, on the raw Git compose (x-* stripped): what parsing would hide.

    Reasons name paths, never values.
    """
    data_dir = d.DATA_DIR if data_dir is None else data_dir
    reasons, _ = validate_meta(meta, data_dir)
    for p, s in _strings(compose):
        if "$" in s.replace("$$", ""):
            reasons.append(f"{p}: ${{VAR}} interpolation is refused in v1")
    for k in sorted(set(compose) - TOP_KEYS, key=str):
        reasons.append(f"{k}: top-level key not allowed in v1")
    for svc, body in sorted((compose.get("services") or {}).items()):
        if not isinstance(body, dict):
            continue
        for k in sorted(set(body) - SERVICE_KEYS, key=str):
            reasons.append(f"services.{svc}.{k}: not on the v1 service-key allowlist")
        env = body.get("environment")
        bare = []
        if isinstance(env, list):
            bare = [str(i) for i, e in enumerate(env) if "=" not in str(e)]
        elif isinstance(env, dict):
            bare = [str(k) for k, v in env.items() if v is None]
        for k in bare:
            # compose fills a bare key from ITS OWN environment: the deployer's.
            reasons.append(f"services.{svc}.environment.{k}: a key without a value is refused in v1")
        labels = body.get("labels")
        if isinstance(labels, list):
            labels = d._kv_list_to_map(labels)
        if isinstance(labels, dict) and OWNER_LABEL in labels:
            reasons.append(f"services.{svc}.labels: {OWNER_LABEL} is set by the deployer")
    return reasons


def mount_reasons(where: str, src, name: str, self_name: str, allow: list[str], data_dir: str) -> list[str]:
    if not isinstance(src, str) or not src.startswith("/"):
        # The compose CLI runs inside the deployer container: a relative or
        # ~ source would resolve against the container's filesystem.
        return [f"{where}: bind source must be an absolute host path"]
    hp = posixpath.normpath(src)
    if name == self_name and hp == SELF_SOCKET:
        return []
    own = f"{OWN_ROOT}/{name}"
    if _is_under(hp, own):
        if name != self_name and _overlaps(hp, posixpath.normpath(data_dir)):
            return [f"{where}: {hp} is the deployer's data directory"]
        return []
    if any(_is_under(hp, a) for a in allow):
        return []
    return [f"{where}: {hp} is not mountable (only {own}/... and x-deploy.allow-host-paths)"]


def validate_config(cfg: dict, name: str, allow: list[str], self_name: str | None = None,
                    data_dir: str | None = None) -> list[str]:
    """Pass 2, on `docker compose config --format json`: what `up` would apply."""
    self_name = d.SELF_NAME if self_name is None else self_name
    data_dir = d.DATA_DIR if data_dir is None else data_dir
    reasons: list[str] = []
    if not isinstance(cfg, dict) or not isinstance(cfg.get("services"), dict):
        return ["docker compose config: unrecognised shape"]
    for k in sorted(set(cfg) - TOP_KEYS, key=str):
        reasons.append(f"{k}: top-level key not allowed in v1")
    top_volumes = cfg.get("volumes") or {}
    for vname, v in sorted(top_volumes.items()):
        v = v or {}
        for k in sorted(set(v) - TOP_VOLUME_KEYS, key=str):
            reasons.append(f"volumes.{vname}.{k}: not allowed in v1 (driver_opts, external, ...)")
        if v.get("driver") not in (None, "local"):
            reasons.append(f"volumes.{vname}.driver: only the local driver")
    for nname, n in sorted((cfg.get("networks") or {}).items()):
        n = n or {}
        for k in sorted(set(n) - TOP_NETWORK_KEYS, key=str):
            reasons.append(f"networks.{nname}.{k}: not allowed in v1 (external, driver_opts, ...)")
        if n.get("driver") not in (None, "bridge"):
            reasons.append(f"networks.{nname}.driver: only bridge")
    for svc, body in sorted(cfg["services"].items()):
        w = f"services.{svc}"
        if not isinstance(body, dict):
            reasons.append(f"{w}: not a mapping")
            continue
        for k in sorted(set(body) - SERVICE_KEYS, key=str):
            reasons.append(f"{w}.{k}: not on the v1 service-key allowlist")
        nm = body.get("network_mode")
        if nm is not None and nm not in NETWORK_MODES and not str(nm).startswith("service:"):
            reasons.append(f"{w}.network_mode: only none, bridge or service:<name>")
        dep = body.get("deploy")
        if dep is not None:
            if not isinstance(dep, dict) or set(dep) - {"resources"}:
                reasons.append(f"{w}.deploy: only deploy.resources")
            else:
                res = dep.get("resources") or {}
                for part, keys in ((p, res.get(p) or {}) for p in res):
                    allowed = DEPLOY_RESOURCE_KEYS.get(part)
                    if allowed is None or not isinstance(keys, dict) or set(keys) - allowed:
                        reasons.append(f"{w}.deploy.resources.{part}: only cpus/memory/pids")
        for i, m in enumerate(body.get("volumes") or []):
            mw = f"{w}.volumes[{i}]"
            if not isinstance(m, dict):
                reasons.append(f"{mw}: unrecognised mount")
                continue
            typ = m.get("type")
            if typ not in VOLUME_TYPES:
                reasons.append(f"{mw}: mount type {typ} not allowed in v1")
                continue
            for k in sorted(set(m) - MOUNT_KEYS[typ], key=str):
                reasons.append(f"{mw}.{k}: not allowed in v1")
            if typ == "bind":
                opts = m.get("bind") or {}
                if set(opts) - BIND_OPTS or opts.get("propagation") not in BIND_PROPAGATION:
                    reasons.append(f"{mw}.bind: only create_host_path and private propagation")
                reasons.extend(mount_reasons(mw, m.get("source"), name, self_name, allow, data_dir))
            elif typ == "volume":
                if set(m.get("volume") or {}) - VOLUME_OPTS:
                    reasons.append(f"{mw}.volume: only nocopy/subpath")
                src = m.get("source")
                if src is not None and src not in top_volumes:
                    reasons.append(f"{mw}: volume {src} is not declared in this compose")
    return reasons


def discover(files: dict[str, str], load_yaml, data_dir: str | None = None) -> list[dict]:
    """deployer.discover() plus validate_git(), per app.

    A failure makes that one app `invalid`, never the whole tick. Adds `adopt`
    and the validated `allow_host_paths` to each managed entry. These are for
    reading only: reconcile() takes them from the git cache, not desired.json.
    """
    data_dir = d.DATA_DIR if data_dir is None else data_dir
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
        reasons = validate_git(e["compose"], meta, data_dir)
        if reasons:
            e.update(status="invalid", reason="; ".join(reasons), compose=None)
            continue
        e["adopt"] = meta.get("adopt") is True
        e["allow_host_paths"] = validate_meta(meta, data_dir)[1]
    return entries


def with_owner_label(compose: dict, owner: str) -> dict:
    """The compose with OWNER_LABEL=owner on every service (plan A4.5)."""
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
    """The config with every leaf replaced by sha256(value)[:12]; images clear."""
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
                         owner: str, plain_hashes: dict[str, str] | None = None, tree=None, baseline=None,
                         config_error: str | None = None) -> dict:
    """Per-app decision. Same result shape as deployer.evaluate_app().

    hashes: service -> desired hash WITH the ownership label; plain_hashes:
    the same without it. baseline: a fingerprint tree recorded earlier.
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
    plain_hashes = plain_hashes or {}

    by_svc: dict[str, list[dict]] = {}
    for c in containers:
        by_svc.setdefault(c.get("service") or "", []).append(c)
    services = set(hashes)
    stopped = services <= set(by_svc) and not any(c["running"] or c["restarting"] for c in containers)
    res["stopped"] = stopped
    owned = all(c.get("owner") == owner for c in containers)

    drift = []
    plain_match = True
    for svc in sorted(services):
        cs = by_svc.get(svc)
        if not cs:
            drift.append(f"{svc}: no container")
            plain_match = False
            continue
        for c in sorted(cs, key=lambda c: c["name"]):
            if c.get("hash") != hashes[svc]:
                if c.get("hash") == plain_hashes.get(svc):
                    drift.append(f"{svc}: matches Git apart from the ownership label ({c['name']})")
                else:
                    drift.append(f"{svc}: config hash differs ({c['name']})")
                    plain_match = False
            elif not stopped and (not c["running"] or c["restarting"]):
                drift.append(f"{svc}: container not running ({c['name']})")
    for svc in sorted(set(by_svc) - services):
        plain_match = False
        for c in sorted(by_svc[svc], key=lambda c: c["name"]):
            drift.append(f"{svc or '<no service label>'}: orphan container {c['name']} (up --remove-orphans removes it)")

    if isinstance(baseline, dict) and isinstance(tree, dict):
        paths = d.diff_paths(tree, baseline)
        top = [p for p in paths if not p.startswith("services.") and p != "services"]
        if top:
            # No service hash covers top-level networks/volumes.
            drift.append("top-level config changed (no service hash covers it): " + ", ".join(top))
        if drift:
            res["changes"] = d.describe_paths(paths, tree, baseline)
    res["drift"] = drift

    if entry.get("paused"):
        res["state"] = "paused"
    elif not owned and not entry.get("adopt"):
        if plain_match:
            res["state"] = "adoptable"
            res["reasons"] = ["matches Git apart from the ownership label; set x-deploy.adopt: true "
                              "for one deploy to take it over"]
        else:
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
    """Raise DryRunViolation unless args is EXACTLY one of the read shapes."""
    a = [str(x) for x in args]

    def refuse(what="read-only"):
        raise d.DryRunViolation(f"refusing docker {a[0] if a else ''}: {what}")

    if not a:
        refuse("no subcommand")
    if a[0] == "ps":
        if a == ["ps", "-a", "--no-trunc", "--format", PS_FORMAT]:
            return
        if len(a) == 8 and a[:5] == ["ps", "-a", "-q", "--no-trunc", "--filter"] \
                and a[5].startswith(f"label={PROJECT_LABEL}=") \
                and d.APP_NAME_RE.match(a[5].split("=", 2)[2]) and a[6:] == ["--filter", ONEOFF_FILTER]:
            return
        refuse("unexpected ps shape")
    if a[0] == "inspect":
        if a[:5] == ["inspect", "--type", "container", "--format", INSPECT_FORMAT] and len(a) > 5 \
                and all(CONTAINER_ID_RE.match(x) for x in a[5:]):
            return
        refuse("unexpected inspect shape")
    if a[0] == "version":
        if a == ["version", "--format", VERSION_FORMAT]:
            return
        refuse("unexpected version shape")
    if a[0] != "compose":
        refuse()
    if a == ["compose", "version", "--short"]:
        return
    if len(a) >= 9 and a[1:4] == ["-f", "-", "-p"] and d.APP_NAME_RE.match(a[4]) \
            and a[5] == "--project-directory" and a[6].startswith("/") and a[7] == "config" \
            and a[8:] in CONFIG_FLAGS:
        return
    refuse("only compose config is allowed")


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
    sequence itself lands before alcatraz's cutover (A-Phase 3).
    """
    if not d.mutations_allowed(dry_run, BACKEND):
        raise d.DryRunViolation(f"refusing to apply {name}: dry-run={dry_run} "
                                f"APPLY_ENABLED={d.apply_enabled(BACKEND)}")
    raise NotImplementedError("the compose apply path lands before alcatraz's cutover (A-Phase 3)")


def compose_args(name: str, project_dir: str, *config_flags: str) -> list[str]:
    return ["compose", "-f", "-", "-p", name, "--project-directory", project_dir, "config", *config_flags]


def render_config(entry: dict, docker, project_dir: str, owner: str, *, allow: list[str],
                  self_name: str | None = None, data_dir: str | None = None):
    """(hashes, plain hashes, fingerprint tree, config_error) for one entry.

    Order matters: validate_git() has already passed, so compose never sees a
    file reference. The rendered config stays in this function's locals; only
    hashes and the fingerprint (of the interpolated, normalised form: what
    `up` applies) leave it.
    """
    name = entry["name"]
    owned = json.dumps(with_owner_label(entry["compose"], owner), sort_keys=True)
    plain = json.dumps(entry["compose"], sort_keys=True)
    try:
        hashes = parse_hashes(docker(*compose_args(name, project_dir, "--hash", "*"), stdin=owned))
        plain_hashes = parse_hashes(docker(*compose_args(name, project_dir, "--hash", "*"), stdin=plain))
        raw = json.loads(docker(*compose_args(name, project_dir, "--format", "json", "--no-interpolate"), stdin=owned))
        cfg = json.loads(docker(*compose_args(name, project_dir, "--format", "json"), stdin=owned))
    except d.DryRunViolation:
        raise
    except FileNotFoundError as e:
        raise d.DockerUnavailable(type(e).__name__) from None
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        # Type only: compose's stderr can quote the file.
        return None, None, None, f"docker compose config failed: {type(e).__name__}"
    # The interpolated and uninterpolated forms are not comparable whole: the
    # interpolated one is also normalised (compose 5.6.1). The rule is on the
    # uninterpolated form, as compose read it: no unescaped $ (plan A3).
    leaked = [p for p, v in _strings(raw) if "$" in v.replace("$$", "")]
    if leaked:
        return None, None, None, f"interpolation refused in v1 ({', '.join(sorted(leaked)[:3])})"
    reasons = validate_config(cfg, name, allow, self_name, data_dir)
    if reasons:
        return None, None, None, "; ".join(reasons)
    return hashes, plain_hashes, fingerprint(cfg), None


def _container_info(line: str) -> dict:
    name, labels, image, running, restarting, restarts = line.split("\t")
    labels = json.loads(labels) or {}
    return {"name": json.loads(name).lstrip("/"), "service": labels.get(SERVICE_LABEL),
            "hash": labels.get(HASH_LABEL), "owner": labels.get(OWNER_LABEL), "image": json.loads(image),
            "running": json.loads(running) is True, "restarting": json.loads(restarting) is True,
            "restart_count": int(restarts)}


def project_containers(name: str, docker=docker_read) -> list[dict]:
    """Service containers of compose project <name> (one-off `run` ones excluded)."""
    try:
        ids = docker("ps", "-a", "-q", "--no-trunc", "--filter", f"label={PROJECT_LABEL}={name}",
                     "--filter", ONEOFF_FILTER).split()
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


def entries_from_cache(data_dir: str, sha: str) -> list[dict]:
    """Rediscover every app from the bare git cache at `sha`.

    The allow-list and every other x-deploy key come from Git here, never
    from desired.json. The cache's refs/deployer/head must be `sha`.
    """
    import yaml  # container-only dependency (py3-yaml)

    git_dir = os.path.join(data_dir, "cache.git")
    head = d.git("rev-parse", "refs/deployer/head", git_dir=git_dir).strip()
    if head != sha:
        raise ValueError("desired.json sha is not the git cache head")
    return discover(d.read_tree(git_dir, sha), yaml.safe_load, data_dir)


# ================================================================ reconcile


def reconcile(*, docker=docker_read, data_dir: str = d.DATA_DIR, textfile_dir: str = d.TEXTFILE_DIR,
              dry_run: bool = True, now: float | None = None, owner: str | None = None,
              self_name: str | None = None) -> int:
    """One shadow tick: read the evaluated commit and the engine, report."""
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
    sha = desired["sha"]
    try:
        all_entries = entries_from_cache(data_dir, sha)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return fail(f"git cache unreadable: {type(e).__name__}")
    except ImportError as e:
        return fail(f"yaml unavailable: {type(e).__name__}")
    entries = [e for e in all_entries if not e.get("shadowed")]
    try:
        project_dir = ensure_project_dir(data_dir)
    except (OSError, ValueError) as e:
        return fail(f"compose project directory unusable: {type(e).__name__}")
    try:
        projects, loose = inventory(docker)
    except d.DockerUnavailable as e:
        return fail(f"docker ps failed: {e}")

    in_git = {e["name"] for e in all_entries} | set(d.EXCLUDED_SCALE_APPS)
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
            hashes, plain, tree, err = render_config(e, docker, project_dir, owner, allow=e["allow_host_paths"],
                                                     self_name=self_name, data_dir=data_dir)
            containers = project_containers(e["name"], docker) if not err else []
            base = (baselines.get(e["name"]) or {}).get("tree")
            r = evaluate_compose_app(e, hashes, containers, owner=owner, plain_hashes=plain, tree=tree,
                                     baseline=base, config_error=err)
            # Baseline: recorded on first evaluation, moved whenever live
            # matches Git (synced, or adoptable: same config, not yet owned).
            if tree is not None and (base is None or r["state"] in ("synced", "adoptable")):
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

    live = {r["app"] for r in results} | set(unevaluated)
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
        v = docker("version", "--format", VERSION_FORMAT).strip()
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
