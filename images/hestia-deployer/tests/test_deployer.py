"""Unit tests for images/hestia-deployer/deployer.py (HOMELAB-111, Phase 1 shadow).

The host-side path against the real middleware is exercised by the shadow run
on hestia itself (docs/operations/apps/hestia-deployer.md). Here the TrueNAS
client and docker are fakes, and the headline property is tested directly:
in Phase 1 nothing can call app.update / app.stop / app.start / app.create.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.dirname(HERE))

import deployer as d  # noqa: E402

IMG = "ghcr.io/gjcourt/foo:2026-11-09-abcdef0@sha256:" + "a" * 64
OLD = "ghcr.io/gjcourt/foo:2026-11-01-1234567@sha256:" + "b" * 64
SHA = "0123456789abcdef0123456789abcdef01234567"
MUTATING = ("app.update", "app.stop", "app.start", "app.create", "app.delete", "app.redeploy")


def load(text):
    return yaml.safe_load(text)


def entry(text, path="hosts/hestia/foo/docker-compose.yml"):
    return d.parse_entry(path, text, load)


# ------------------------------------------------------------ phase 1 guards


def test_apply_is_disabled_in_phase_1():
    assert d.APPLY_ENABLED is False


@pytest.mark.parametrize("env,expected", [({}, True), ({"DRY_RUN": "1"}, True), ({"DRY_RUN": "true"}, True),
                                          ({"DRY_RUN": "yes"}, True), ({"DRY_RUN": ""}, True),
                                          ({"DRY_RUN": "0"}, False), ({"DRY_RUN": "false"}, False)])
def test_dry_run_defaults_on(env, expected):
    assert d.dry_run_from_env(env) is expected


def test_mutations_never_allowed_in_phase_1():
    assert not d.mutations_allowed(True)
    assert not d.mutations_allowed(False)


class FakeClient:
    def __init__(self, apps=None, configs=None):
        self.calls = []
        self.apps = apps or []
        self.configs = configs or {}

    def call(self, method, *args, **kwargs):
        self.calls.append(method)
        if method == "app.query":
            return self.apps
        if method == "app.config":
            return json.loads(json.dumps(self.configs[args[0]]))
        return None


@pytest.mark.parametrize("method", MUTATING)
def test_guarded_client_refuses_mutations(method):
    fake = FakeClient()
    g = d.GuardedClient(fake)
    with pytest.raises(d.DryRunViolation):
        g.call(method, "foo", {})
    assert fake.calls == []


def test_guarded_client_is_default_deny():
    with pytest.raises(d.DryRunViolation):
        d.GuardedClient(FakeClient()).call("system.reboot")


def test_guarded_client_cannot_enable_mutations_in_phase_1():
    with pytest.raises(d.DryRunViolation):
        d.GuardedClient(FakeClient(), allow_mutations=True)


@pytest.mark.parametrize("dry_run", [True, False])
def test_apply_app_refuses_before_any_call(dry_run):
    fake = FakeClient()
    with pytest.raises(d.DryRunViolation):
        d.apply_app(fake, "foo", {"services": {}}, dry_run=dry_run)
    assert fake.calls == []


@pytest.mark.parametrize("sub", ["rm", "stop", "start", "run", "exec", "compose", "kill"])
def test_docker_is_read_only(sub):
    with pytest.raises(d.DryRunViolation):
        d.docker_read(sub, "x")


# ------------------------------------------------------------ discovery


def test_default_name():
    assert d.default_name("hosts/hestia/monitoring/docker-compose-node-exporter.yml") == "node-exporter"
    assert d.default_name("hosts/hestia/libation/docker-compose.yml") == "libation"


def test_is_compose_path():
    assert d.is_compose_path("hosts/hestia/a/docker-compose.yml")
    assert d.is_compose_path("hosts/hestia/a/b/docker-compose-x.yml")
    assert not d.is_compose_path("hosts/hestia/a/compose.yaml")
    assert not d.is_compose_path("hosts/alcatraz/a/docker-compose.yml")


def test_managed_entry_strips_top_level_x_and_dates():
    e = entry("x-deploy:\n  archived: false\n  archived-at: 2026-05-16\nx-other: 1\n"
              f"services:\n  foo:\n    image: {IMG}\n")
    assert e["status"] == "managed"
    assert e["compose"] == {"services": {"foo": {"image": IMG}}}
    json.dumps(e)  # desired.json must serialise


@pytest.mark.parametrize("bad", ['"false"', '"true"', "1", "yes please"])
def test_archived_must_be_a_bool_and_invalid_is_per_app(bad):
    e = entry(f"x-deploy:\n  archived: {bad}\nservices:\n  foo:\n    image: {IMG}\n")
    assert e["status"] == "invalid" and "archived" in e["reason"]


def test_archived_true():
    e = entry(f"x-deploy:\n  archived: true\n  archived-reason: gone\nservices:\n  foo:\n    image: {IMG}\n")
    assert e["status"] == "archived" and e["reason"] == "gone"


@pytest.mark.parametrize("key", ["paused", "create"])
def test_new_flags_must_be_bools(key):
    e = entry(f"x-deploy:\n  {key}: \"true\"\nservices:\n  foo:\n    image: {IMG}\n")
    assert e["status"] == "invalid"


def test_paused_preserve_env_create_are_carried():
    e = entry("x-deploy:\n  paused: true\n  create: true\n  preserve-env: [B, A, A]\n"
              f"services:\n  foo:\n    image: {IMG}\n")
    assert e["status"] == "managed"
    assert e["paused"] is True and e["create"] is True and e["preserve_env"] == ["A", "B"]


@pytest.mark.parametrize("val", ["A", "[1]", '[""]', "{A: 1}"])
def test_preserve_env_must_be_a_list_of_names(val):
    e = entry(f"x-deploy:\n  preserve-env: {val}\nservices:\n  foo:\n    image: {IMG}\n")
    assert e["status"] == "invalid"


@pytest.mark.parametrize("text", ["services: {}\n", "foo: 1\n", "- a\n", "services:\n  foo: bar\n", ":\n  - [\n"])
def test_invalid_shapes(text):
    assert entry(text)["status"] == "invalid"


def test_nested_x_keys_are_invalid():
    e = entry(f"services:\n  foo:\n    image: {IMG}\n    x-note: hi\n")
    assert e["status"] == "invalid" and "services.foo.x-note" in e["reason"]


def test_bad_app_name_is_invalid():
    e = entry(f"x-deploy:\n  name: Foo_Bar\nservices:\n  foo:\n    image: {IMG}\n")
    assert e["status"] == "invalid"


def test_runner_and_self_are_excluded():
    svc = f"services:\n  foo:\n    image: {IMG}\n"
    assert entry(svc, "hosts/hestia/actions-runner/docker-compose.yml")["status"] == "excluded"
    assert entry(svc, "hosts/hestia/hestia-deployer/docker-compose.yml")["status"] == "excluded"
    assert entry("x-deploy:\n  name: hestia-deployer\n" + svc)["status"] == "excluded"


def test_duplicate_active_names_are_both_invalid():
    svc = f"services:\n  foo:\n    image: {IMG}\n"
    out = d.discover({
        "hosts/hestia/a/docker-compose-foo.yml": svc,
        "hosts/hestia/b/docker-compose.yml": "x-deploy:\n  name: foo\n" + svc,
        "hosts/hestia/c/docker-compose.yml": svc,
    }, load)
    by_file = {e["file"]: e for e in out}
    assert by_file["hosts/hestia/a/docker-compose-foo.yml"]["status"] == "invalid"
    assert by_file["hosts/hestia/b/docker-compose.yml"]["status"] == "invalid"
    assert "duplicate" in by_file["hosts/hestia/b/docker-compose.yml"]["reason"]
    assert by_file["hosts/hestia/c/docker-compose.yml"]["status"] == "managed"


def test_archived_file_sharing_an_active_name_is_shadowed():
    svc = f"services:\n  foo:\n    image: {IMG}\n"
    out = d.discover({
        "hosts/hestia/monitoring/docker-compose.yml": "x-deploy:\n  name: thermalscope\n  archived: true\n" + svc,
        "hosts/hestia/thermalscope/docker-compose.yml": svc,
    }, load)
    by_file = {e["file"]: e for e in out}
    assert by_file["hosts/hestia/thermalscope/docker-compose.yml"]["status"] == "managed"
    assert by_file["hosts/hestia/monitoring/docker-compose.yml"].get("shadowed") is True


def test_two_archived_files_with_one_name_emit_one_series():
    svc = f"services:\n  foo:\n    image: {IMG}\n"
    arch = "x-deploy:\n  name: same\n  archived: true\n" + svc
    out = d.discover({"hosts/hestia/a/docker-compose.yml": arch, "hosts/hestia/b/docker-compose.yml": arch}, load)
    assert [e.get("shadowed", False) for e in out] == [False, True]


def real_tree():
    files = {}
    for path in glob.glob(os.path.join(ROOT, "hosts/hestia/**/docker-compose*.yml"), recursive=True):
        with open(path) as f:
            files[os.path.relpath(path, ROOT)] = f.read()
    return files


def deploy_hestia_rule(files):
    """The discovery in .github/workflows/deploy-hestia.yml:86-130, re-stated."""
    names = {}
    for path, text in sorted(files.items()):
        if path.startswith("hosts/hestia/actions-runner/"):
            continue
        doc = yaml.safe_load(text) or {}
        meta = doc.get("x-deploy") or {}
        if meta.get("archived") is True:
            continue
        names[path] = meta.get("name") or d.default_name(path)
    return names


def test_real_tree_matches_deploy_hestia_discovery():
    files = real_tree()
    out = d.discover(files, load)
    assert [e for e in out if e["status"] == "invalid"] == []
    managed = {e["file"]: e["name"] for e in out if e["status"] == "managed"}
    expected = {p: n for p, n in deploy_hestia_rule(files).items() if n != d.SELF_NAME}
    assert managed == expected
    excluded = {e["file"] for e in out if e["status"] == "excluded"}
    assert "hosts/hestia/actions-runner/docker-compose.yml" in excluded
    assert "hosts/hestia/hestia-deployer/docker-compose.yml" in excluded
    assert "gha-runner-watchdog" in managed.values()


# ------------------------------------------------------------ compare


def test_normalize_environment_list_and_map_agree():
    a = d.normalize({"services": {"s": {"environment": ["A=1", "B=x=y", "C", "D="]}}})
    b = d.normalize({"services": {"s": {"environment": {"A": 1, "B": "x=y", "C": None, "D": ""}}}})
    assert a == b
    assert d.diff_paths(a, b) == []


def test_normalize_bools_and_labels():
    a = d.normalize({"services": {"s": {"environment": {"X": True}, "labels": ["k=v"]}}})
    assert a["services"]["s"]["environment"] == {"X": "true"}
    assert a["services"]["s"]["labels"] == {"k": "v"}


def test_diff_paths():
    a = {"services": {"s": {"image": IMG, "environment": {"A": "1"}, "ports": ["1:1"]}}}
    b = {"services": {"s": {"image": OLD, "environment": {"A": "2", "Z": "q"}, "ports": ["1:1", "2:2"]}}}
    assert d.diff_paths(a, b) == ["services.s.environment.A", "services.s.environment.Z",
                                  "services.s.image", "services.s.ports"]
    assert d.diff_paths(a, a) == []


def test_describe_paths_prints_images_but_never_other_values():
    desired = {"services": {"s": {"image": IMG, "environment": {"PASSWORD": "hunter2"}}}}
    stored = {"services": {"s": {"image": OLD, "environment": {"PASSWORD": "s3cret"}}}}
    out = d.describe_paths(d.diff_paths(desired, stored), desired, stored)
    text = "\n".join(out)
    assert f"{OLD} -> {IMG}" in text
    assert "services.s.environment.PASSWORD" in text
    assert "hunter2" not in text and "s3cret" not in text


def test_merge_secrets_preserves_listed_keys():
    desired = {"services": {"s": {"environment": ["TOKEN=", "A=1"]}}}
    stored = {"services": {"s": {"environment": {"TOKEN": "real", "A": "1"}}}}
    merged, blocked = d.merge_secrets(desired, stored, ["TOKEN"])
    assert blocked == []
    assert merged["services"]["s"]["environment"]["TOKEN"] == "real"
    assert d.diff_paths(merged, d.normalize(stored)) == []


def test_merge_secrets_blocks_unlisted_keys_fail_closed():
    desired = {"services": {"s": {"environment": {"TOKEN": "", "OTHER": ""}}}}
    stored = {"services": {"s": {"environment": {"TOKEN": "real", "OTHER": ""}}}}
    merged, blocked = d.merge_secrets(desired, stored, [])
    assert blocked == ["s.TOKEN"]
    assert merged["services"]["s"]["environment"]["TOKEN"] == ""


def test_merge_secrets_removal_is_not_blocked():
    desired = {"services": {"s": {"environment": {"A": "1"}}}}
    stored = {"services": {"s": {"environment": {"A": "1", "GONE": "x"}}}}
    merged, blocked = d.merge_secrets(desired, stored, [])
    assert blocked == []
    assert d.diff_paths(merged, d.normalize(stored)) == ["services.s.environment.GONE"]


def test_merge_secrets_does_not_mutate_inputs():
    desired = {"services": {"s": {"environment": {"T": ""}}}}
    d.merge_secrets(desired, {"services": {"s": {"environment": {"T": "v"}}}}, ["T"])
    assert desired["services"]["s"]["environment"]["T"] == ""


def test_image_drift():
    stored = {"services": {"a": {"image": IMG}, "b": {"image": IMG}, "c": {"image": IMG}, "e": {"image": IMG}}}
    containers = {"a": {"image": IMG, "running": True, "restarting": False},
                  "b": {"image": OLD, "running": True, "restarting": False},
                  "e": {"image": IMG, "running": True, "restarting": True}}
    out = d.image_drift(stored, containers)
    assert out == ["b: container runs " + OLD + ", stored " + IMG, "c: no container", "e: container not running"]


def managed(compose, **kw):
    e = {"name": "foo", "file": "hosts/hestia/foo/docker-compose.yml", "status": "managed", "reason": "",
         "paused": False, "preserve_env": [], "create": False, "compose": compose}
    e.update(kw)
    return e


RUNNING = {"s": {"image": IMG, "running": True, "restarting": False}}
CUSTOM = {"name": "foo", "custom_app": True, "state": "RUNNING"}


def test_evaluate_synced_and_pending():
    c = {"services": {"s": {"image": IMG}}}
    assert d.evaluate_app(managed(c), CUSTOM, c, RUNNING)["state"] == "synced"
    r = d.evaluate_app(managed({"services": {"s": {"image": OLD}}}), CUSTOM, c, RUNNING)
    assert r["state"] == "pending" and r["changes"] == [f"services.s.image: {IMG} -> {OLD}"]


def test_evaluate_quirk_is_pending():
    c = {"services": {"s": {"image": IMG}}}
    stale = {"s": {"image": OLD, "running": True, "restarting": False}}
    r = d.evaluate_app(managed(c), CUSTOM, c, stale)
    assert r["state"] == "pending" and r["changes"] == [] and r["drift"]


def test_evaluate_states():
    c = {"services": {"s": {"image": IMG}}}
    assert d.evaluate_app(managed(c), None, None, {})["state"] == "missing"
    assert "Phase 5" in d.evaluate_app(managed(c, create=True), None, None, {})["reasons"][0]
    assert d.evaluate_app(managed(c), {"name": "foo", "custom_app": False}, None, {})["state"] == "invalid"
    assert d.evaluate_app(managed(c), CUSTOM, {"weird": 1}, {})["state"] == "invalid"
    assert d.evaluate_app(managed(c), CUSTOM, "yaml text", {})["state"] == "invalid"
    assert d.evaluate_app(managed({"services": {"s": {"image": OLD}}}, paused=True), CUSTOM, c, RUNNING)["state"] == "paused"
    assert d.evaluate_app({**managed(c), "status": "archived"}, CUSTOM, None, {})["state"] == "archived"
    assert d.evaluate_app({**managed(None), "status": "invalid", "reason": "x"}, CUSTOM, None, {})["state"] == "invalid"


def test_evaluate_blocked_never_carries_value_in_reasons():
    desired = {"services": {"s": {"image": IMG, "environment": {"TOKEN": ""}}}}
    stored = {"services": {"s": {"image": IMG, "environment": {"TOKEN": "hunter2"}}}}
    r = d.evaluate_app(managed(desired), CUSTOM, stored, RUNNING)
    assert r["state"] == "blocked"
    assert "hunter2" not in json.dumps({k: v for k, v in r.items() if k != "merged"})


def test_plan_applies_cap_and_eyes():
    rs = [{"app": n, "state": "pending"} for n in ("node-exporter", "homelabscope-heartbeat", "b", "a", "c", "d")]
    rs.append({"app": "z", "state": "synced"})
    chosen, deferred = d.plan_applies(rs, cap=3)
    assert chosen == ["a", "b", "c"]
    assert deferred == ["d", "homelabscope-heartbeat", "node-exporter"]
    chosen, _ = d.plan_applies(rs, cap=10)
    assert not ("node-exporter" in chosen and "homelabscope-heartbeat" in chosen)


# ------------------------------------------------------------ metrics


def test_job_help_strings_match_other_textfile_writers():
    # node-exporter drops a family whose HELP differs between files (PR #1864).
    src = open(os.path.join(ROOT, "images/homelabscope-heartbeat/heartbeat.sh")).read()
    for name, text in d.JOB_HELP.items():
        assert re.search(r"# HELP " + re.escape(name) + " " + re.escape(text) + r"\s*$", src, re.M), name


def test_deploy_help_strings_are_pinned():
    # Plan D9: fixed once. Changing one breaks node-exporter merging across
    # deployer versions mid-rollout; change it only deliberately, here too.
    assert d.DEPLOY_HELP == {
        "homelabscope_deploy_head_info": "master commit the last hestia-deployer tick evaluated.",
        "homelabscope_deploy_head_commit_seconds": "Commit time of the master commit the last hestia-deployer tick evaluated.",
        "homelabscope_deploy_app_state": "hestia-deployer state of each hestia app (1 for the current state).",
        "homelabscope_deploy_app_applied_info": "Last commit hestia-deployer successfully applied to the app.",
        "homelabscope_deploy_app_last_apply_seconds": "Unix timestamp of hestia-deployer's last successful apply of the app.",
        "homelabscope_deploy_paused": "1 when hestia-deployer is paused (scope=global: PAUSE file; otherwise x-deploy.paused).",
        "homelabscope_deploy_unmanaged_apps": "Count of TrueNAS apps on hestia with no compose file in Git.",
        "homelabscope_deploy_dry_run": "1 when hestia-deployer runs in dry-run (shadow) mode and applies nothing.",
    }


def test_no_other_writer_uses_the_deploy_families():
    hits = []
    for path in glob.glob(os.path.join(ROOT, "images/**/*"), recursive=True):
        if "hestia-deployer" in path or not os.path.isfile(path):
            continue
        with open(path, errors="ignore") as f:
            if "homelabscope_deploy_" in f.read():
                hits.append(path)
    assert hits == []


def parse_prom(body):
    samples = {}
    for line in body.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        key, value = line.rsplit(" ", 1)
        samples[key] = float(value)
    return samples


def test_render_metrics_ok():
    results = [{"app": "a", "state": "synced"}, {"app": "b", "state": "paused"}, {"app": "c", "state": "pending"}]
    body = d.render_metrics(ok=True, now=100, prev_success=50, duration=3, dry_run=True, sha=SHA,
                            commit_seconds=90, results=results, global_paused=False, unmanaged=2)
    s = parse_prom(body)
    assert s['homelabscope_job_last_success_seconds{job="hestia-deployer"}'] == 100
    assert s['homelabscope_job_last_status{job="hestia-deployer"}'] == 0
    assert s['homelabscope_job_max_age_seconds{job="hestia-deployer"}'] == 1800
    assert s['homelabscope_deploy_head_info{commit="0123456"}'] == 1
    assert s["homelabscope_deploy_head_commit_seconds"] == 90
    assert s['homelabscope_deploy_app_state{app="c",state="pending"}'] == 1
    assert s['homelabscope_deploy_paused{scope="global"}'] == 0
    assert s['homelabscope_deploy_paused{scope="b"}'] == 1
    assert s["homelabscope_deploy_unmanaged_apps"] == 2
    assert s["homelabscope_deploy_dry_run"] == 1
    assert "applied_info" not in body
    # every family has exactly one HELP and TYPE
    helps = re.findall(r"^# HELP (\S+)", body, re.M)
    assert len(helps) == len(set(helps))
    assert d.previous_success(body) == 100


def test_render_metrics_first_failure_does_not_fabricate_success():
    body = d.render_metrics(ok=False, now=100, prev_success=None, duration=1, dry_run=True)
    assert "homelabscope_job_last_success_seconds" not in body
    assert 'homelabscope_job_last_status{job="hestia-deployer"} 1' in body
    body = d.render_metrics(ok=False, now=100, prev_success=42, duration=1, dry_run=True)
    assert 'homelabscope_job_last_success_seconds{job="hestia-deployer"} 42' in body


def test_render_metrics_one_series_per_app():
    results = [{"app": "a", "state": "invalid"}, {"app": "a", "state": "invalid"}, {"app": "b", "state": "synced"},
               {"app": "b", "state": "pending"}]
    body = d.render_metrics(ok=True, now=1, prev_success=None, duration=0, dry_run=True, results=results)
    lines = [ln for ln in body.splitlines() if ln.startswith("homelabscope_deploy_app_state")]
    assert lines == ['homelabscope_deploy_app_state{app="a",state="invalid"} 1',
                     'homelabscope_deploy_app_state{app="b",state="invalid"} 1']


def test_render_applied():
    body = d.render_metrics(ok=True, now=1, prev_success=None, duration=0, dry_run=False,
                            applied={"a": {"commit": SHA, "at": 77}})
    assert 'homelabscope_deploy_app_applied_info{app="a",commit="0123456"} 1' in body
    assert 'homelabscope_deploy_app_last_apply_seconds{app="a"} 77' in body


# ------------------------------------------------------------ reconcile end to end


def write_desired(tmp_path, apps, sha=SHA):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "desired.json").write_text(json.dumps({"version": d.DESIRED_VERSION, "sha": sha,
                                                   "commit_time": 1700000000, "apps": apps}))
    return data


def fake_docker(images):
    """images: app -> {service: image}."""
    def run(*args):
        if args[0] == "ps":
            proj = [a for a in args if a.startswith("label=com.docker.compose.project=ix-")][0]
            app = proj.split("ix-", 1)[1]
            return " ".join(f"{app}::{svc}" for svc in images.get(app, {}))
        if args[0] == "inspect":
            out = []
            for cid in args[1:]:
                app, svc = cid.split("::", 1)
                out.append({"Config": {"Image": images[app][svc], "Labels": {"com.docker.compose.service": svc}},
                            "State": {"Running": True, "Restarting": False}})
            return json.dumps(out)
        raise AssertionError(f"unexpected docker {args}")
    return run


SECRET = "hunter2-very-secret"


def scenario(tmp_path):
    apps = [
        managed({"services": {"s": {"image": IMG}}}, name="synced-app"),
        managed({"services": {"s": {"image": IMG}}}, name="bumped"),
        managed({"services": {"s": {"image": IMG, "environment": {"TOKEN": ""}}}}, name="secretive"),
        managed({"services": {"s": {"image": IMG, "environment": {"TOKEN": ""}}}}, name="kept",
                preserve_env=["TOKEN"]),
        managed({"services": {"s": {"image": IMG}}}, name="absent"),
        {**managed(None, name="old"), "status": "archived", "reason": "gone"},
        {**managed(None, name="actions-runner"), "status": "excluded"},
    ]
    data = write_desired(tmp_path, apps)
    configs = {
        "synced-app": {"services": {"s": {"image": IMG}}},
        "bumped": {"services": {"s": {"image": OLD}}},
        "secretive": {"services": {"s": {"image": IMG, "environment": {"TOKEN": SECRET}}}},
        "kept": {"services": {"s": {"image": IMG, "environment": {"TOKEN": SECRET}}}},
    }
    scale = [{"name": n, "id": n, "custom_app": True, "state": "RUNNING"} for n in configs]
    scale += [{"name": "gha-runner", "id": "gha-runner", "custom_app": True},
              {"name": "catalog-thing", "id": "catalog-thing", "custom_app": False}]
    docker = fake_docker({"synced-app": {"s": IMG}, "bumped": {"s": OLD}, "secretive": {"s": IMG}, "kept": {"s": IMG}})
    return data, FakeClient(scale, configs), docker


@pytest.mark.parametrize("dry_run", [True, False])
def test_reconcile_only_reads_and_reports(tmp_path, capsys, dry_run):
    data, client, docker = scenario(tmp_path)
    tf = tmp_path / "textfile"
    rc = d.reconcile(client, docker=docker, data_dir=str(data), textfile_dir=str(tf), dry_run=dry_run, now=1000)
    assert rc == 0
    assert set(client.calls) <= d.READ_METHODS
    assert not set(client.calls) & set(MUTATING)

    body = (tf / d.DEPLOY_TEXTFILE).read_text()
    s = parse_prom(body)
    assert s['homelabscope_deploy_app_state{app="synced-app",state="synced"}'] == 1
    assert s['homelabscope_deploy_app_state{app="bumped",state="pending"}'] == 1
    assert s['homelabscope_deploy_app_state{app="secretive",state="blocked"}'] == 1
    assert s['homelabscope_deploy_app_state{app="kept",state="synced"}'] == 1
    assert s['homelabscope_deploy_app_state{app="absent",state="missing"}'] == 1
    assert s['homelabscope_deploy_app_state{app="old",state="archived"}'] == 1
    assert not any('app="gha-runner"' in k for k in s)
    assert s["homelabscope_deploy_unmanaged_apps"] == 1  # catalog-thing
    # APPLY_ENABLED is False, so even DRY_RUN=0 reports dry-run.
    assert s["homelabscope_deploy_dry_run"] == 1

    state_text = (data / "state.json").read_text()
    out = capsys.readouterr().out
    assert "would_apply=['bumped']" in out
    assert "secretive [blocked]" in out and "s.TOKEN" in out
    for text in (body, state_text, out):
        assert SECRET not in text
    assert oct(os.stat(data / "state.json").st_mode & 0o777) == "0o600"


def test_reconcile_global_pause_is_reported(tmp_path):
    data, client, docker = scenario(tmp_path)
    (data / "PAUSE").write_text("")
    tf = tmp_path / "tf"
    d.reconcile(client, docker=docker, data_dir=str(data), textfile_dir=str(tf), dry_run=True, now=1)
    assert 'homelabscope_deploy_paused{scope="global"} 1' in (tf / d.DEPLOY_TEXTFILE).read_text()


def test_reconcile_without_desired_fails_without_fabricating(tmp_path):
    tf = tmp_path / "tf"
    client = FakeClient()
    rc = d.reconcile(client, docker=fake_docker({}), data_dir=str(tmp_path), textfile_dir=str(tf), now=1)
    assert rc == 1 and client.calls == []
    body = (tf / d.DEPLOY_TEXTFILE).read_text()
    assert "homelabscope_job_last_success_seconds" not in body
    assert 'homelabscope_job_last_status{job="hestia-deployer"} 1' in body


def test_reconcile_middleware_down(tmp_path):
    data, _, docker = scenario(tmp_path)

    class Down:
        def call(self, *a, **k):
            raise ConnectionError("socket")

    tf = tmp_path / "tf"
    assert d.reconcile(Down(), docker=docker, data_dir=str(data), textfile_dir=str(tf), now=1) == 1


def test_reconcile_keeps_previous_success_on_failure(tmp_path):
    data, client, docker = scenario(tmp_path)
    tf = tmp_path / "tf"
    d.reconcile(client, docker=docker, data_dir=str(data), textfile_dir=str(tf), now=500)
    os.remove(data / "desired.json")
    d.reconcile(client, docker=docker, data_dir=str(data), textfile_dir=str(tf), now=900)
    body = (tf / d.DEPLOY_TEXTFILE).read_text()
    assert 'homelabscope_job_last_success_seconds{job="hestia-deployer"} 500' in body


# ------------------------------------------------------------ fetch (needs git)


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def make_repo(path, files):
    subprocess.run(["git", "init", "-q", "-b", "master", str(path)], check=True)
    for rel, text in files.items():
        p = path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "c"], check=True, env=env)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


@needs_git
def test_fetch_writes_desired_for_one_commit(tmp_path):
    svc = f"services:\n  foo:\n    image: {IMG}\n"
    sha = make_repo(tmp_path / "remote", {
        "hosts/hestia/foo/docker-compose.yml": svc,
        "hosts/hestia/actions-runner/docker-compose.yml": svc,
        "hosts/hestia/foo/README.md": "not compose",
        "apps/base/x.yaml": "kind: X\n",
    })
    data = tmp_path / "data"
    url = f"file://{tmp_path / 'remote'}"
    assert d.fetch_main(str(data), url) == 0
    desired = json.loads((data / "desired.json").read_text())
    assert desired["sha"] == sha
    by = {e["file"]: e for e in desired["apps"]}
    assert set(by) == {"hosts/hestia/foo/docker-compose.yml", "hosts/hestia/actions-runner/docker-compose.yml"}
    assert by["hosts/hestia/foo/docker-compose.yml"]["status"] == "managed"
    assert by["hosts/hestia/actions-runner/docker-compose.yml"]["status"] == "excluded"
    # unchanged SHA: no rewrite
    mtime = os.stat(data / "desired.json").st_mtime_ns
    assert d.fetch_main(str(data), url) == 0
    assert os.stat(data / "desired.json").st_mtime_ns == mtime


@needs_git
def test_fetch_failure_keeps_previous_desired(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "desired.json").write_text('{"version": 1, "sha": "' + SHA + '"}')
    assert d.fetch_main(str(data), f"file://{tmp_path / 'nope'}") == 1
    assert json.loads((data / "desired.json").read_text())["sha"] == SHA


# ------------------------------------------------------------ packaging


def test_compose_is_shadow_and_bootstrap_archived():
    path = os.path.join(ROOT, "hosts/hestia/hestia-deployer/docker-compose.yml")
    doc = yaml.safe_load(open(path))
    assert doc["x-deploy"]["name"] == d.SELF_NAME
    assert doc["x-deploy"]["archived"] is True
    svc = doc["services"]["hestia-deployer"]
    assert svc["privileged"] is True and svc["pid"] == "host"
    env = d._kv_list_to_map(svc["environment"])
    assert env["DRY_RUN"] == "1"
    assert env["DATA_DIR"] == d.DATA_DIR
    # same path in the container and on the host: the two halves share it
    assert f"{d.DATA_DIR}:{d.DATA_DIR}" in svc["volumes"]


def test_entrypoint_runs_both_halves():
    sh = open(os.path.join(HERE, "..", "deployer.sh")).read()
    assert 'python3 "$SCRIPT" fetch' in sh
    assert "nsenter -t 1 -m -u -i -n -p -- /usr/bin/python3 - reconcile" in sh


def test_host_half_needs_no_yaml():
    # reconcile runs on hestia's python3; yaml is imported only inside fetch_main.
    src = open(os.path.join(HERE, "..", "deployer.py")).read()
    top = [line for line in src.splitlines() if re.match(r"^(import|from) ", line)]
    assert not any("yaml" in line for line in top)
