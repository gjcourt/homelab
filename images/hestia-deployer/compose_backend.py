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
  - Every MUTATING docker call (preflight run, compose pull, compose up) goes
    through docker_write(), which refuses before a process starts unless
    mutations are allowed for this backend, and then allows only its own
    exact argv shapes (check_write_argv).
  - apply_compose() raises before its first subprocess call while mutations
    are off, and reconcile() only calls it when they are on.

THE APPLY PATH (plan A5; off until the cutover flips APPLY_ENABLED["compose"]).
Per app, one at a time, in plan_applies() order (cap 3, self last):
  1. `compose pull`: a wrong-arch or missing image fails here, before
     anything is stopped.
  2. preflight, immediately before `up` (after the pull, so the window
     between check and use is as short as it can be): a throwaway helper
     container from the deployer's own image, with only the needed host
     roots bind-mounted read-only under /host (never /run or /var/run, so the
     docker socket is not reachable), no network, a read-only root, every
     capability dropped except DAC_READ_SEARCH and no-new-privileges, resolves
     every bind source, allow-list entry and DATA_DIR to its REAL path on the
     host (resolve_under). Each resolved source is re-checked against the same
     mount policy. A missing, unreadable, looping or non-file/dir source
     blocks the app; so does a /var/log source that is not a single file.
  3. `compose up -d --remove-orphans --wait --wait-timeout 300`, or
     `up --no-start --remove-orphans` for a project stopped on purpose.
  4. settle: SETTLE_SECONDS later, every service's containers must carry the
     desired hash, be running (unless stopped on purpose) and not have
     restarted. A one-shot service (one that exits by design) therefore
     FAILS the settle check: v1 supports long-running services only.
The race the preflight cannot close by itself (a container swapping a path it
can write for a symlink between the check and `up`) is closed by the mount
policy: a bind source must be EXACTLY the app's own /volume1/docker/<name> or
EXACTLY an allow-list entry, and no source may lie under any writable bind of
any app (nested_write_reasons). So no app can write to the parent of anything
that is bind-mounted. A failure makes the app `failed`, with a per-(app, sha)
backoff of 1 h; a new SHA retries at once. There is no automatic rollback.
Self-apply (the detached helper of plan A5) is not implemented: the deployer
refuses to apply its own project.

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
# /var/log as a whole: an allow-list entry may name a single log file under
# it (immich-photos-pull does), never the directory; the preflight also
# requires a /var/log source to resolve to a regular file.
DENY_EXACT = frozenset({"/", "/volume1", "/volume2", "/volumeUSB1", "/var/log"})
FILES_ONLY_ROOTS = ("/var/log",)
# Every DSM volume but /volume1 (and USB volumes): not this repo's to mount.
OTHER_VOLUME_RE = re.compile(r"^/volume(?!1(/|$))[^/]*(/|$)")
DENY_ROOTS = ("/etc", "/proc", "/sys", "/boot", "/dev", "/var/run", "/run", "/root", "/var/lib",
              "/usr", "/usr/syno", "/opt", "/var/packages",
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
# `name` is allowed only as compose's own default, <project>_<key>: any other
# name borrows an existing volume or network (another project's, a hand-made
# bind volume, `host`). Checked in validate_config().
TOP_VOLUME_KEYS = frozenset({"name", "labels", "driver"})
TOP_NETWORK_KEYS = frozenset({"name", "ipam", "labels", "internal", "driver", "enable_ipv6", "attachable"})
LOGGING_DRIVERS = (None, "json-file", "local")

# docker inspect fields the deployer reads (plan A4.2). Never .Config.Env.
INSPECT_FORMAT = ("{{json .Name}}\t{{json .Config.Labels}}\t{{json .Config.Image}}\t"
                  "{{json .State.Running}}\t{{json .State.Restarting}}\t{{.RestartCount}}")
PS_FORMAT = '{{.Names}}\t{{.Label "' + PROJECT_LABEL + '"}}'
VERSION_FORMAT = "{{.Client.Version}} {{.Server.Version}}"
SELF_IMAGE_FORMAT = "{{.Image}}"
# Mount sources and RW flags of every container (never Env): who can write what.
MOUNTS_FORMAT = "{{json .Name}}\t{{json .Mounts}}"
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


# Path components the preflight helper can mount (its argv guard uses the same
# rule): no spaces or shell-ish characters, and never `.` or `..`. Checked at
# validation time, so such a path makes the app `invalid` instead of tripping
# the guard during an armed tick.
PATH_COMPONENT_RE = re.compile(r"^(?!\.\.?$)[A-Za-z0-9._@-]+$")


def path_chars_ok(path: str) -> bool:
    comps = [c for c in posixpath.normpath(path).split("/") if c]
    return bool(comps) and all(PATH_COMPONENT_RE.match(c) for c in comps)


def allow_entry_reasons(entry, data_dir: str) -> list[str]:
    """Reasons an x-deploy.allow-host-paths entry is itself unacceptable."""
    if not isinstance(entry, str) or not entry.startswith("/"):
        return ["x-deploy.allow-host-paths entries must be absolute paths"]
    if not path_chars_ok(entry):
        return ["x-deploy.allow-host-paths entries may use only [A-Za-z0-9._@-] path components"]
    p = posixpath.normpath(entry)
    if p in DENY_EXACT or p.startswith("/volume1/@") or any(_overlaps(p, r) for r in DENY_ROOTS) \
            or OTHER_VOLUME_RE.match(p) \
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


def _underscore_keys(doc: dict) -> list[str]:
    """Top-level volume / network keys may not contain `_`.

    Their names must be <project>_<key>, and with `_` in a key that name is
    ambiguous: project `foo` key `a_b` and project `foo_a` key `b` are both
    `foo_a_b`, so one app could claim another's resource.
    """
    out = []
    for kind in ("volumes", "networks"):
        block = doc.get(kind)
        if isinstance(block, dict):
            out.extend(f"{kind}.{k}: `_` is not allowed in a top-level {kind[:-1]} key"
                       for k in sorted(block, key=str) if "_" in str(k))
    return out


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
    reasons.extend(_underscore_keys(compose))
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
    if not path_chars_ok(hp):
        return [f"{where}: bind source path components may use only [A-Za-z0-9._@-]"]
    own = f"{OWN_ROOT}/{name}"
    if hp == own:
        if name != self_name and _overlaps(hp, posixpath.normpath(data_dir)):
            return [f"{where}: {hp} is the deployer's data directory"]
        return []
    if hp in allow:
        return []
    if _is_under(hp, own):
        # A sub-path of a tree the app writes could be swapped for a symlink
        # between the preflight and `up`.
        return [f"{where}: {hp} is a sub-path of the app's own subtree; bind {own} itself"]
    return [f"{where}: {hp} is not mountable (only {own} itself or an exact x-deploy.allow-host-paths entry)"]


def nested_write_reasons(binds: list, writable: list[tuple[str, str]]) -> list[str]:
    """Sources that lie under a WRITABLE bind of any app (incl. this one).

    writable: (app, path) for every non-read-only bind source in the tick. A
    container that can write a directory can replace anything below it with
    a symlink, so nothing below it may be bind-mounted.
    """
    out = []
    for where, src, _ro in binds:
        p = posixpath.normpath(src)
        for app, w in writable:
            if p != w and _is_under(p, w):
                out.append(f"{where}: {p} lies under {w}, which {app} mounts writable")
    return out


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
    reasons.extend(_underscore_keys(cfg))
    top_volumes = cfg.get("volumes") or {}
    for vname, v in sorted(top_volumes.items()):
        v = v or {}
        for k in sorted(set(v) - TOP_VOLUME_KEYS, key=str):
            reasons.append(f"volumes.{vname}.{k}: not allowed in v1 (driver_opts, external, ...)")
        if v.get("driver") not in (None, "local"):
            reasons.append(f"volumes.{vname}.driver: only the local driver")
        if "name" in v and v["name"] != f"{name}_{vname}":
            reasons.append(f"volumes.{vname}.name: only the project's own volume ({name}_{vname})")
    for nname, n in sorted((cfg.get("networks") or {}).items()):
        n = n or {}
        for k in sorted(set(n) - TOP_NETWORK_KEYS, key=str):
            reasons.append(f"networks.{nname}.{k}: not allowed in v1 (external, driver_opts, ...)")
        if n.get("driver") not in (None, "bridge"):
            reasons.append(f"networks.{nname}.driver: only bridge")
        if "name" in n and n["name"] != f"{name}_{nname}":
            reasons.append(f"networks.{nname}.name: only the project's own network ({name}_{nname})")
    for svc, body in sorted(cfg["services"].items()):
        w = f"services.{svc}"
        if not isinstance(body, dict):
            reasons.append(f"{w}: not a mapping")
            continue
        for k in sorted(set(body) - SERVICE_KEYS, key=str):
            reasons.append(f"{w}.{k}: not on the v1 service-key allowlist")
        logging = body.get("logging")
        if logging is not None and (not isinstance(logging, dict) or logging.get("driver") not in LOGGING_DRIVERS
                                    or set(logging) - {"driver", "options"}):
            reasons.append(f"{w}.logging: only the json-file or local driver")
        cn = body.get("container_name")
        if cn is not None and cn not in (name, f"{name}-{svc}"):
            # A name another app (or the deployer) uses would make `up` fight
            # it; reconcile also refuses one equal to another app's name.
            reasons.append(f"{w}.container_name: must be {name} or {name}-{svc}")
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
                          ("; x-deploy.create: planned for creation (applied only once the compose backend "
                           "is armed at cutover)" if entry.get("create") else "")]
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
            # No service hash covers top-level networks/volumes. Live does not
            # match Git then, so the project is not `adoptable` either.
            drift.append("top-level config changed (no service hash covers it): " + ", ".join(top))
            plain_match = False
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
        if a in (["ps", "-a", "--no-trunc", "--format", PS_FORMAT], ["ps", "-a", "-q", "--no-trunc"]):
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
        if a[:5] == ["inspect", "--type", "container", "--format", MOUNTS_FORMAT] and len(a) > 5 \
                and all(CONTAINER_ID_RE.match(x) for x in a[5:]):
            return
        if len(a) == 6 and a[:5] == ["inspect", "--type", "container", "--format", SELF_IMAGE_FORMAT] \
                and CONTAINER_ID_RE.match(a[5]):
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


# ------------------------------------------------------------------ mutating calls

HOST_ROOT = "/host"
RESOLVE_SCRIPT = "/app/compose_backend.py"
# Every capability dropped except DAC_READ_SEARCH: enough to lstat/readlink
# through DSM's 0700 home directories, nothing that writes. Host roots are
# mounted one by one, each read-only (preflight_roots); `/` itself is never
# mounted, because a plain read-only bind is not recursive on Docker < 25 or
# kernels < 5.12, and /run and /var/run (the docker socket) never are.
PREFLIGHT_RUN = ["run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                 "--cap-add", "DAC_READ_SEARCH", "--security-opt", "no-new-privileges"]
PREFLIGHT_TAIL = ["--entrypoint", "python3"]
PREFLIGHT_NEVER_ROOTS = ("/run", "/var/run", "/proc", "/sys", "/dev")
_ROOT_COMPONENT = r"(?!\.\.?(?:/|,))[A-Za-z0-9._@-]+"
PREFLIGHT_MOUNT_RE = re.compile(rf"^type=bind,source=(/{_ROOT_COMPONENT}(?:/{_ROOT_COMPONENT})?),"
                                rf"target={HOST_ROOT}\1,readonly$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
WAIT_TIMEOUT_SECONDS = 300
SETTLE_SECONDS = int(d.env_param("SETTLE_SECONDS", "60", re.compile(r"^[0-9]{1,4}$")))
BACKOFF_SECONDS = 3600
UP_FLAGS = (["up", "-d", "--remove-orphans", "--wait", "--wait-timeout", str(WAIT_TIMEOUT_SECONDS)],
            ["up", "--no-start", "--remove-orphans"])


def check_write_argv(args) -> None:
    """Raise DryRunViolation unless args is EXACTLY one of the mutating shapes."""
    a = [str(x) for x in args]
    if a[:len(PREFLIGHT_RUN)] == PREFLIGHT_RUN:
        rest = a[len(PREFLIGHT_RUN):]
        while rest[:1] == ["--mount"] and len(rest) >= 2:
            m = PREFLIGHT_MOUNT_RE.match(rest[1])
            if not m or any(_overlaps(m.group(1), r) for r in PREFLIGHT_NEVER_ROOTS):
                raise d.DryRunViolation("refusing docker run: preflight mount not allowed")
            rest = rest[2:]
        if rest[:2] == PREFLIGHT_TAIL and len(rest) >= 7 and IMAGE_ID_RE.match(rest[2]) \
                and rest[3:7] == ["-I", "-B", RESOLVE_SCRIPT, "resolve"] and all(p.startswith("/") for p in rest[7:]):
            return
        raise d.DryRunViolation("refusing docker run: not the preflight shape")
    if len(a) >= 8 and a[:4] == ["compose", "-f", "-", "-p"] and d.APP_NAME_RE.match(a[4]) \
            and a[5] == "--project-directory" and a[6].startswith("/") \
            and (a[7:] == ["pull", "--quiet"] or a[7:] in [list(u) for u in UP_FLAGS]):
        return
    raise d.DryRunViolation(f"refusing docker {a[0] if a else ''}: not an apply shape")


def docker_write(*args: str, stdin: str | None = None, dry_run: bool = True) -> str:
    """The only way this file runs a MUTATING docker command.

    Refuses before any process while mutations are off for this backend.
    """
    if not d.mutations_allowed(dry_run, BACKEND):
        raise d.DryRunViolation(f"refusing docker {args[:1]}: mutations are off "
                                f"(dry-run={dry_run} APPLY_ENABLED={d.apply_enabled(BACKEND)})")
    check_write_argv(args)
    return subprocess.run(["docker", *args], input=stdin, check=True, capture_output=True, text=True,
                          timeout=WAIT_TIMEOUT_SECONDS + 120, env=_child_env(), cwd="/").stdout


def resolve_under(root: str, path: str, max_links: int = 40) -> tuple[str | None, str]:
    """(real path, kind) of `path` as the host sees it, with the host's / at `root`.

    Symlinks are followed inside `root`: an absolute target restarts at
    root, never at this process's own /. kind: file | dir | other |
    missing | loop | error. Runs in the preflight helper container.
    """
    import stat as st_

    parts = [x for x in path.split("/") if x]
    done: list[str] = []
    links = 0
    while parts:
        c = parts.pop(0)
        if c == ".":
            continue
        if c == "..":
            if done:
                done.pop()
            continue
        full = os.path.join(root, *done, c)
        try:
            info = os.lstat(full)
        except FileNotFoundError:
            return None, "missing"
        except OSError:
            return None, "error"
        if st_.S_ISLNK(info.st_mode):
            links += 1
            if links > max_links:
                return None, "loop"
            target = os.readlink(full)
            if target.startswith("/"):
                done = []
            parts = [x for x in target.split("/") if x] + parts
            continue
        done.append(c)
    real = "/" + "/".join(done)
    try:
        info = os.stat(os.path.join(root, *done))
    except OSError:
        return None, "error"
    kind = "dir" if st_.S_ISDIR(info.st_mode) else "file" if st_.S_ISREG(info.st_mode) else "other"
    return real, kind


def preflight_roots(paths) -> list[str]:
    """The host roots the helper needs: the first path component (two under
    /var and /usr), deduplicated, never one of PREFLIGHT_NEVER_ROOTS. A
    symlink pointing outside them resolves as missing, which fails closed."""
    roots = set()
    for p in paths:
        comps = [c for c in posixpath.normpath(p).split("/") if c]
        if not comps:
            continue
        r = "/" + "/".join(comps[:2] if comps[0] in ("var", "usr") and len(comps) > 1 else comps[:1])
        if not any(_overlaps(r, n) for n in PREFLIGHT_NEVER_ROOTS):
            roots.add(r)
    return sorted(r for r in roots if not any(o != r and _is_under(r, o) for o in roots))


def preflight_argv(image: str, paths: list[str]) -> list[str]:
    mounts = []
    for r in preflight_roots(paths):
        mounts += ["--mount", f"type=bind,source={r},target={HOST_ROOT}{r},readonly"]
    return [*PREFLIGHT_RUN, *mounts, *PREFLIGHT_TAIL, image, "-I", "-B", RESOLVE_SCRIPT, "resolve", *paths]


def preflight_paths(args) -> list[str]:
    """The paths a preflight argv asks about (for fakes and logs)."""
    a = list(args)
    return a[a.index("resolve") + 1:]


def resolve_main(paths: list[str]) -> int:
    print(json.dumps({p: resolve_under(HOST_ROOT, p) for p in paths}))
    return 0


def bind_sources(cfg: dict) -> list[tuple[str, str, bool]]:
    """(where, source, read_only) of every bind mount in a parsed config."""
    out = []
    for svc, body in sorted((cfg.get("services") or {}).items()):
        for i, m in enumerate((body or {}).get("volumes") or []):
            if isinstance(m, dict) and m.get("type") == "bind":
                out.append((f"services.{svc}.volumes[{i}]", m.get("source"), m.get("read_only") is True))
    return out


def self_image(docker=docker_read) -> str:
    """The image ID of the running deployer container (the preflight's image)."""
    cid = os.environ.get("HOSTNAME", "")
    if not CONTAINER_ID_RE.match(cid):
        raise ValueError("HOSTNAME is not a container id")
    image = docker("inspect", "--type", "container", "--format", SELF_IMAGE_FORMAT, cid).strip()
    if not IMAGE_ID_RE.match(image):
        raise ValueError("unexpected image id")
    return image


def preflight(entry: dict, binds: list, *, image: str, write, self_name: str,
              data_dir: str, writable: list | None = None) -> list[str]:
    """Plan A5 preflight: resolve on the host, re-check the policy. [] = go.

    Every bind source and allow-list entry must EQUAL its own realpath (no
    symlink anywhere in it), so the lexical policy is the real one. The
    nested-write rule is then re-checked against the RESOLVED sources of
    every live read-write mount (`writable`, from live_writable()).
    """
    allow = list(entry.get("allow_host_paths") or [])
    sources = sorted({posixpath.normpath(b[1]) for b in binds if isinstance(b[1], str)})
    if not sources:
        return []
    others = sorted({w for _, w in (writable or []) if path_chars_ok(w)})
    asked = sorted(set(sources) | set(allow) | {posixpath.normpath(data_dir)} | set(others))
    out = write(*preflight_argv(image, asked))
    resolved = json.loads(out)
    if not isinstance(resolved, dict):
        raise ValueError("unexpected preflight output")

    def real(p):
        r = resolved.get(p) or [None, "error"]
        return r[0], r[1]

    real_allow = []
    reasons = []
    for a in allow:
        ra, _ = real(a)
        if ra != a:
            # A symlink in an allow-list entry (DSM's /var/services/homes, say):
            # list the real path instead.
            reasons.append(f"x-deploy.allow-host-paths: {a} is not its own realpath ({ra}); list the real path")
        elif not allow_entry_reasons(ra, data_dir):
            real_allow.append(ra)
    real_data = real(posixpath.normpath(data_dir))[0] or posixpath.normpath(data_dir)
    real_writable = []
    for c, w in (writable or []):
        rw = real(w)[0] if w in others else None
        real_writable.append((c, rw or w))
    for where, src, *_ in binds:
        p = posixpath.normpath(src)
        rp, kind = real(p)
        if rp is None:
            reasons.append(f"{where}: bind source {p} is {kind} on the host")
            continue
        if kind not in ("file", "dir"):
            reasons.append(f"{where}: bind source {p} is a {kind} (device, fifo or socket), not a file or directory")
            continue
        if rp != p:
            reasons.append(f"{where}: {p} is not its own realpath ({rp}): bind the real path")
            continue
        if any(_is_under(rp, r) for r in FILES_ONLY_ROOTS) and kind != "file":
            reasons.append(f"{where}: {p} resolves to a {kind} under /var/log (single files only)")
        # The own-subtree rule stays lexical on the RESOLVED path: if
        # /volume1/docker/<name> is itself a link elsewhere, its target is
        # not the app's own subtree.
        bad = mount_reasons(where, rp, entry["name"], self_name, real_allow, real_data)
        if bad:
            reasons.append(f"{where}: {p} resolves to {rp}, which the mount policy refuses")
            continue
        reasons.extend(nested_write_reasons([(where, rp, True)], real_writable))
    return reasons


def apply_compose(entry: dict, result: dict, *, dry_run: bool, docker=docker_read, write=docker_write,
                  project_dir: str = "", owner: str = "", self_name: str = "", data_dir: str = "",
                  binds: list | None = None, hashes: dict | None = None, sleep=time.sleep,
                  writable: list | None = None) -> tuple[bool, str]:
    """preflight -> pull -> up -> settle for one app (plan A5). (ok, reason).

    Refuses before any subprocess call while mutations are off.
    """
    name = entry["name"] if isinstance(entry, dict) else str(entry)
    if not d.mutations_allowed(dry_run, BACKEND):
        raise d.DryRunViolation(f"refusing to apply {name}: dry-run={dry_run} "
                                f"APPLY_ENABLED={d.apply_enabled(BACKEND)}")
    if name == self_name:
        return False, "self-apply is not implemented (plan A5): George re-runs the bootstrap command"

    def w(*args, stdin=None):
        return write(*args, stdin=stdin, dry_run=dry_run)

    try:
        image = self_image(docker)
        body = json.dumps(with_owner_label(entry["compose"], owner), sort_keys=True)
        base = ["compose", "-f", "-", "-p", name, "--project-directory", project_dir]
        w(*base, "pull", "--quiet", stdin=body)
        # Preflight AFTER the pull, immediately before `up`: the shortest
        # window between check and use.
        try:
            reasons = preflight(entry, binds or [], image=image, write=w, self_name=self_name,
                                data_dir=data_dir, writable=writable)
        except d.DryRunViolation:
            if not d.mutations_allowed(dry_run, BACKEND):
                raise
            # Armed, so this is the argv guard refusing this app's preflight
            # (a path it cannot mount): this app fails, the tick goes on.
            return False, "preflight refused by the argv guard"
        if reasons:
            return False, "preflight: " + "; ".join(reasons)
        w(*base, *(UP_FLAGS[1] if result.get("stopped") else UP_FLAGS[0]), stdin=body)
        before = {c["name"]: c["restart_count"] for c in project_containers(name, docker)}
        sleep(SETTLE_SECONDS)
        after = project_containers(name, docker)
    except d.DryRunViolation:
        raise
    except Exception as e:  # noqa: BLE001 - any failure is this app's, recorded with backoff
        # Type only: compose's stderr can quote the file.
        return False, f"apply step failed: {type(e).__name__}"
    by_svc: dict[str, list[dict]] = {}
    for c in after:
        by_svc.setdefault(c.get("service") or "", []).append(c)
    for svc, want in sorted((hashes or {}).items()):
        cs = by_svc.get(svc) or []
        if not cs:
            return False, f"verify: {svc} has no container"
        for c in cs:
            if c.get("hash") != want or c.get("owner") != owner:
                return False, f"verify: {svc} ({c['name']}) does not carry the desired config"
            if not result.get("stopped") and (not c["running"] or c["restarting"]):
                return False, f"verify: {svc} ({c['name']}) is not running"
            if c["restart_count"] > before.get(c["name"], c["restart_count"]):
                return False, f"verify: {svc} ({c['name']}) restarted within {SETTLE_SECONDS}s"
    return True, ""


def compose_args(name: str, project_dir: str, *config_flags: str) -> list[str]:
    return ["compose", "-f", "-", "-p", name, "--project-directory", project_dir, "config", *config_flags]


def render_config(entry: dict, docker, project_dir: str, owner: str, *, allow: list[str],
                  self_name: str | None = None, data_dir: str | None = None):
    """(hashes, plain hashes, fingerprint tree, bind sources, config_error) for one entry.

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
        return None, None, None, None, f"docker compose config failed: {type(e).__name__}"
    # The interpolated and uninterpolated forms are not comparable whole: the
    # interpolated one is also normalised (compose 5.6.1). The rule is on the
    # uninterpolated form, as compose read it: no unescaped $ (plan A3).
    leaked = [p for p, v in _strings(raw) if "$" in v.replace("$$", "")]
    if leaked:
        return None, None, None, None, f"interpolation refused in v1 ({', '.join(sorted(leaked)[:3])})"
    reasons = validate_config(cfg, name, allow, self_name, data_dir)
    if reasons:
        return None, None, None, None, "; ".join(reasons)
    return hashes, plain_hashes, fingerprint(cfg), bind_sources(cfg), None


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


def live_writable(docker=docker_read) -> list[tuple[str, str]]:
    """(container, source) of every read-write mount of EVERY container on the host.

    The nested-write rule needs what can be written NOW, not only what this
    tick's Git context plans: any running container (hand-made ones too) that
    can write a directory could swap a path below it for a symlink.
    """
    try:
        ids = docker("ps", "-a", "-q", "--no-trunc").split()
        if not ids:
            return []
        out = docker("inspect", "--type", "container", "--format", MOUNTS_FORMAT, *ids)
    except (subprocess.SubprocessError, OSError) as e:
        raise d.DockerUnavailable(type(e).__name__) from None
    found = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, mounts = line.split("\t", 1)
        for m in json.loads(mounts) or []:
            src = m.get("Source") if isinstance(m, dict) else None
            if m.get("RW") is True and isinstance(src, str) and src.startswith("/"):
                found.append((json.loads(name).lstrip("/"), posixpath.normpath(src)))
    return sorted(set(found))


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
              self_name: str | None = None, write=docker_write, sleep=time.sleep) -> int:
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
        live_rw = live_writable(docker)
    except d.DockerUnavailable as e:
        return fail(f"docker ps failed: {e}")

    in_git = {e["name"] for e in all_entries} | set(d.EXCLUDED_SCALE_APPS)
    unmanaged = sorted(p for p in projects if p not in in_git) + [f"container:{n}" for n in loose]

    results = []
    unevaluated: list[str] = []
    ctx: dict[str, dict] = {}
    for e in entries:
        if e["status"] == "excluded":
            continue
        try:
            if e["status"] != "managed":
                results.append(evaluate_compose_app(e, None, [], owner=owner))
                continue
            hashes, plain, tree, binds, err = render_config(e, docker, project_dir, owner,
                                                            allow=e["allow_host_paths"], self_name=self_name,
                                                            data_dir=data_dir)
            containers = project_containers(e["name"], docker) if not err else []
            base = (baselines.get(e["name"]) or {}).get("tree")
            r = evaluate_compose_app(e, hashes, containers, owner=owner, plain_hashes=plain, tree=tree,
                                     baseline=base, config_error=err)
            if r["state"] not in STATES:
                raise ValueError("unknown state")
            # Baseline: recorded on first evaluation, moved only when live is
            # deployer-owned and matches Git (synced). Not while adoptable or
            # unowned: that would hide a top-level change behind the move.
            if tree is not None and (base is None or r["state"] == "synced"):
                baselines[e["name"]] = {"sha": sha, "tree": tree}
            ctx[e["name"]] = {"entry": e, "binds": binds, "hashes": hashes, "tree": tree,
                              "container_names": {svc: b.get("container_name") for svc, b in
                                                  (e["compose"].get("services") or {}).items()
                                                  if isinstance(b, dict) and b.get("container_name")}}
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

    # Cross-app: nothing may be bind-mounted below a writable mount. Writable =
    # every LIVE read-write mount on the host (any container) plus what this
    # tick plans; the preflight re-checks it on resolved real paths.
    writable = live_rw + [(n, posixpath.normpath(b[1])) for n, c in ctx.items() for b in (c["binds"] or [])
                          if isinstance(b[1], str) and not b[2]]
    names = {e["name"] for e in all_entries} | {self_name}
    for r in results:
        c = ctx.get(r["app"])
        bad = nested_write_reasons(c["binds"] or [], writable) if c else []
        if c:
            bad += [f"services.{svc}.container_name: {cn} is another app's name"
                    for svc, cn in sorted((c.get("container_names") or {}).items())
                    if cn in names and cn != r["app"]]
        if bad:
            r.update(state="invalid", reasons=bad, drift=[], changes=[])
            ctx.pop(r["app"])

    # x-deploy.create is honoured from cutover (open question 2): a missing app
    # that opts in is planned like a pending one.
    plan_input = [{**r, "state": "pending"} if r["state"] == "missing" and (ctx.get(r["app"]) or {}).get(
        "entry", {}).get("create") else r for r in results]
    failures = state.get("failures") or {}
    held = [n for n, f in failures.items() if f.get("sha") == sha and started - f.get("at", 0) < BACKOFF_SECONDS]
    would, deferred = d.plan_applies([r for r in plan_input if r["app"] not in held])
    by_app = {r["app"]: r for r in results}
    for n in held:
        if n in by_app and by_app[n]["state"] in ("pending", "missing"):
            by_app[n].update(state="failed", reasons=[f"backoff: the apply of {sha[:7]} failed less than "
                                                      f"{BACKOFF_SECONDS // 60} min ago"])
    d.log_tick(dry_run=dry_run, sha=sha, results=results, would=would, deferred=deferred,
               global_paused=global_paused, unmanaged=unmanaged, unevaluated=unevaluated,
               stopped_note="project STOPPED: would update with up --no-start, never start")

    if d.mutations_allowed(dry_run, BACKEND) and not global_paused:
        for n in would:
            c = ctx[n]
            try:
                ok, why = apply_compose(c["entry"], by_app[n], dry_run=dry_run, docker=docker, write=write,
                                        project_dir=project_dir, owner=owner, self_name=self_name,
                                        data_dir=data_dir, binds=c["binds"], hashes=c["hashes"], sleep=sleep,
                                        writable=writable)
            except d.DryRunViolation:
                raise
            except Exception as ex:  # noqa: BLE001 - record it, keep the tick
                ok, why = False, f"apply error: {type(ex).__name__}"
            if ok:
                log(f"  {n}: applied {sha[:7]}")
                applied[n] = {"commit": sha, "at": int(time.time())}
                baselines[n] = {"sha": sha, "tree": c["tree"]}
                failures.pop(n, None)
                by_app[n].update(state="synced", reasons=[], drift=[], changes=[])
            else:
                log(f"  {n}: apply FAILED: {why}")
                failures[n] = {"sha": sha, "at": int(started)}
                by_app[n].update(state="failed", reasons=[why])
    state["failures"] = {k: v for k, v in failures.items() if k in by_app or k in unevaluated}

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
    if cmd == "resolve":
        # Runs only inside the preflight helper container (host / at /host).
        sys.exit(resolve_main(sys.argv[2:]))
    print("usage: compose_backend.py fetch|reconcile|version|resolve", file=sys.stderr)
    sys.exit(2)
