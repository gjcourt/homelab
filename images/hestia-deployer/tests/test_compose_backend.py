"""Unit tests for images/hestia-deployer/compose_backend.py (HOMELAB-118, A-Phase 1 shadow).

Three kinds of test, and what each one proves:

  - Pure tests of the validators, the evaluator and the argv allowlist.
  - Tick tests through a real git cache and a FakeDocker behind the backend's
    own argv allowlist. FakeDocker computes the "desired" hash and the
    container labels with the same function, so its synced/pending results
    are CIRCULAR: they test the bookkeeping (states, baselines, metrics, the
    secrecy of the outputs), not that compose's hash matches up's label.
  - Tests against the REAL compose CLI (`docker compose config`, which needs
    no engine), skipped where no compose CLI is installed. These run the
    reviewer's bypass probes through the real parser. The hash-vs-label
    property was measured end to end with podman (see the PR).
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)

import compose_backend as cb  # noqa: E402
import deployer as d  # noqa: E402

IMG = "ghcr.io/gjcourt/foo:2026-11-09-abcdef0@sha256:" + "a" * 64
NEW = "ghcr.io/gjcourt/foo:2026-11-10-1234567@sha256:" + "c" * 64
OWNER = "alcatraz-deployer"
DATA = "/volume1/docker/alcatraz-deployer"
GIT_SENTINEL = "git-public-sentinel-4242"
HOST_SENTINEL = "host-only-sentinel-9191"


def load(text):
    return yaml.safe_load(text)


@pytest.fixture(autouse=True)
def alcatraz(monkeypatch):
    """Run every test as alcatraz would be configured (host label, prefix, self)."""
    monkeypatch.setattr(d, "HOST", "alcatraz")
    monkeypatch.setattr(d, "HOSTS_PREFIX", "hosts/alcatraz/")
    monkeypatch.setattr(d, "SELF_NAME", OWNER)
    monkeypatch.setattr(d, "JOB", OWNER)
    monkeypatch.setattr(d, "DATA_DIR", DATA)
    monkeypatch.setattr(d, "EXCLUDED_PREFIXES", ("hosts/alcatraz/actions-runner/", "hosts/alcatraz/alcatraz-deployer/"))
    monkeypatch.setattr(d, "EXCLUDED_NAMES", (OWNER,))
    monkeypatch.setattr(d, "EXCLUDED_SCALE_APPS", ("gha-runner", OWNER))


# ------------------------------------------------------------ shadow guards


def test_compose_apply_is_disabled():
    assert d.APPLY_ENABLED["compose"] is False
    assert not d.mutations_allowed(False, cb.BACKEND)


@pytest.mark.parametrize("dry_run", [True, False])
def test_apply_compose_refuses_before_any_call(dry_run):
    calls = []
    with pytest.raises(d.DryRunViolation):
        cb.apply_compose("foo", dry_run=dry_run, docker=lambda *a, **k: calls.append(a))
    assert calls == []


def test_arming_truenas_does_not_arm_compose(monkeypatch):
    monkeypatch.setitem(d.APPLY_ENABLED, "truenas", True)
    with pytest.raises(d.DryRunViolation):
        cb.apply_compose("foo", dry_run=False)


CID = "0123456789ab" * 4 + "0" * 16
ALLOWED = [
    ["ps", "-a", "--no-trunc", "--format", cb.PS_FORMAT],
    ["ps", "-a", "-q", "--no-trunc", "--filter", f"label={cb.PROJECT_LABEL}=foo", "--filter", cb.ONEOFF_FILTER],
    ["inspect", "--type", "container", "--format", cb.INSPECT_FORMAT, CID, CID[:12]],
    ["version", "--format", cb.VERSION_FORMAT],
    ["compose", "version", "--short"],
    cb.compose_args("foo", "/d", "--hash", "*"),
    cb.compose_args("foo", "/d", "--format", "json"),
    cb.compose_args("foo", "/d", "--format", "json", "--no-interpolate"),
]
REFUSED = [
    [],
    ["run", "--rm", "--mount", "type=bind,source=/,target=/p", "img", "true"],
    ["rm", "-f", "x"], ["stop", "x"], ["start", "x"], ["exec", "x", "sh"], ["kill", "x"], ["pull", "x"],
    ["-H", "tcp://x", "ps"],
    ["image", "inspect", "foo"],
    ["ps", "-a"],                                                  # unpinned ps shape
    ["ps", "-a", "--format", "{{json .}}"],
    ["ps", "-a", "-q", "--no-trunc", "--filter", f"label={cb.PROJECT_LABEL}=foo"],  # no oneoff filter
    ["ps", "-a", "-q", "--no-trunc", "--filter", f"label={cb.PROJECT_LABEL}=../x", "--filter", cb.ONEOFF_FILTER],
    ["inspect", CID],                                              # unpinned inspect (would return Env)
    ["inspect", "--type", "container", "--format", "{{json .Config.Env}}", CID],
    ["inspect", "--type", "container", "--format", cb.INSPECT_FORMAT],  # no ids
    ["inspect", "--type", "container", "--format", cb.INSPECT_FORMAT, "--size"],
    ["version"],
    ["compose", "pull"], ["compose", "up", "-d"], ["compose", "ls"], ["compose", "down"],
    ["compose", "-f", "-", "-p", "foo", "--project-directory", "/d", "up", "-d", "--remove-orphans"],
    ["compose", "-f", "-", "-p", "foo", "--project-directory", "/d", "pull"],
    ["compose", "-f", "/etc/x.yml", "-p", "foo", "--project-directory", "/d", "config"],
    ["compose", "-f", "-", "-p", "foo", "config"],                 # no fixed project directory
    ["compose", "-f", "-", "-p", "Foo Bar", "--project-directory", "/d", "config", "--hash", "*"],
    cb.compose_args("foo", "relative", "--hash", "*"),
    cb.compose_args("foo", "/d"),                                  # bare config prints YAML
    cb.compose_args("foo", "/d", "--output", "/tmp/x"),
    cb.compose_args("foo", "/d", "--format", "yaml"),
    cb.compose_args("foo", "/d", "--resolve-image-digests"),
    cb.compose_args("foo", "/d", "--hash", "s"),
]


@pytest.mark.parametrize("argv", ALLOWED)
def test_read_argv_allowed(argv):
    cb.check_read_argv(argv)


@pytest.mark.parametrize("argv", REFUSED)
def test_read_argv_refused_before_any_process(argv, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess.run called")
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(d.DryRunViolation):
        cb.docker_read(*argv)


def test_docker_child_env_is_minimal(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setenv("DEPLOYER_HOST", "alcatraz")
    monkeypatch.setenv("SOME_TOKEN", HOST_SENTINEL)
    monkeypatch.setattr(subprocess, "run", run)
    cb.docker_read("ps", "-a", "--no-trunc", "--format", cb.PS_FORMAT)
    assert HOST_SENTINEL not in json.dumps(seen["env"])
    assert set(seen["env"]) <= set(cb.CHILD_ENV_KEYS)
    assert seen["cwd"] == "/"


# ------------------------------------------------------------ pass 1: validate_git (raw Git)


def svc(**body):
    return {"services": {"s": {"image": IMG, **body}}}


@pytest.mark.parametrize("body,needle", [
    ({"privileged": True}, "privileged"),
    ({"privileged": "true"}, "privileged"),          # a string: compose turns it into true
    ({"devices": ["/dev/sda:/dev/sda"]}, "devices"),
    ({"volumes_from": ["portainer"]}, "volumes_from"),
    ({"security_opt": ["seccomp=unconfined"]}, "security_opt"),
    ({"userns_mode": "host"}, "userns_mode"),
    ({"ipc": "host"}, "ipc"),
    ({"cgroup": "host"}, "cgroup"),
    ({"pid": "host"}, "pid"),
    ({"cap_add": ["NET_ADMIN"]}, "cap_add"),
    ({"sysctls": {"kernel.x": "1"}}, "sysctls"),
    ({"env_file": ".env"}, "env_file"),
    ({"label_file": ["/proc/self/environ"]}, "label_file"),
    ({"build": "."}, "build"),
    ({"extends": {"file": "x.yml", "service": "y"}}, "extends"),
    ({"environment": {"TOKEN": "${TOKEN}"}}, "interpolation"),
    ({"environment": ["TOKEN=$TOKEN"]}, "interpolation"),
    ({"command": "echo ${HOME:-x}"}, "interpolation"),
    ({"environment": ["DOCKER_HOST"]}, "without a value"),  # bare key: compose fills it from its env
    ({"environment": {"HOME": None}}, "without a value"),
    ({"labels": {cb.OWNER_LABEL: "me"}}, "set by the deployer"),
])
def test_git_pass_refuses(body, needle):
    reasons = cb.validate_git(svc(**body), {}, DATA)
    assert reasons and any(needle in r for r in reasons), reasons


@pytest.mark.parametrize("top", ["secrets", "configs", "include", "models", "x"])
def test_git_pass_refuses_top_level_keys(top):
    c = svc()
    c[top] = {"k": {"file": "/volume1/docker/foo/k"}}
    assert any(top in r for r in cb.validate_git(c, {}, DATA))


def test_git_pass_allows_the_plain_cases():
    assert cb.validate_git(svc(command="echo $$HOME", environment=["TZ=UTC"], mem_limit="64m",
                               restart="unless-stopped", volumes=["/volume1/docker/foo:/d"]), {}, DATA) == []


@pytest.mark.parametrize("meta", [
    {"adopt": "yes"},
    {"allow-host-paths": "/volume1/homes"},
    {"allow-host-paths": ["relative"]},
    {"allow-host-paths": ["/volume1/docker"]},             # every app's subtree
    {"allow-host-paths": ["/volume1/docker/other-app"]},    # someone else's subtree
    {"allow-host-paths": [DATA]},                           # the deployer's DATA_DIR
    {"allow-host-paths": [DATA + "/desired.json"]},
    {"allow-host-paths": ["/volume1"]},                      # contains denied roots
    {"allow-host-paths": ["/var"]},                          # contains /var/run, /var/lib
    {"allow-host-paths": ["/var/lib/x"]},
    {"allow-host-paths": ["/root"]},
    {"allow-host-paths": ["/volume1/@docker"]},
    {"allow-host-paths": ["/volume1/@appstore/x"]},
    {"allow-host-paths": ["/volume1/@anything"]},
    {"allow-host-paths": ["/volume2/x"]},
    {"allow-host-paths": ["/etc/../etc"]},
    {"allow-host-paths": ["/usr/syno"]},
    {"allow-host-paths": ["/usr/local/bin"]},
    {"allow-host-paths": ["/var/packages/ContainerManager"]},
    {"allow-host-paths": ["/opt"]},
])
def test_bad_allow_list_entries_are_refused(meta):
    assert cb.validate_git(svc(), meta, DATA)


def test_validation_reasons_never_carry_values():
    reasons = cb.validate_git(svc(environment={"TOKEN": "${" + GIT_SENTINEL + "}"}), {}, DATA)
    assert reasons and GIT_SENTINEL not in " ".join(reasons)


# ------------------------------------------------------------ pass 2: validate_config (parsed shape)


def parsed(service=None, **top):
    """A `docker compose config --format json` shaped document."""
    s = {"image": IMG, "command": None, "entrypoint": None, "networks": {"default": None}}
    s.update(service or {})
    out = {"name": "foo", "networks": {"default": {"name": "foo_default", "ipam": {}}}, "services": {"s": s}}
    out.update(top)
    return out


def bind(src, **kw):
    return {"type": "bind", "source": src, "target": "/t", "bind": {"create_host_path": True}, **kw}


def vc(cfg, name="foo", allow=(), self_name=OWNER):
    return cb.validate_config(cfg, name, list(allow), self_name, DATA)


@pytest.mark.parametrize("src,ok", [
    ("/volume1/docker/foo", True),                    # own subtree
    ("/volume1/docker/foo/data", True),
    ("/volume1/docker", False),                       # every app's subtree (HIGH-2)
    ("/volume1/docker/bar/data", False),              # another app's subtree
    (DATA, False),                                    # the deployer's data dir (HIGH-2)
    (DATA + "/desired.json", False),
    ("/volume1/docker/foo/../alcatraz-deployer", False),
    ("/volume1/misc", False),                          # not allow-listed: refused (allowlist of roots)
    ("/root", False), ("/var/lib/docker", False), ("/volume1/@docker", False), ("/volume1/@appstore", False),
    ("/usr/syno/etc", False), ("/var/packages/x", False), ("/usr", False), ("/opt/x", False),
    ("/volume2/x", False), ("/", False), ("/volume1", False), ("/etc/passwd", False),
    ("/var/run/docker.sock", False), ("/run/x", False), ("/volume1/@iSCSI/lun", False),
    ("/volume1/homes/george", False),
    ("/volume1/docker/foo/../../homes/george", False),
    ("./data", False), ("~/data", False),
])
def test_bind_sources(src, ok):
    assert (vc(parsed({"volumes": [bind(src)]})) == []) is ok


def test_allow_list_opens_exactly_what_it_lists():
    allow = ["/volume1/homes/george/Photos"]
    assert vc(parsed({"volumes": [bind("/volume1/homes/george/Photos/2026")]}), allow=allow) == []
    assert vc(parsed({"volumes": [bind("/volume1/homes/mara/Photos")]}), allow=allow)


def test_self_may_mount_the_socket_and_its_data_dir_and_nothing_else():
    ok = parsed({"volumes": [bind(cb.SELF_SOCKET), bind(DATA), bind(DATA + "/hc-url", read_only=True)]},
                networks={"default": {"name": f"{OWNER}_default", "ipam": {}}})
    assert vc(ok, name=OWNER) == []
    assert vc(parsed({"volumes": [bind(cb.SELF_SOCKET)]}), name="foo")
    assert vc(parsed({"volumes": [bind("/var/run/other.sock")]}), name=OWNER)
    assert vc(parsed({"volumes": [bind("/etc")]}), name=OWNER)
    assert vc(parsed({"privileged": True}), name=OWNER)


@pytest.mark.parametrize("service", [
    {"privileged": True},
    {"devices": [{"source": "/dev/sda", "target": "/dev/sda", "permissions": "rwm"}]},
    {"volumes_from": ["container:portainer"]},
    {"security_opt": ["apparmor=unconfined"]},
    {"userns_mode": "host"}, {"ipc": "host"}, {"cgroup": "host"}, {"pid": "host"},
    {"cap_add": ["SYS_ADMIN"]}, {"sysctls": {"kernel.x": "1"}},
    {"network_mode": "host"}, {"network_mode": "HOST"}, {"network_mode": "container:other"},
    {"label_file": ["/proc/self/environ"]}, {"env_file": [{"path": ".env"}]},
    {"deploy": {"resources": {"reservations": {"devices": [{"driver": "nvidia"}]}}}},
    {"deploy": {"placement": {}}},
    {"gpus": "all"},
    {"volumes": [{"type": "npipe", "source": "x", "target": "/x"}]},
    {"volumes": [{"type": "image", "source": "x", "target": "/x"}]},
    {"volumes": [{"type": "cluster", "source": "x", "target": "/x"}]},
    {"volumes": [bind("/volume1/docker/foo", bind={"propagation": "rshared"})]},
    {"volumes": [bind("/volume1/docker/foo", bind={"selinux": "z"})]},
    {"volumes": [{"type": "volume", "source": "undeclared", "target": "/x", "volume": {}}]},
    {"logging": {"driver": "syslog", "options": {"syslog-address": "tcp://203.0.113.1:514"}}},
    {"logging": {"driver": "some/plugin"}},
    {"logging": {"driver": "gelf"}},
    {"logging": "json-file"},
])
def test_config_pass_refuses_services(service):
    assert vc(parsed(service))


@pytest.mark.parametrize("top", [
    {"volumes": {"x": {"name": "foo_x", "driver_opts": {"type": "none", "o": "rbind", "device": "/"}}}},
    {"volumes": {"x": {"name": "foo_x", "driver_opts": {"type": "ext4", "device": "/dev/md2"}}}},
    {"volumes": {"x": {"name": "foo_x", "driver_opts": {"type": "none", "o": "ro, bind", "device": "/etc"}}}},
    {"volumes": {"x": {"name": "some-root-vol", "external": True}}},
    {"volumes": {"x": {"name": "foo_x", "driver": "some/plugin"}}},
    {"networks": {"n": {"name": "host", "ipam": {}, "external": True}}},
    {"networks": {"n": {"name": "foo_n", "driver": "macvlan"}}},
    {"networks": {"n": {"name": "foo_n", "driver_opts": {"parent": "eth0"}}}},
    {"secrets": {"k": {"file": "/etc/shadow"}}},
    {"configs": {"k": {"file": "/etc/shadow"}}},
    # Round 2 HIGH: `name` borrows an existing volume / network.
    {"volumes": {"x": {"name": "other-project_data"}}},
    {"volumes": {"x": {"name": "hand-made-bind-volume"}}},
    {"volumes": {"x": {"name": "foo_other"}}},
    {"networks": {"default": {"name": "other_default", "ipam": {}}}},
    {"networks": {"default": {"name": "host", "ipam": {}}}},
    {"networks": {"default": {"name": "bridge", "ipam": {}}}},
])
def test_config_pass_refuses_top_level(top):
    cfg = parsed({"volumes": [{"type": "volume", "source": "x", "target": "/x", "volume": {}}]}, **top)
    assert vc(cfg)


def test_config_pass_allows_the_projects_own_names():
    cfg = parsed({"volumes": [{"type": "volume", "source": "data", "target": "/d", "volume": {}}],
                  "logging": {"driver": "local", "options": {"max-size": "1m"}}},
                 volumes={"data": {"name": "foo_data"}}, networks={"default": {"name": "foo_default", "ipam": {}}})
    assert vc(cfg) == []


def test_config_pass_allows_a_realistic_service():
    cfg = parsed({"restart": "unless-stopped", "container_name": "cc", "cpus": 2, "mem_limit": "268435456",
                  "environment": {"TZ": "UTC"}, "user": "0:0", "init": True, "read_only": True,
                  "tmpfs": ["/t"], "healthcheck": {"test": ["CMD", "true"]}, "ports": [{"target": 80}],
                  "logging": {"driver": "json-file"}, "network_mode": "none",
                  "deploy": {"resources": {"limits": {"memory": "64m"}}},
                  "volumes": [bind("/volume1/docker/foo"), {"type": "volume", "source": "data", "target": "/data",
                                                             "volume": {}}, {"type": "tmpfs", "target": "/x"}]},
                 volumes={"data": {"name": "foo_data"}})
    assert vc(cfg) == []


# ------------------------------------------------------------ discovery against the real tree


def alcatraz_files():
    files = {}
    for dirpath, _, names in os.walk(os.path.join(ROOT, "hosts/alcatraz")):
        for n in names:
            p = os.path.relpath(os.path.join(dirpath, n), ROOT)
            if d.is_compose_path(p):
                files[p] = open(os.path.join(ROOT, p)).read()
    return files


def test_real_alcatraz_composes():
    files = alcatraz_files()
    by = {e["file"]: e for e in cb.discover(files, load, DATA)}
    assert by["hosts/alcatraz/immich-photos-pull/docker-compose.yml"]["status"] == "archived"
    assert by["hosts/alcatraz/actions-runner/docker-compose.yml"]["status"] == "excluded"
    assert not [e for e in by.values() if e["status"] == "invalid"], by
    canary = by["hosts/alcatraz/deploy-canary/docker-compose.yml"]
    assert canary["status"] == "managed" and canary["create"] is True and canary["name"] == "deploy-canary"
    svc_ = canary["compose"]["services"]["deploy-canary"]
    assert "@sha256:" in svc_["image"] and "volumes" not in svc_ and svc_["mem_limit"] == "16m"
    doc = load(files["hosts/alcatraz/immich-photos-pull/docker-compose.yml"])
    meta = doc.pop("x-deploy")
    compose = {k: v for k, v in doc.items() if not k.startswith("x-")}
    assert cb.validate_git(compose, meta, DATA) == []


def test_discover_makes_one_app_invalid_not_the_tick():
    good = f"services:\n  s:\n    image: {IMG}\n"
    bad = f"services:\n  s:\n    image: {IMG}\n    privileged: 'true'\n"
    adopt = f"x-deploy:\n  adopt: true\n  allow-host-paths: [/volume1/homes/x]\nservices:\n  s:\n    image: {IMG}\n"
    es = {e["name"]: e for e in cb.discover({"hosts/alcatraz/good/docker-compose.yml": good,
                                             "hosts/alcatraz/bad/docker-compose.yml": bad,
                                             "hosts/alcatraz/adopted/docker-compose.yml": adopt}, load, DATA)}
    assert es["good"]["status"] == "managed" and es["good"]["adopt"] is False
    assert es["bad"]["status"] == "invalid" and "privileged" in es["bad"]["reason"] and es["bad"]["compose"] is None
    assert es["adopted"]["adopt"] is True and es["adopted"]["allow_host_paths"] == ["/volume1/homes/x"]


# ------------------------------------------------------------ fingerprint / owner label


def test_owner_label_added_to_every_service_without_mutating_input():
    c = {"services": {"a": {"image": IMG, "labels": ["x=1"]}, "b": {"image": IMG}}}
    before = copy.deepcopy(c)
    out = cb.with_owner_label(c, OWNER)
    assert c == before
    assert out["services"]["a"]["labels"] == {"x": "1", cb.OWNER_LABEL: OWNER}
    assert out["services"]["b"]["labels"] == {cb.OWNER_LABEL: OWNER}


def test_fingerprint_hides_values_keeps_images_and_names_paths():
    a = {"services": {"s": {"image": IMG, "environment": {"TOKEN": GIT_SENTINEL, "X": "1"}}}}
    b = copy.deepcopy(a)
    b["services"]["s"]["image"] = NEW
    b["services"]["s"]["environment"]["TOKEN"] = "other"
    fa, fb = cb.fingerprint(a), cb.fingerprint(b)
    assert GIT_SENTINEL not in json.dumps(fa)
    assert fa["services"]["s"]["image"] == IMG
    paths = d.diff_paths(fb, fa)
    assert paths == ["services.s.environment.TOKEN", "services.s.image"]
    assert d.describe_paths(paths, fb, fa) == ["services.s.environment.TOKEN", f"services.s.image: {IMG} -> {NEW}"]


def test_parse_hashes():
    assert cb.parse_hashes("a 123\nb 456\n") == {"a": "123", "b": "456"}
    with pytest.raises(ValueError):
        cb.parse_hashes("garbage line here\n")


# ------------------------------------------------------------ evaluate (pure)


def managed(compose=None, name="foo", **kw):
    e = {"name": name, "file": f"hosts/alcatraz/{name}/docker-compose.yml", "status": "managed", "reason": "",
         "paused": False, "preserve_env": [], "create": False, "adopt": False, "allow_host_paths": [],
         "compose": compose or {"services": {"s": {"image": IMG}}}}
    e.update(kw)
    return e


def ctr(service="s", h="H1", owner=OWNER, running=True, restarting=False, name=None):
    return {"name": name or f"foo-{service}-1", "service": service, "hash": h, "owner": owner, "image": IMG,
            "running": running, "restarting": restarting, "restart_count": 0}


def ev(entry, containers, hashes=None, plain=None, **kw):
    return cb.evaluate_compose_app(entry, {"s": "H1"} if hashes is None else hashes, containers, owner=OWNER,
                                   plain_hashes={"s": "P1"} if plain is None else plain, **kw)


def test_evaluate_synced():
    r = ev(managed(), [ctr()])
    assert r["state"] == "synced" and r["drift"] == [] and r["changes"] == []


def test_evaluate_hash_mismatch_is_pending_with_paths_from_baseline():
    base = cb.fingerprint({"services": {"s": {"image": IMG}}})
    tree = cb.fingerprint({"services": {"s": {"image": NEW}}})
    r = ev(managed(), [ctr(h="OLD")], tree=tree, baseline=base)
    assert r["state"] == "pending"
    assert r["drift"] == ["s: config hash differs (foo-s-1)"]
    assert r["changes"] == [f"services.s.image: {IMG} -> {NEW}"]


def test_evaluate_without_baseline_still_pending_but_names_no_paths():
    r = ev(managed(), [ctr(h="OLD")])
    assert r["state"] == "pending" and r["changes"] == []


def test_evaluate_top_level_change_is_drift():
    base = cb.fingerprint({"services": {"s": {"image": IMG}}, "networks": {"n": {"internal": False}}})
    tree = cb.fingerprint({"services": {"s": {"image": IMG}}, "networks": {"n": {"internal": True}}})
    r = ev(managed(), [ctr()], tree=tree, baseline=base)
    assert r["state"] == "pending" and "top-level config changed" in r["drift"][0]
    assert r["changes"] == ["networks.n.internal"]


def test_evaluate_missing_and_create_note():
    r = ev(managed(create=True), [])
    assert r["state"] == "missing" and "not acted on until cutover" in r["reasons"][0]


def test_evaluate_ownership():
    # Made by hand from the same file: adoptable (MEDIUM-3), not just blocked.
    r = ev(managed(), [ctr(owner=None, h="P1")])
    assert r["state"] == "adoptable" and "apart from the ownership label" in r["drift"][0]
    # Made by hand and different: blocked.
    r = ev(managed(), [ctr(owner=None, h="OTHER")])
    assert r["state"] == "blocked" and "not deployer-owned" in r["reasons"][0]
    # adopt: pending, either way.
    assert ev(managed(adopt=True), [ctr(owner=None, h="OTHER")])["state"] == "pending"
    assert ev(managed(adopt=True), [ctr(owner=None, h="P1")])["state"] == "pending"
    # one stray hand-made container in an owned project also blocks
    assert ev(managed(), [ctr(), ctr(service="x", owner=None, name="foo-x-1")])["state"] == "blocked"
    # an unowned project missing a service is not adoptable
    r = ev(managed(), [ctr(owner=None, h="P1")], hashes={"s": "H1", "t": "H2"}, plain={"s": "P1", "t": "P2"})
    assert r["state"] == "blocked"
    assert "adoptable" in cb.STATES and set(d.STATES) < set(cb.STATES)


def test_evaluate_orphan_and_missing_service_and_not_running():
    r = ev(managed(), [ctr(), ctr(service="gone", name="foo-gone-1")])
    assert r["state"] == "pending" and "orphan container foo-gone-1" in r["drift"][0]
    r = ev(managed(), [ctr()], hashes={"s": "H1", "t": "H2"})
    assert r["drift"] == ["t: no container"]
    r = ev(managed(), [ctr(restarting=True)])
    assert r["state"] == "pending" and "not running" in r["drift"][0]
    r = ev(managed(), [ctr(), ctr(service="t", h="H2", running=False, name="foo-t-1")], hashes={"s": "H1", "t": "H2"})
    assert r["state"] == "pending" and r["drift"] == ["t: container not running (foo-t-1)"]


def test_evaluate_stopped_project_is_never_started():
    r = ev(managed(), [ctr(running=False)])
    assert r["state"] == "synced" and r["stopped"] is True
    r = ev(managed(), [ctr(running=False, h="OLD")])
    assert r["state"] == "pending" and r["stopped"] is True


def test_evaluate_paused_archived_invalid_and_config_error():
    assert ev(managed(paused=True), [ctr(h="OLD")])["state"] == "paused"
    assert cb.evaluate_compose_app({**managed(), "status": "archived"}, None, [], owner=OWNER)["state"] == "archived"
    r = cb.evaluate_compose_app({**managed(), "status": "invalid", "reason": "bad"}, None, [], owner=OWNER)
    assert r["state"] == "invalid" and r["reasons"] == ["bad"]
    assert ev(managed(), [ctr()], config_error="docker compose config failed: X")["state"] == "invalid"


# ------------------------------------------------------------ tick tests (git cache + FakeDocker)


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def fake_parse(compose, project):
    """A rough `docker compose config --format json`: long-syntax volumes,
    env/labels as maps, string booleans converted. Enough for the tick tests;
    the real parser is exercised in the real-compose tests below."""
    out = json.loads(json.dumps(compose).replace("$$", "$"))
    out["name"] = project
    for body in out["services"].values():
        for key in ("environment", "labels"):
            if isinstance(body.get(key), list):
                body[key] = d._kv_list_to_map(body[key])
        vols = []
        for v in body.get("volumes") or []:
            if isinstance(v, str):
                parts = v.split(":")
                long = {"type": "bind" if parts[0].startswith("/") else "volume", "source": parts[0],
                        "target": parts[1]}
                if len(parts) > 2 and parts[2] == "ro":
                    long["read_only"] = True
                long["bind" if long["type"] == "bind" else "volume"] = {}
                vols.append(long)
            else:
                vols.append(v)
        if vols:
            body["volumes"] = vols
        if body.get("privileged") == "true":
            body["privileged"] = True
        body.setdefault("command", None)
    return out


def config_hash(service_body):
    return hashlib.sha256(json.dumps(service_body, sort_keys=True).encode()).hexdigest()[:16]


def cid(project, i):
    return hashlib.sha256(f"{project}/{i}".encode()).hexdigest()


class FakeDocker:
    """Fake docker behind the backend's real argv allowlist.

    projects: project -> containers. hash "auto" = matches the owner-labelled
    Git compose; "plain" = matches it without the label (made by hand from
    the same file). CIRCULAR by construction: see the module docstring.
    """

    def __init__(self, projects, loose=(), desired=None, fail=()):
        self.projects = projects
        self.loose = list(loose)
        self.desired = desired or {}
        self.fail = set(fail)
        self.calls = []
        self.stdins = []

    def _hash(self, project, service, owned):
        c = self.desired[project]
        c = cb.with_owner_label(c, OWNER) if owned else c
        return config_hash(fake_parse(c, project)["services"][service])

    def __call__(self, *args, stdin=None):
        cb.check_read_argv(args)
        self.calls.append(args)
        if stdin is not None:
            self.stdins.append(stdin)
        if args[0] in self.fail:
            raise subprocess.CalledProcessError(1, ["docker", args[0]])
        if args[0] == "ps" and "--format" in args:
            lines = [f"{c['name']}\t{p}" for p, cs in self.projects.items() for c in cs]
            lines += [f"{n}\t" for n in self.loose]
            return "\n".join(lines) + "\n"
        if args[0] == "ps":
            project = args[5].split("=", 2)[2]
            return "\n".join(cid(project, i) for i, _ in enumerate(self.projects.get(project, [])))
        if args[0] == "inspect":
            ids = {cid(p, i): (p, c) for p, cs in self.projects.items() for i, c in enumerate(cs)}
            out = []
            for x in args[5:]:
                project, c = ids[x]
                h = c.get("hash")
                if h in ("auto", "plain"):
                    h = self._hash(project, c["service"], h == "auto")
                labels = {cb.PROJECT_LABEL: project, cb.SERVICE_LABEL: c["service"], cb.HASH_LABEL: h,
                          "org.example.host-only": HOST_SENTINEL}
                if c.get("owner"):
                    labels[cb.OWNER_LABEL] = c["owner"]
                out.append("\t".join([json.dumps("/" + c["name"]), json.dumps(labels), json.dumps(IMG),
                                      json.dumps(c.get("running", True)), json.dumps(c.get("restarting", False)),
                                      "0"]))
            return "\n".join(out) + "\n"
        if args[0] == "compose":
            if "compose" in self.fail:
                raise subprocess.CalledProcessError(1, ["docker", "compose"])
            compose = json.loads(stdin)
            project = args[4]
            flags = args[8:]
            if flags[0] == "--hash":
                p = fake_parse(compose, project)
                return "".join(f"{s} {config_hash(b)}\n" for s, b in sorted(p["services"].items()))
            if "--no-interpolate" in flags:
                return json.dumps(compose)
            return json.dumps(fake_parse(compose, project))
        raise AssertionError(f"unexpected docker {args}")


def git_env():
    return {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t"}


def commit(remote, files):
    if not (remote / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "master", str(remote)], check=True)
    for rel, text in files.items():
        (remote / rel).parent.mkdir(parents=True, exist_ok=True)
        (remote / rel).write_text(text)
    subprocess.run(["git", "-C", str(remote), "add", "-A"], check=True, env=git_env())
    subprocess.run(["git", "-C", str(remote), "commit", "-q", "-m", "c"], check=True, env=git_env())


def fetch(tmp_path, data):
    url = f"file://{tmp_path / 'remote'}"
    assert d.fetch_main(str(data), url, discover_fn=lambda f, y: cb.discover(f, y, str(data))) == 0
    return json.loads((data / "desired.json").read_text())


def dump(compose, meta=None):
    doc = dict(compose)
    if meta:
        doc = {"x-deploy": meta, **doc}
    return yaml.safe_dump(doc, sort_keys=False)


APPS = {
    "synced-app": ({"services": {"s": {"image": IMG, "environment": {"TOKEN": GIT_SENTINEL}}}}, None),
    "bumped": ({"services": {"s": {"image": IMG}}}, None),
    "handmade": ({"services": {"s": {"image": IMG}}}, None),
    "adoptable-app": ({"services": {"s": {"image": IMG}}}, None),
    "absent": ({"services": {"s": {"image": IMG}}}, {"create": True}),
    "stopped-app": ({"services": {"s": {"image": IMG}}}, None),
    "old": ({"services": {"s": {"image": IMG}}}, {"archived": True}),
    "evil-priv": ({"services": {"s": {"image": IMG, "privileged": "true"}}}, None),
    "evil-mount": ({"services": {"s": {"image": IMG, "volumes": [f"{DATA}:/d"]}}}, None),
    "photos": ({"services": {"s": {"image": IMG, "volumes": ["/volume1/homes/george/Photos:/p"]}}},
               {"allow-host-paths": ["/volume1/homes/george/Photos"]}),
    "widened": ({"services": {"s": {"image": IMG, "volumes": ["/volume1/homes/mara:/m"]}}}, None),
}


def scenario(tmp_path, apps=None):
    apps = APPS if apps is None else apps
    files = {f"hosts/alcatraz/{n}/docker-compose.yml": dump(c, m) for n, (c, m) in apps.items()}
    files["hosts/alcatraz/actions-runner/docker-compose.yml"] = dump({"services": {"r": {"image": IMG}}})
    commit(tmp_path / "remote", files)
    data = tmp_path / "data"
    desired = fetch(tmp_path, data)
    compose = {e["name"]: e["compose"] for e in desired["apps"] if e.get("compose")}
    projects = {
        "synced-app": [{"name": "synced-app-s-1", "service": "s", "hash": "auto", "owner": OWNER}],
        "bumped": [{"name": "bumped-s-1", "service": "s", "hash": "auto", "owner": OWNER}],
        "handmade": [{"name": "handmade-s-1", "service": "s", "hash": "dsm-made"}],
        "adoptable-app": [{"name": "adoptable-app-s-1", "service": "s", "hash": "plain"}],
        "stopped-app": [{"name": "stopped-app-s-1", "service": "s", "hash": "auto", "owner": OWNER,
                         "running": False}],
        "photos": [{"name": "photos-s-1", "service": "s", "hash": "auto", "owner": OWNER}],
        "gha-runner": [{"name": "gha-runner", "service": "runner", "hash": "x"}],
        "dsm-thing": [{"name": "dsm-thing-1", "service": "x", "hash": "x"}],
    }
    return data, FakeDocker(projects, loose=["portainer"], desired=compose)


def tick(tmp_path, docker, data, **kw):
    tf = tmp_path / "tf"
    rc = cb.reconcile(docker=docker, data_dir=str(data), textfile_dir=str(tf), now=1000, **kw)
    return rc, (tf / d.DEPLOY_TEXTFILE).read_text(), (data / "state.json").read_text()


def parse_prom(body):
    samples = {}
    for line in body.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        key, value = line.rsplit(" ", 1)
        samples[key] = float(value)
    return samples


def state_of(s, app, st):
    return s.get(f'homelabscope_deploy_app_state{{host="alcatraz",app="{app}",state="{st}"}}')


@needs_git
@pytest.mark.parametrize("dry_run", [True, False])
def test_reconcile_only_reads_and_reports(tmp_path, capsys, dry_run):
    data, docker = scenario(tmp_path)
    rc, body, state_text = tick(tmp_path, docker, data, dry_run=dry_run)
    assert rc == 0
    for call in docker.calls:  # every call passed the allowlist; none mutates
        assert call[0] in ("ps", "inspect", "compose") and (call[0] != "compose" or "config" in call)
    s = parse_prom(body)
    expected = {"synced-app": "synced", "bumped": "synced", "handmade": "blocked", "adoptable-app": "adoptable",
                "absent": "missing", "stopped-app": "synced", "old": "archived", "evil-priv": "invalid",
                "evil-mount": "invalid", "photos": "synced", "widened": "invalid"}
    for app, st in expected.items():
        assert state_of(s, app, st) == 1, (app, st)
    assert not any('app="actions-runner"' in k or 'app="gha-runner"' in k for k in s)
    assert s['homelabscope_deploy_unmanaged_apps{host="alcatraz"}'] == 2  # dsm-thing + portainer
    assert s['homelabscope_deploy_dry_run{host="alcatraz"}'] == 1  # compose switch off: always dry-run
    assert s['homelabscope_job_last_status{job="alcatraz-deployer"}'] == 0
    out = capsys.readouterr().out
    assert "tick DRY-RUN" in out and "unmanaged=['dsm-thing', 'container:portainer']" in out
    assert "handmade [blocked]" in out and "adoptable-app [adoptable]" in out
    assert "evil-priv [invalid]" in out and "evil-mount [invalid]" in out and "is not mountable" in out
    assert oct(os.stat(data / "state.json").st_mode & 0o777) == "0o600"
    st = json.loads(state_text)
    assert st["last_tick"]["backend"] == "compose" and st["last_tick"]["would_apply"] == []
    # Baselines from the first evaluation (MEDIUM-3), invalid apps have none.
    assert set(st["baseline"]) == {"synced-app", "bumped", "handmade", "adoptable-app", "absent", "stopped-app",
                                   "photos"}


@needs_git
def test_evil_compose_never_reaches_the_compose_cli(tmp_path):
    # Pass 1 refuses before render: compose never reads a label_file / env_file.
    apps = {"evil": ({"services": {"s": {"image": IMG, "label_file": ["/proc/self/environ"]}}}, None),
            "bare": ({"services": {"s": {"image": IMG, "environment": ["DOCKER_HOST"]}}}, None)}
    data, docker = scenario(tmp_path, apps)
    _, body, state = tick(tmp_path, docker, data)
    s = parse_prom(body)
    assert state_of(s, "evil", "invalid") == 1 and state_of(s, "bare", "invalid") == 1
    assert not any(c[0] == "compose" for c in docker.calls)


@needs_git
def test_tampered_desired_json_cannot_widen_the_allow_list(tmp_path, capsys):
    # HIGH-2: the reconcile re-check reads Git (the cache), not desired.json.
    data, docker = scenario(tmp_path)
    desired = json.loads((data / "desired.json").read_text())
    for e in desired["apps"]:
        if e["name"] == "widened":
            e.update(status="managed", reason="", allow_host_paths=["/volume1/homes"],
                     compose=APPS["widened"][0])
    (data / "desired.json").write_text(json.dumps(desired))
    _, body, _ = tick(tmp_path, docker, data)
    assert state_of(parse_prom(body), "widened", "invalid") == 1


@needs_git
def test_desired_sha_must_be_the_cache_head(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    desired = json.loads((data / "desired.json").read_text())
    desired["sha"] = "f" * 40
    (data / "desired.json").write_text(json.dumps(desired))
    tf = tmp_path / "tf"
    assert cb.reconcile(docker=docker, data_dir=str(data), textfile_dir=str(tf), now=1) == 1
    assert "git cache unreadable: ValueError" in capsys.readouterr().out


@needs_git
def test_no_value_reaches_log_state_or_metrics(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    _, body, state_text = tick(tmp_path, docker, data)
    out = capsys.readouterr().out
    for text in (out, state_text, body):
        assert GIT_SENTINEL not in text and HOST_SENTINEL not in text
    # Git content is public: the sentinel is in desired.json only because Git
    # has it. Nothing from the host is.
    desired_text = (data / "desired.json").read_text()
    assert GIT_SENTINEL in desired_text and HOST_SENTINEL not in desired_text


@needs_git
def test_a_merge_is_predicted_with_its_paths(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    tick(tmp_path, docker, data)
    capsys.readouterr()
    bumped = {"services": {"s": {"image": NEW}}}
    commit(tmp_path / "remote", {"hosts/alcatraz/bumped/docker-compose.yml": dump(bumped)})
    fetch(tmp_path, data)
    _, body, _ = tick(tmp_path, docker, data)
    s = parse_prom(body)
    assert state_of(s, "bumped", "pending") == 1 and state_of(s, "synced-app", "synced") == 1
    out = capsys.readouterr().out
    assert "would_apply=['bumped']" in out
    assert f"bumped [pending] (hosts/alcatraz/bumped/docker-compose.yml) changes: services.s.image: {IMG} -> {NEW}" in out


@needs_git
def test_baseline_does_not_move_while_adoptable(tmp_path, capsys):
    # A top-level change behind an unowned project must stay visible.
    apps = {"hand": ({"services": {"s": {"image": IMG}}}, None)}
    data, docker = scenario(tmp_path, apps)
    docker.projects["hand"] = [{"name": "hand-s-1", "service": "s", "hash": "plain"}]
    tick(tmp_path, docker, data)
    first = json.loads((data / "state.json").read_text())["baseline"]["hand"]
    commit(tmp_path / "remote", {"hosts/alcatraz/hand/docker-compose.yml": dump(
        {"services": {"s": {"image": IMG}}, "networks": {"default": {"internal": True}}})})
    fetch(tmp_path, data)
    docker.desired = {e["name"]: e["compose"] for e in json.loads((data / "desired.json").read_text())["apps"]
                      if e["name"] == "hand"}
    capsys.readouterr()
    _, body, state = tick(tmp_path, docker, data)
    assert json.loads(state)["baseline"]["hand"] == first
    assert "top-level config changed (no service hash covers it): networks" in capsys.readouterr().out
    tick(tmp_path, docker, data)
    assert json.loads((data / "state.json").read_text())["baseline"]["hand"] == first


@needs_git
def test_unchanged_sha_twice_reports_no_pending(tmp_path):
    data, docker = scenario(tmp_path)
    tick(tmp_path, docker, data)
    rc, body, _ = tick(tmp_path, docker, data)
    assert rc == 0 and 'state="pending"' not in body


@needs_git
def test_escaped_dollar_is_valid_end_to_end(tmp_path):
    apps = {"escaped": ({"services": {"s": {"image": IMG, "command": "echo $$HOME"}}}, None)}
    data, docker = scenario(tmp_path, apps)
    docker.projects["escaped"] = [{"name": "e", "service": "s", "hash": "auto", "owner": OWNER}]
    _, body, _ = tick(tmp_path, docker, data)
    assert state_of(parse_prom(body), "escaped", "synced") == 1


def test_interpolation_compose_still_sees_is_refused(tmp_path):
    e = managed({"services": {"s": {"image": IMG, "environment": {"T": "${TOKEN}"}}}})
    *_, err = cb.render_config(e, FakeDocker({}, desired={}), str(tmp_path), OWNER, allow=[], data_dir=DATA)
    assert err.startswith("interpolation refused in v1") and "services.s.environment.T" in err


@needs_git
def test_compose_config_failure_is_invalid_by_type_only(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    docker.fail = {"compose"}
    _, body, _ = tick(tmp_path, docker, data)
    assert state_of(parse_prom(body), "synced-app", "invalid") == 1
    assert "docker compose config failed: CalledProcessError" in capsys.readouterr().out


@needs_git
def test_inspect_failure_is_unevaluated_and_keeps_baselines(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    tick(tmp_path, docker, data)
    docker.fail = {"inspect"}
    rc, body, state = tick(tmp_path, docker, data)
    assert rc == 1
    s = parse_prom(body)
    assert not any('state="pending"' in k for k in s) and not any('app="synced-app"' in k for k in s)
    assert s['homelabscope_job_last_status{job="alcatraz-deployer"}'] == 1
    assert "unevaluated this tick: docker CalledProcessError" in capsys.readouterr().out
    assert "synced-app" in json.loads(state)["baseline"]  # LOW-6


@needs_git
def test_docker_down_fails_the_tick(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    docker.fail = {"ps"}
    tf = tmp_path / "tf"
    assert cb.reconcile(docker=docker, data_dir=str(data), textfile_dir=str(tf), now=1) == 1
    assert 'homelabscope_job_last_status{job="alcatraz-deployer"} 1' in (tf / d.DEPLOY_TEXTFILE).read_text()
    assert "docker ps failed" in capsys.readouterr().out


@needs_git
def test_non_empty_project_directory_fails_closed(tmp_path):
    data, docker = scenario(tmp_path)
    (data / "compose-project").mkdir()
    (data / "compose-project" / ".env").write_text("TOKEN=x\n")
    assert cb.reconcile(docker=docker, data_dir=str(data), textfile_dir=str(tmp_path / "tf"), now=1) == 1
    assert not any(c[0] == "compose" for c in docker.calls)


@needs_git
def test_global_pause_is_reported(tmp_path):
    data, docker = scenario(tmp_path)
    (data / "PAUSE").write_text("")
    _, body, _ = tick(tmp_path, docker, data)
    assert 'homelabscope_deploy_paused{host="alcatraz",scope="global"} 1' in body


def test_no_desired_json_yet(tmp_path):
    (tmp_path / "data").mkdir()
    assert cb.reconcile(docker=FakeDocker({}), data_dir=str(tmp_path / "data"),
                        textfile_dir=str(tmp_path / "tf"), now=1) == 1


def test_one_off_containers_are_filtered():
    calls = []

    def docker(*args, stdin=None):
        cb.check_read_argv(args)
        calls.append(args)
        return ""
    assert cb.project_containers("foo", docker) == []
    assert cb.ONEOFF_FILTER in calls[0]


# ------------------------------------------------------------ the REAL compose parser


def _compose_cli():
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "compose", "version", "--short"], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs_compose = pytest.mark.skipif(not _compose_cli(), reason="docker compose CLI not installed")

# The reviewer's probe cases (scratchpad probe.py / c*.sh), as Git composes.
PROBES = {
    "rbind driver_opts /": {**svc(volumes=["data:/x"]),
                            "volumes": {"data": {"driver_opts": {"type": "none", "o": "rbind", "device": "/"}}}},
    "ext4 block device": {**svc(volumes=["data:/x"]),
                          "volumes": {"data": {"driver_opts": {"type": "ext4", "device": "/dev/md2"}}}},
    "o='ro, bind'": {**svc(volumes=["data:/x"]),
                     "volumes": {"data": {"driver_opts": {"type": "none", "o": "ro, bind", "device": "/etc"}}}},
    "external volume": {**svc(volumes=["data:/x"]), "volumes": {"data": {"external": True, "name": "root-vol"}}},
    "plugin driver": {**svc(volumes=["data:/x"]), "volumes": {"data": {"driver": "some/plugin"}}},
    "external host network": {**svc(networks=["n"]), "networks": {"n": {"external": True, "name": "host"}}},
    "volumes_from": svc(volumes_from=["container:portainer"]),
    "devices": svc(devices=["/dev/sda:/dev/sda"]),
    "privileged string": svc(privileged="true"),
    "security_opt": svc(security_opt=["apparmor=unconfined", "seccomp=unconfined"]),
    "userns host": svc(userns_mode="host"),
    "ipc host": svc(ipc="host"),
    "cgroup host": svc(cgroup="host"),
    "network container": svc(network_mode="container:other"),
    "network HOST": svc(network_mode="HOST"),
    "bind type BIND /etc": svc(volumes=[{"type": "bind", "source": "/etc", "target": "/e"}]),
    "bind /etc/../etc": svc(volumes=[{"type": "bind", "source": "/etc/../etc", "target": "/e"}]),
    "/volume1/docker": svc(volumes=["/volume1/docker:/d"]),
    "deployer DATA_DIR": svc(volumes=[f"{DATA}:/dd"]),
    "traversal to homes": svc(volumes=["/volume1/docker/foo/../../homes:/h"]),
    "/root": svc(volumes=["/root:/r"]),
    "/volume1/@docker": svc(volumes=["/volume1/@docker:/d"]),
    "/var/lib": svc(volumes=["/var/lib:/d"]),
    "/volume2/x": svc(volumes=["/volume2/x:/d"]),
    "label_file": svc(label_file=["/proc/self/environ"]),
    "bare env key": svc(environment=["DOCKER_HOST"]),
    "gpu reservation": svc(deploy={"resources": {"reservations": {"devices": [{"driver": "nvidia",
                                                                                 "capabilities": ["gpu"]}]}}}),
    "sysctls": svc(sysctls={"kernel.x": "1"}),
    "selinux relabel": svc(volumes=["/volume1/docker/foo:/d:z"]),
    "rshared propagation": svc(volumes=[{"type": "bind", "source": "/volume1/docker/foo", "target": "/d",
                                         "bind": {"propagation": "rshared"}}]),
    # Round 2 HIGH: borrowing an existing volume or network by name.
    "foreign volume": {**svc(volumes=["data:/x"]), "volumes": {"data": {"name": "other-project_data"}}},
    "hand-made bind volume": {**svc(volumes=["data:/x"]), "volumes": {"data": {"name": "rootfs"}}},
    "foreign network": {**svc(networks=["n"]), "networks": {"n": {"name": "other_default"}}},
    "network named host": {**svc(networks=["n"]), "networks": {"n": {"name": "host"}}},
    "default network renamed": {**svc(), "networks": {"default": {"name": "host"}}},
    "syslog remote": svc(logging={"driver": "syslog", "options": {"syslog-address": "tcp://203.0.113.1:514"}}),
    "logging plugin": svc(logging={"driver": "some/plugin"}),
    "/usr/syno": svc(volumes=["/usr/syno:/s"]),
    "/var/packages": svc(volumes=["/var/packages:/p"]),
}


def run_both_passes(compose, name="foo", meta=None, project_dir=None):
    meta = meta or {}
    reasons = cb.validate_git(compose, meta, DATA)
    if reasons:
        return reasons
    _, allow = cb.validate_meta(meta, DATA)
    e = managed(compose, name=name)
    pd = project_dir or tempfile.mkdtemp()
    *_, err = cb.render_config(e, cb.docker_read, pd, OWNER, allow=allow, self_name=OWNER, data_dir=DATA)
    return [err] if err else []


@needs_compose
@pytest.mark.parametrize("case", sorted(PROBES))
def test_probe_is_refused_by_the_real_parser_path(case):
    assert run_both_passes(PROBES[case]), case


@needs_compose
@pytest.mark.parametrize("case", ["privileged string", "network HOST", "bind type BIND /etc", "/volume1/docker",
                                  "deployer DATA_DIR", "traversal to homes", "rshared propagation",
                                  "external host network", "rbind driver_opts /", "foreign volume",
                                  "hand-made bind volume", "foreign network", "network named host",
                                  "default network renamed", "syslog remote", "logging plugin"])
def test_probe_is_refused_by_the_config_pass_alone(case, monkeypatch):
    # Even with pass 1 bypassed, pass 2 (on what compose parsed) refuses.
    monkeypatch.setattr(cb, "validate_git", lambda *a, **k: [])
    assert run_both_passes(PROBES[case]), case


@needs_compose
def test_real_parser_accepts_own_named_volume_and_network():
    c = {**svc(volumes=["data:/x"], networks=["n"], logging={"driver": "local"}),
         "volumes": {"data": {}}, "networks": {"n": {}}}
    assert run_both_passes(c) == []


@needs_compose
def test_real_parser_accepts_the_real_apps():
    doc = load(alcatraz_files()["hosts/alcatraz/immich-photos-pull/docker-compose.yml"])
    meta = doc.pop("x-deploy")
    compose = {k: v for k, v in doc.items() if not k.startswith("x-")}
    assert run_both_passes(compose, "immich-photos-pull", meta) == []
    # The shape PR 4's own compose will have.
    self_compose = {"services": {OWNER: {
        "image": IMG, "container_name": OWNER, "restart": "unless-stopped", "user": "0:0", "mem_limit": "256m",
        "environment": ["DEPLOYER_BACKEND=compose", "DRY_RUN=1", "TZ=America/New_York"],
        "volumes": [f"{cb.SELF_SOCKET}:{cb.SELF_SOCKET}", f"{DATA}:{DATA}", f"{DATA}/hc-url:/run/hc-url:ro"]}}}
    assert run_both_passes(self_compose, OWNER) == []
    assert run_both_passes(self_compose, "not-the-deployer")


@needs_compose
def test_real_parser_accepts_the_canary():
    doc = load(alcatraz_files()["hosts/alcatraz/deploy-canary/docker-compose.yml"])
    meta = doc.pop("x-deploy")
    compose = {k: v for k, v in doc.items() if not k.startswith("x-")}
    assert run_both_passes(compose, "deploy-canary", meta) == []
    # Before cutover it reads `missing` with the create note.
    r = cb.evaluate_compose_app({**managed(compose, name="deploy-canary"), "create": True}, {"deploy-canary": "h"},
                                [], owner=OWNER)
    assert r["state"] == "missing" and "x-deploy.create" in r["reasons"][0]


# ------------------------------------------------------------ fetch (needs git) and entrypoint


@needs_git
def test_compose_fetch_writes_validated_desired(tmp_path):
    good = f"services:\n  s:\n    image: {IMG}\n"
    bad = f"services:\n  s:\n    image: {IMG}\n    volumes: ['/etc:/etc']\n    devices: ['/dev/sda:/dev/sda']\n"
    commit(tmp_path / "remote", {"hosts/alcatraz/good/docker-compose.yml": good,
                                 "hosts/alcatraz/bad/docker-compose.yml": bad,
                                 "hosts/hestia/other/docker-compose.yml": good})
    by = {e["name"]: e for e in fetch(tmp_path, tmp_path / "data")["apps"]}
    assert set(by) == {"good", "bad"}
    assert by["good"]["status"] == "managed" and by["good"]["adopt"] is False
    assert by["bad"]["status"] == "invalid" and "devices" in by["bad"]["reason"]


def run_entrypoint(tmp_path, env):
    """deployer.sh with stub python3 / nsenter / sleep; sleep ends the loop."""
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    calls = tmp_path / "calls.log"
    for tool in ("python3", "nsenter"):
        (stubs / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\nexit 0\n')
    (stubs / "sleep").write_text('#!/bin/sh\nkill -TERM "$PPID"\n')
    for f in stubs.iterdir():
        f.chmod(0o755)
    p = subprocess.run(["bash", os.path.join(SRC, "deployer.sh")], capture_output=True, text=True, timeout=20,
                       env={"PATH": f"{stubs}:{os.environ.get('PATH', '')}", **env})
    return p, (calls.read_text() if calls.exists() else "")


needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")


@needs_bash
@pytest.mark.parametrize("env", [{}, {"DEPLOYER_BACKEND": ""}, {"DEPLOYER_BACKEND": "truenas"}])
def test_entrypoint_truenas_case(tmp_path, env):
    p, calls = run_entrypoint(tmp_path, env)
    assert "hestia-deployer: tick every 300s, DRY_RUN=1 (default)" in p.stdout
    assert calls.splitlines()[0] == "python3 /app/deployer.py fetch"
    assert "compose_backend" not in calls
    # The host half is the nsenter line. Where /app/deployer.py does not exist
    # its stdin redirect fails before nsenter runs; inside the image it runs.
    assert "nsenter -t 1 -m -u -i -n -p -- /usr/bin/python3 - reconcile" in calls or "reconcile exited" in p.stdout


@needs_bash
def test_entrypoint_compose_case(tmp_path):
    p, calls = run_entrypoint(tmp_path, {"DEPLOYER_BACKEND": "compose", "INTERVAL_SECONDS": "7"})
    assert "(compose backend): tick every 7s" in p.stdout
    assert calls.splitlines() == ["python3 /app/compose_backend.py version", "python3 /app/compose_backend.py fetch",
                                  "python3 /app/compose_backend.py reconcile"]


@needs_bash
def test_entrypoint_refuses_an_unknown_backend_without_echoing_it(tmp_path):
    p, calls = run_entrypoint(tmp_path, {"DEPLOYER_BACKEND": f"bogus-{HOST_SENTINEL}"})
    assert p.returncode == 2 and calls == ""
    assert "unknown DEPLOYER_BACKEND" in p.stderr
    assert HOST_SENTINEL not in p.stdout + p.stderr


def test_entrypoint_truenas_path_is_unchanged():
    # hestia (DEPLOYER_BACKEND unset) still runs exactly the pre-HOMELAB-118
    # loop: container fetch, then the nsenter host reconcile.
    src = open(os.path.join(SRC, "deployer.sh")).read()
    tail = src.split("esac\n", 1)[1]
    assert tail == '''
echo "hestia-deployer: tick every ${INTERVAL_SECONDS}s, DRY_RUN=${DRY_RUN:-1 (default)}"
while true; do
  python3 "$SCRIPT" fetch
  rc=$?
  [ "$rc" -ne 0 ] && echo "fetch exited $rc"
  nsenter -t 1 -m -u -i -n -p -- /usr/bin/python3 - reconcile < "$SCRIPT"
  rc=$?
  [ "$rc" -ne 0 ] && echo "reconcile exited $rc"
  sleep "$INTERVAL_SECONDS"
done
'''
