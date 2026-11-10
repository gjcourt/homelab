"""Unit tests for images/hestia-deployer/compose_backend.py (HOMELAB-118, A-Phase 1 shadow).

docker is a fake that understands exactly the read calls the backend makes,
behind the backend's own argv allowlist. The headline property is tested
directly: in shadow, nothing can run compose pull / up, run, rm or stop, and
no value from the host ever reaches the log, state.json or the textfile.
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.dirname(HERE))

import compose_backend as cb  # noqa: E402
import deployer as d  # noqa: E402

IMG = "ghcr.io/gjcourt/foo:2026-11-09-abcdef0@sha256:" + "a" * 64
NEW = "ghcr.io/gjcourt/foo:2026-11-10-1234567@sha256:" + "c" * 64
SHA = "0123456789abcdef0123456789abcdef01234567"
OWNER = "alcatraz-deployer"
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


ALLOWED = [
    ["ps", "-a", "-q"],
    ["inspect", "--type", "container", "--format", "x", "id"],
    ["image", "inspect", "foo"],
    ["version", "--format", "x"],
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
    ["compose", "pull"], ["compose", "up", "-d"], ["compose", "ls"], ["compose", "down"],
    ["compose", "-f", "-", "-p", "foo", "--project-directory", "/d", "up", "-d", "--remove-orphans"],
    ["compose", "-f", "-", "-p", "foo", "--project-directory", "/d", "pull"],
    ["compose", "-f", "/etc/x.yml", "-p", "foo", "--project-directory", "/d", "config"],
    ["compose", "-f", "-", "-p", "foo", "config"],  # no fixed project directory
    cb.compose_args("foo", "/d", "--output", "/tmp/x"),
    cb.compose_args("foo", "/d", "--format", "yaml"),
    cb.compose_args("foo", "/d", "--resolve-image-digests"),
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
    cb.docker_read("ps", "-a")
    assert HOST_SENTINEL not in json.dumps(seen["env"])
    assert set(seen["env"]) <= set(cb.CHILD_ENV_KEYS)
    assert seen["cwd"] == "/"


# ------------------------------------------------------------ validation (A3)


def svc(**body):
    return {"services": {"s": {"image": IMG, **body}}}


@pytest.mark.parametrize("vol,ok", [
    ("/volume1/docker/foo/data:/data", True),           # own subtree
    ("/volume1/docker/foo:/data", True),
    ("/volume1/docker/bar/data:/data", True),           # someone else's subtree: not a guarded prefix
    ("/volume1/misc:/m", True),                          # unlisted path: allowed, as in CI
    ("named:/data", True),                               # named volume
    ("/data", True),                                     # anonymous volume
    ("/:/host", False),
    ("/volume1:/v", False),
    ("/etc/passwd:/p:ro", False),
    ("/var/run/docker.sock:/var/run/docker.sock", False),
    ("/run/x:/x", False),
    ("/volume1/@iSCSI/lun:/l", False),
    ("/volume1/homes/george:/h", False),                # protected, not allowed
    ("/volume1/docker/foo/../../homes/george:/h", False),  # normalised before the check
    ("./data:/data", False),                             # relative: resolves in the deployer container
    ("~/data:/data", False),
])
def test_bind_guard_short_syntax(vol, ok):
    assert (cb.validate(svc(volumes=[vol]), {}, "foo") == []) is ok


def test_bind_guard_long_syntax_and_driver_opts():
    assert cb.validate(svc(volumes=[{"type": "bind", "source": "/etc", "target": "/e"}]), {}, "foo")
    assert cb.validate(svc(volumes=[{"type": "volume", "source": "named", "target": "/e"}]), {}, "foo") == []
    c = svc(volumes=["dev:/d"])
    c["volumes"] = {"dev": {"driver": "local", "driver_opts": {"type": "none", "o": "bind", "device": "/dev"}}}
    assert any("hard-denied" in r for r in cb.validate(c, {}, "foo"))


def test_allow_host_paths_opens_protected_but_never_hard_denied():
    meta = {"allow-host-paths": ["/volume1/homes/george/Photos", "/etc"]}
    assert cb.validate(svc(volumes=["/volume1/homes/george/Photos:/p"]), meta, "foo") == []
    assert cb.validate(svc(volumes=["/volume1/homes/mara/Photos:/p"]), meta, "foo")
    assert cb.validate(svc(volumes=["/etc/x:/x"]), meta, "foo")


def test_self_may_mount_the_socket_and_nothing_else():
    sock = svc(volumes=["/var/run/docker.sock:/var/run/docker.sock"])
    assert cb.validate(sock, {}, OWNER, self_name=OWNER) == []
    assert cb.validate(sock, {}, "foo", self_name=OWNER)
    assert cb.validate(svc(volumes=["/var/run/other.sock:/s"]), {}, OWNER, self_name=OWNER)
    assert cb.validate(svc(volumes=["/etc:/etc"]), {}, OWNER, self_name=OWNER)
    assert cb.validate(svc(privileged=True), {}, OWNER, self_name=OWNER)


@pytest.mark.parametrize("body,needle", [
    ({"privileged": True}, "privileged"),
    ({"network_mode": "host"}, "network_mode"),
    ({"pid": "host"}, "pid"),
    ({"cap_add": ["NET_ADMIN"]}, "cap_add"),
    ({"env_file": ".env"}, "env_file"),
    ({"build": "."}, "build"),
    ({"extends": {"file": "x.yml", "service": "y"}}, "extends"),
    ({"environment": {"TOKEN": "${TOKEN}"}}, "interpolation"),
    ({"environment": ["TOKEN=$TOKEN"]}, "interpolation"),
    ({"command": "echo ${HOME:-x}"}, "interpolation"),
    ({"labels": {cb.OWNER_LABEL: "me"}}, "set by the deployer"),
])
def test_refused_service_keys(body, needle):
    reasons = cb.validate(svc(**body), {}, "foo")
    assert reasons and any(needle in r for r in reasons), reasons


def test_escaped_dollar_passes_static_check():
    assert cb.validate(svc(command="echo $$HOME"), {}, "foo") == []


def test_refused_top_level():
    c = svc()
    c["secrets"] = {"k": {"file": "/volume1/docker/foo/k"}}
    assert any("secrets.k" in r for r in cb.validate(c, {}, "foo"))
    c = svc()
    c["include"] = ["other.yml"]
    assert any("include" in r for r in cb.validate(c, {}, "foo"))
    assert any("adopt" in r for r in cb.validate(svc(), {"adopt": "yes"}, "foo"))
    assert any("allow-host-paths" in r for r in cb.validate(svc(), {"allow-host-paths": "/x"}, "foo"))


def test_validation_reasons_never_carry_values():
    reasons = cb.validate(svc(environment={"TOKEN": "${" + GIT_SENTINEL + "}"}, env_file=GIT_SENTINEL), {}, "foo")
    assert reasons and GIT_SENTINEL not in " ".join(reasons)


def test_real_alcatraz_composes():
    files = {}
    for dirpath, _, names in os.walk(os.path.join(ROOT, "hosts/alcatraz")):
        for n in names:
            p = os.path.relpath(os.path.join(dirpath, n), ROOT)
            if d.is_compose_path(p):
                files[p] = open(os.path.join(ROOT, p)).read()
    by = {e["file"]: e for e in cb.discover(files, load)}
    assert by["hosts/alcatraz/immich-photos-pull/docker-compose.yml"]["status"] == "archived"
    assert by["hosts/alcatraz/actions-runner/docker-compose.yml"]["status"] == "excluded"
    assert not [e for e in by.values() if e["status"] == "invalid"], by
    # Un-archived, the photo pull passes the guard through its allow-list.
    doc = load(files["hosts/alcatraz/immich-photos-pull/docker-compose.yml"])
    meta = doc.pop("x-deploy")
    compose = {k: v for k, v in doc.items() if not k.startswith("x-")}
    assert cb.validate(compose, meta, "immich-photos-pull") == []
    assert cb.validate(compose, {}, "immich-photos-pull")  # and fails without it


def test_discover_makes_one_app_invalid_not_the_tick():
    good = f"services:\n  s:\n    image: {IMG}\n"
    bad = f"services:\n  s:\n    image: {IMG}\n    privileged: true\n"
    adopt = f"x-deploy:\n  adopt: true\n  allow-host-paths: [/volume1/homes/x]\nservices:\n  s:\n    image: {IMG}\n"
    es = {e["name"]: e for e in cb.discover({"hosts/alcatraz/good/docker-compose.yml": good,
                                             "hosts/alcatraz/bad/docker-compose.yml": bad,
                                             "hosts/alcatraz/adopted/docker-compose.yml": adopt}, load)}
    assert es["good"]["status"] == "managed" and es["good"]["adopt"] is False
    assert es["bad"]["status"] == "invalid" and "privileged" in es["bad"]["reason"] and es["bad"]["compose"] is None
    assert es["adopted"]["adopt"] is True and es["adopted"]["allow_host_paths"] == ["/volume1/homes/x"]
    json.dumps(list(es.values()))


# ------------------------------------------------------------ fingerprint / owner label (A4)


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


def ev(entry, containers, hashes=None, **kw):
    return cb.evaluate_compose_app(entry, {"s": "H1"} if hashes is None else hashes, containers, owner=OWNER, **kw)


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


def test_evaluate_missing_and_create_note():
    r = ev(managed(create=True), [])
    assert r["state"] == "missing" and "not acted on until cutover" in r["reasons"][0]


def test_evaluate_not_owned_is_blocked_unless_adopt():
    r = ev(managed(), [ctr(owner=None, h="OTHER")])
    assert r["state"] == "blocked" and "not deployer-owned" in r["reasons"][0]
    r = ev(managed(adopt=True), [ctr(owner=None, h="OTHER")])
    assert r["state"] == "pending" and "adopt" in r["reasons"][0]
    # one stray hand-made container in an otherwise owned project also blocks
    r = ev(managed(), [ctr(), ctr(service="x", owner=None, name="foo-x-1")])
    assert r["state"] == "blocked"


def test_evaluate_orphan_and_missing_service_and_not_running():
    r = ev(managed(), [ctr(), ctr(service="gone", name="foo-gone-1")])
    assert r["state"] == "pending" and "orphan container foo-gone-1" in r["drift"][0]
    r = ev(managed(), [ctr()], hashes={"s": "H1", "t": "H2"})
    assert r["drift"] == ["t: no container"]
    r = ev(managed(), [ctr(restarting=True)])
    assert r["state"] == "pending" and "not running" in r["drift"][0]
    # one of two running is not a stopped project: the exited one is drift
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
    r = ev(managed(), [ctr()], config_error="docker compose config failed: CalledProcessError")
    assert r["state"] == "invalid"


# ------------------------------------------------------------ reconcile end to end (fake docker)


def config_hash(service_body):
    return hashlib.sha256(json.dumps(service_body, sort_keys=True).encode()).hexdigest()[:16]


class FakeDocker:
    """Fake docker behind the backend's real allowlist.

    projects: project name -> list of container dicts, where hash "auto"
    means "matches the desired config" (the owner-labelled Git compose).
    """

    def __init__(self, projects, loose=(), desired=None, fail=()):
        self.projects = projects
        self.loose = list(loose)
        self.desired = desired or {}
        self.fail = set(fail)
        self.calls = []

    def auto_hash(self, project, service):
        compose = cb.with_owner_label(self.desired[project], OWNER)
        return config_hash(compose["services"][service])

    def __call__(self, *args, stdin=None):
        cb.check_read_argv(args)
        self.calls.append(args)
        if args[0] in self.fail:
            raise subprocess.CalledProcessError(1, ["docker", args[0]])
        if args[0] == "ps" and "--format" in args:
            lines = [f"{c['name']}\t{p}" for p, cs in self.projects.items() for c in cs]
            lines += [f"{n}\t" for n in self.loose]
            return "\n".join(lines) + "\n"
        if args[0] == "ps":
            label = [a for a in args if a.startswith(f"label={cb.PROJECT_LABEL}=")][0]
            project = label.split("=", 2)[2]
            return "\n".join(f"{project}::{i}" for i, _ in enumerate(self.projects.get(project, [])))
        if args[0] == "inspect":
            out = []
            for cid in args[5:]:
                project, i = cid.split("::")
                c = self.projects[project][int(i)]
                h = self.auto_hash(project, c["service"]) if c.get("hash") == "auto" else c.get("hash")
                labels = {cb.PROJECT_LABEL: project, cb.SERVICE_LABEL: c["service"], cb.HASH_LABEL: h,
                          "org.opencontainers.image.secret-ish": HOST_SENTINEL}
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
            flags = args[args.index("config") + 1:]
            if "--hash" in flags:
                return "".join(f"{s} {config_hash(b)}\n" for s, b in sorted(compose["services"].items()))
            if "--no-interpolate" in flags:
                return json.dumps(compose)
            # Like compose 5.x: the interpolated form is also normalised, so it
            # is NOT equal to the uninterpolated one even with no $ at all.
            text = json.dumps(compose).replace("$$", "$")
            out = json.loads(text)
            out["name"] = args[args.index("-p") + 1]
            for body in out["services"].values():
                body.setdefault("command", None)
            return json.dumps(out)
        raise AssertionError(f"unexpected docker {args}")


def write_desired(tmp_path, apps, sha=SHA):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "desired.json").write_text(json.dumps({"version": d.DESIRED_VERSION, "sha": sha,
                                                   "commit_time": 1700000000, "apps": apps}))
    return data


def scenario(tmp_path, bumped_image=IMG):
    synced = {"services": {"s": {"image": IMG, "environment": {"TOKEN": GIT_SENTINEL}}}}
    bumped = {"services": {"s": {"image": bumped_image}}}
    plain = {"services": {"s": {"image": IMG}}}
    apps = [
        managed(synced, name="synced-app"),
        managed(bumped, name="bumped"),
        managed(plain, name="handmade"),
        managed(plain, name="absent", create=True),
        managed(plain, name="stopped-app"),
        {**managed(None, name="old"), "status": "archived", "reason": "gone", "compose": None},
        {**managed(None, name="actions-runner"), "status": "excluded", "compose": None},
        # Valid when written, then tampered: the reconcile-time guard catches it.
        managed({"services": {"s": {"image": IMG, "privileged": True}}}, name="tampered"),
    ]
    data = write_desired(tmp_path, apps)
    desired = {a["name"]: a["compose"] for a in apps if a["compose"]}
    projects = {
        "synced-app": [{"name": "synced-app-s-1", "service": "s", "hash": "auto", "owner": OWNER}],
        "bumped": [{"name": "bumped-s-1", "service": "s", "hash": "auto", "owner": OWNER}],
        "handmade": [{"name": "handmade-s-1", "service": "s", "hash": "dsm-made"}],
        "stopped-app": [{"name": "stopped-app-s-1", "service": "s", "hash": "auto", "owner": OWNER,
                         "running": False}],
        "gha-runner": [{"name": "gha-runner", "service": "runner", "hash": "x"}],
        "dsm-thing": [{"name": "dsm-thing-1", "service": "x", "hash": "x"}],
    }
    return data, FakeDocker(projects, loose=["portainer"], desired=desired)


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


@pytest.mark.parametrize("dry_run", [True, False])
def test_reconcile_only_reads_and_reports(tmp_path, capsys, dry_run):
    data, docker = scenario(tmp_path)
    rc, body, state_text = tick(tmp_path, docker, data, dry_run=dry_run)
    assert rc == 0
    for call in docker.calls:  # every call passed the allowlist; none mutates
        assert call[0] in ("ps", "inspect", "compose")
        if call[0] == "compose":
            assert "config" in call
    s = parse_prom(body)
    assert state_of(s, "synced-app", "synced") == 1
    assert state_of(s, "bumped", "synced") == 1
    assert state_of(s, "handmade", "blocked") == 1
    assert state_of(s, "absent", "missing") == 1
    assert state_of(s, "stopped-app", "synced") == 1
    assert state_of(s, "old", "archived") == 1
    assert state_of(s, "tampered", "invalid") == 1
    assert not any('app="actions-runner"' in k or 'app="gha-runner"' in k for k in s)
    assert s['homelabscope_deploy_unmanaged_apps{host="alcatraz"}'] == 2  # dsm-thing + portainer
    assert s['homelabscope_deploy_dry_run{host="alcatraz"}'] == 1  # compose switch off: always dry-run
    assert s['homelabscope_job_last_status{job="alcatraz-deployer"}'] == 0
    out = capsys.readouterr().out
    assert "tick DRY-RUN sha=0123456" in out
    assert "unmanaged=['dsm-thing', 'container:portainer']" in out
    assert "handmade [blocked]" in out and "not deployer-owned" in out
    assert "tampered [invalid]" in out and "privileged" in out
    assert oct(os.stat(data / "state.json").st_mode & 0o777) == "0o600"
    st = json.loads(state_text)
    assert st["last_tick"]["backend"] == "compose" and st["last_tick"]["would_apply"] == []
    assert set(st["baseline"]) == {"synced-app", "bumped", "stopped-app"}


def test_no_value_reaches_log_state_or_metrics(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    rc, body, state_text = tick(tmp_path, docker, data)
    out = capsys.readouterr().out
    for text in (out, state_text, body):
        assert GIT_SENTINEL not in text
        assert HOST_SENTINEL not in text
    # Git content is public: the sentinel is in desired.json only because Git
    # has it. Nothing from the host is.
    desired_text = (data / "desired.json").read_text()
    assert GIT_SENTINEL in desired_text and HOST_SENTINEL not in desired_text


def test_a_merge_is_predicted_with_its_paths(tmp_path, capsys):
    # Tick 1 on the deployed config records a baseline; tick 2 sees a new SHA
    # whose compose bumps one image. The prediction names exactly that path.
    data, docker = scenario(tmp_path)
    tick(tmp_path, docker, data)
    capsys.readouterr()
    apps = json.loads((data / "desired.json").read_text())["apps"]
    for a in apps:
        if a["name"] == "bumped":
            a["compose"]["services"]["s"]["image"] = NEW
    write_desired(tmp_path, apps, sha="f" * 40)
    rc, body, _ = tick(tmp_path, docker, data)
    s = parse_prom(body)
    assert state_of(s, "bumped", "pending") == 1
    assert state_of(s, "synced-app", "synced") == 1
    out = capsys.readouterr().out
    assert "would_apply=['bumped']" in out
    assert f"bumped [pending] (hosts/alcatraz/bumped/docker-compose.yml) changes: services.s.image: {IMG} -> {NEW}" in out
    assert "drift: s: config hash differs (bumped-s-1)" in out


def test_unchanged_sha_twice_reports_no_pending(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    tick(tmp_path, docker, data)
    rc, body, _ = tick(tmp_path, docker, data)
    assert rc == 0 and 'state="pending"' not in body


def test_escaped_dollar_is_valid_end_to_end(tmp_path):
    c = {"services": {"s": {"image": IMG, "command": "echo $$HOME"}}}
    data = write_desired(tmp_path, [managed(c, name="escaped")])
    docker = FakeDocker({"escaped": [{"name": "e", "service": "s", "hash": "auto", "owner": OWNER}]},
                        desired={"escaped": c})
    _, body, _ = tick(tmp_path, docker, data)
    assert state_of(parse_prom(body), "escaped", "synced") == 1


def test_interpolation_compose_still_sees_is_refused(tmp_path):
    # Defence in depth behind validate(): if a ${VAR} reaches compose anyway,
    # the uninterpolated render shows it and the app is invalid.
    e = managed({"services": {"s": {"image": IMG, "environment": {"T": "${TOKEN}"}}}})
    hashes, tree, err = cb.render_config(e, FakeDocker({}), str(tmp_path), OWNER)
    assert hashes is None and tree is None
    assert err.startswith("interpolation refused in v1") and "services.s.environment.T" in err


def test_compose_config_failure_is_invalid_by_type_only(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    docker.fail = {"compose"}
    rc, body, _ = tick(tmp_path, docker, data)
    s = parse_prom(body)
    assert state_of(s, "synced-app", "invalid") == 1
    assert "docker compose config failed: CalledProcessError" in capsys.readouterr().out


def test_inspect_failure_is_unevaluated_not_pending(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    docker.fail = {"inspect"}
    rc, body, state = tick(tmp_path, docker, data)
    assert rc == 1
    s = parse_prom(body)
    assert not any('state="pending"' in k for k in s)
    assert not any('app="synced-app"' in k for k in s)
    assert state_of(s, "absent", "missing") == 1  # no inspect needed
    assert s['homelabscope_job_last_status{job="alcatraz-deployer"}'] == 1
    assert "unevaluated this tick: docker CalledProcessError" in capsys.readouterr().out


def test_docker_down_fails_the_tick(tmp_path, capsys):
    data, docker = scenario(tmp_path)
    docker.fail = {"ps"}
    tf = tmp_path / "tf"
    assert cb.reconcile(docker=docker, data_dir=str(data), textfile_dir=str(tf), now=1) == 1
    body = (tf / d.DEPLOY_TEXTFILE).read_text()
    assert 'homelabscope_job_last_status{job="alcatraz-deployer"} 1' in body
    assert "docker ps failed" in capsys.readouterr().out


def test_non_empty_project_directory_fails_closed(tmp_path):
    data, docker = scenario(tmp_path)
    (data / "compose-project").mkdir()
    (data / "compose-project" / ".env").write_text("TOKEN=x\n")
    tf = tmp_path / "tf"
    assert cb.reconcile(docker=docker, data_dir=str(data), textfile_dir=str(tf), now=1) == 1
    assert not any(c[0] == "compose" for c in docker.calls)


def test_global_pause_is_reported(tmp_path):
    data, docker = scenario(tmp_path)
    (data / "PAUSE").write_text("")
    _, body, _ = tick(tmp_path, docker, data)
    assert 'homelabscope_deploy_paused{host="alcatraz",scope="global"} 1' in body


def test_no_desired_json_yet(tmp_path):
    tf = tmp_path / "tf"
    (tmp_path / "data").mkdir()
    assert cb.reconcile(docker=FakeDocker({}), data_dir=str(tmp_path / "data"), textfile_dir=str(tf), now=1) == 1


# ------------------------------------------------------------ fetch (needs git) and entrypoint


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@needs_git
def test_compose_fetch_writes_validated_desired(tmp_path):
    good = f"services:\n  s:\n    image: {IMG}\n"
    bad = f"services:\n  s:\n    image: {IMG}\n    volumes: ['/etc:/etc']\n"
    remote = tmp_path / "remote"
    subprocess.run(["git", "init", "-q", "-b", "master", str(remote)], check=True)
    for rel, text in {"hosts/alcatraz/good/docker-compose.yml": good,
                      "hosts/alcatraz/bad/docker-compose.yml": bad,
                      "hosts/hestia/other/docker-compose.yml": good}.items():
        (remote / rel).parent.mkdir(parents=True, exist_ok=True)
        (remote / rel).write_text(text)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(remote), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(remote), "commit", "-q", "-m", "c"], check=True, env=env)
    data = tmp_path / "data"
    assert d.fetch_main(str(data), f"file://{remote}", discover_fn=cb.discover) == 0
    by = {e["name"]: e for e in json.loads((data / "desired.json").read_text())["apps"]}
    assert set(by) == {"good", "bad"}
    assert by["good"]["status"] == "managed" and by["good"]["adopt"] is False
    assert by["bad"]["status"] == "invalid" and "hard-denied" in by["bad"]["reason"]


def run_entrypoint(env):
    script = os.path.join(os.path.dirname(HERE), "deployer.sh")
    return subprocess.run(["bash", script], env={"PATH": os.environ.get("PATH", ""), **env},
                          capture_output=True, text=True, timeout=10)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_entrypoint_refuses_an_unknown_backend_without_echoing_it():
    p = run_entrypoint({"DEPLOYER_BACKEND": f"bogus-{HOST_SENTINEL}"})
    assert p.returncode == 2
    assert "unknown DEPLOYER_BACKEND" in p.stderr
    assert HOST_SENTINEL not in p.stdout + p.stderr


def test_entrypoint_truenas_path_is_unchanged():
    # hestia (DEPLOYER_BACKEND unset) still runs exactly the pre-HOMELAB-118
    # loop: container fetch, then the nsenter host reconcile.
    src = open(os.path.join(os.path.dirname(HERE), "deployer.sh")).read()
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
