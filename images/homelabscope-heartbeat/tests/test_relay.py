"""Tests for the alcatraz deploy relay in images/homelabscope-heartbeat/heartbeat.sh (HOMELAB-118).

heartbeat.sh is sourced (sourcing defines functions only) and its relay
functions are driven with a stub `ssh` on PATH. The accepted input is real:
images/hestia-deployer/deployer.py rendered as alcatraz would render it.
"""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SCRIPT = os.path.join(ROOT, "images/homelabscope-heartbeat/heartbeat.sh")
DEPLOYER = os.path.join(ROOT, "images/hestia-deployer")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")

SHA = "0123456789abcdef0123456789abcdef01234567"


def alcatraz_body(**over):
    """deployer.render_metrics() exactly as alcatraz's deployer writes it."""
    kw = dict(ok=True, now=100, prev_success=50, duration=3, dry_run=True, sha=SHA, commit_seconds=90,
              results=[{"app": "deploy-canary", "state": "missing"}, {"app": "immich-photos-pull", "state": "archived"},
                       {"app": "hand-made", "state": "adoptable"}, {"app": "p", "state": "paused"}],
              global_paused=True, unmanaged=3, applied={"deploy-canary": {"commit": SHA, "at": 77}})
    kw.update(over)
    code = (f"import json, sys; sys.path.insert(0, {DEPLOYER!r}); import deployer as d; "
            f"sys.stdout.write(d.render_metrics(**json.loads(sys.argv[1])))")
    env = {**os.environ, "DEPLOYER_HOST": "alcatraz", "DEPLOYER_JOB": "alcatraz-deployer",
           "DEPLOYER_SELF_NAME": "alcatraz-deployer", "DEPLOYER_HOSTS_PREFIX": "hosts/alcatraz/"}
    return subprocess.run([sys.executable, "-c", code, json.dumps(kw)], env=env, check=True,
                          capture_output=True, text=True).stdout


def bash(snippet, tmp_path, env=None):
    full = {"PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '')}", "TEXTFILE_DIR": str(tmp_path / "tf"),
            **(env or {})}
    return subprocess.run(["bash", "-c", f"source {SCRIPT!r}; {snippet}"], env=full, capture_output=True,
                          text=True, timeout=30)


def validate(tmp_path, body):
    f = tmp_path / "in.prom"
    f.write_text(body)
    return bash(f"validate_relay {str(f)!r}", tmp_path)


def test_relay_help_strings_equal_the_deployers():
    # One node-exporter serves both files: a HELP mismatch drops the family.
    src = open(SCRIPT).read()
    block = src[src.index("declare -A RELAY_HELP=("):src.index("\n)\n", src.index("declare -A RELAY_HELP=("))]
    relay = dict(re.findall(r'\[(\w+)\]="([^"]*)"', block))
    sys.path.insert(0, DEPLOYER)
    import deployer as d
    assert relay == {**d.JOB_HELP, **d.DEPLOY_HELP}


def test_real_alcatraz_output_is_accepted(tmp_path):
    for body in (alcatraz_body(), alcatraz_body(ok=False, sha=None, commit_seconds=None, results=[], applied={},
                                                unmanaged=None, prev_success=None)):
        p = validate(tmp_path, body)
        assert p.returncode == 0, p.stderr


REJECT = {
    "hestia-labelled sample": 'homelabscope_deploy_dry_run{host="hestia"} 1',
    "no host label": "homelabscope_deploy_dry_run 1",
    "host not first": 'homelabscope_deploy_app_state{app="x",host="alcatraz",state="synced"} 1',
    "label injection": 'homelabscope_deploy_app_state{host="alcatraz",app="x\\",host=\\"hestia",state="synced"} 1',
    "unknown label": 'homelabscope_deploy_app_state{host="alcatraz",job="x"} 1',
    "unknown deploy family": 'homelabscope_deploy_evil{host="alcatraz"} 1',
    "foreign job": 'homelabscope_job_last_status{job="immich-photos-backup"} 0',
    "job with extra label": 'homelabscope_job_last_status{job="alcatraz-deployer",host="alcatraz"} 0',
    "arbitrary series": 'node_cpu_seconds_total{cpu="0"} 1',
    "container family": 'homelabscope_container_running{name="x"} 1',
    "bad value": 'homelabscope_deploy_dry_run{host="alcatraz"} NaN',
    "timestamp suffix": 'homelabscope_deploy_dry_run{host="alcatraz"} 1 1700000000000',
    "changed HELP": "# HELP homelabscope_deploy_dry_run 1 when alcatraz-deployer is in dry-run.",
    "HELP unknown family": "# HELP node_cpu_seconds_total x",
    "TYPE counter": "# TYPE homelabscope_deploy_dry_run counter",
    "TYPE unknown": "# TYPE evil gauge",
    "other comment": "# a comment",
    "carriage return": 'homelabscope_deploy_dry_run{host="alcatraz"} 1\r',
    "leading space": ' homelabscope_deploy_dry_run{host="alcatraz"} 1',
}


@pytest.mark.parametrize("case", sorted(REJECT))
def test_rejected_lines(tmp_path, case):
    body = alcatraz_body()
    lines = body.splitlines()
    lines.insert(5, REJECT[case])
    p = validate(tmp_path, "\n".join(lines) + "\n")
    assert p.returncode == 1, case
    assert p.stderr.startswith("line 6: ")
    assert REJECT[case].strip() not in p.stderr  # never echoes the untrusted line


def with_line(body, at, line):
    lines = body.splitlines()
    lines.insert(at, line)
    return "\n".join(lines) + "\n"


def first_index(body, prefix):
    return next(i for i, ln in enumerate(body.splitlines()) if ln.startswith(prefix))


def test_structural_rejections(tmp_path):
    body = alcatraz_body()
    lines = body.splitlines()
    help_dry = next(ln for ln in lines if ln.startswith("# HELP homelabscope_deploy_dry_run"))
    type_dry = next(ln for ln in lines if ln.startswith("# TYPE homelabscope_deploy_dry_run"))
    sample_dry = next(ln for ln in lines if ln.startswith("homelabscope_deploy_dry_run{"))
    i = first_index(body, "homelabscope_deploy_dry_run{")
    cases = {
        "second HELP": with_line(body, i, help_dry),                    # HELP, TYPE, HELP, sample
        "second TYPE": with_line(body, i, type_dry),
        "HELP after samples": with_line(body, i + 1, help_dry),
        "TYPE after samples": with_line(body, i + 1, type_dry),
        "duplicate series": with_line(body, i + 1, sample_dry),
        "duplicate series, other value": with_line(body, i + 1, sample_dry.rsplit(" ", 1)[0] + " 0"),
        "duplicate label name": with_line(body, len(lines),
                                          'homelabscope_deploy_app_state{host="alcatraz",app="a",app="b",state="x"} 1'),
        "duplicate host label": with_line(body, len(lines), 'homelabscope_deploy_paused{host="alcatraz",scope="g",scope="h"} 1'),
    }
    for case, text in cases.items():
        p = validate(tmp_path, text)
        assert p.returncode == 1, case
        assert p.stderr.startswith("line "), (case, p.stderr)


def test_nul_bytes_are_rejected(tmp_path):
    f = tmp_path / "in.prom"
    body = alcatraz_body()
    f.write_bytes(body.encode().replace(b'host="alcatraz"', b'host="alca\x00traz"', 1))
    p = bash(f"validate_relay {str(f)!r}", tmp_path)
    assert p.returncode == 1 and "NUL" in p.stderr


def test_oversized_file_is_rejected(tmp_path):
    p = bash(f"RELAY_MAX_BYTES=100; f={str(tmp_path / 'big')!r}; head -c 101 /dev/zero | tr '\\0' 'x' > \"$f\"; "
             "validate_relay \"$f\"", tmp_path)
    assert p.returncode == 1 and "larger than" in p.stderr


def stub_ssh(tmp_path, body=None, fail=False):
    """An `ssh` that prints `body` (the remote file) or fails like a dead host."""
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    src = tmp_path / "remote.prom"
    if body is not None:
        src.write_text(body)
    (b / "ssh").write_text("#!/bin/sh\n" + ("exit 255\n" if fail else f'cat "{src}"\n'))
    (b / "ssh").chmod(0o755)


def collect(tmp_path, enabled="1"):
    (tmp_path / "tf").mkdir(exist_ok=True)
    return bash("collect_alcatraz_deploy; echo rc=$?", tmp_path, {"ALCATRAZ_DEPLOY_RELAY": enabled})


def relay_status(tmp_path):
    text = (tmp_path / "tf" / "homelabscope-relay.prom").read_text()
    return int(re.search(r'homelabscope_relay_last_status\{source="alcatraz-deploy"\} (\d)', text).group(1)), text


def test_turning_the_relay_off_disarms_it(tmp_path):
    # On, then off: neither the relayed job_* series (HomelabscopeJobStale)
    # nor relay_enabled 1 (the gated absent() arm) may linger.
    stub_ssh(tmp_path, alcatraz_body())
    collect(tmp_path)
    assert sorted(os.listdir(tmp_path / "tf")) == ["homelabscope-deploy-alcatraz.prom", "homelabscope-relay.prom"]
    p = collect(tmp_path, enabled="0")
    assert "rc=0" in p.stdout and "removed the relayed and status files" in p.stdout
    assert os.listdir(tmp_path / "tf") == []
    p = collect(tmp_path, enabled="0")      # idempotent, and quiet the second time
    assert "removed" not in p.stdout


def test_relay_is_off_by_default(tmp_path):
    stub_ssh(tmp_path, alcatraz_body())
    p = collect(tmp_path, enabled="0")
    assert "rc=0" in p.stdout
    assert os.listdir(tmp_path / "tf") == []  # no file at all: hestia unchanged until bootstrap


def test_accepted_file_is_published_atomically_and_unchanged(tmp_path):
    body = alcatraz_body()
    stub_ssh(tmp_path, body)
    p = collect(tmp_path)
    assert "rc=0" in p.stdout and "accepted" in p.stdout
    out = tmp_path / "tf" / "homelabscope-deploy-alcatraz.prom"
    assert out.read_text() == body
    assert oct(os.stat(out).st_mode & 0o777) == "0o644"
    status, text = relay_status(tmp_path)
    assert status == 0 and 'homelabscope_relay_enabled{source="alcatraz-deploy"} 1' in text
    assert "homelabscope_relay_last_success_seconds" in text
    assert [f for f in os.listdir(tmp_path / "tf") if f.startswith(".relay")] == []


def test_rejected_file_keeps_the_last_accepted_one(tmp_path):
    good = alcatraz_body()
    stub_ssh(tmp_path, good)
    collect(tmp_path)
    stub_ssh(tmp_path, good + 'homelabscope_deploy_dry_run{host="hestia"} 0\n')
    p = collect(tmp_path)
    assert "rc=1" in p.stdout and "REJECTED (line" in p.stdout and 'host="hestia"' not in p.stdout
    assert (tmp_path / "tf" / "homelabscope-deploy-alcatraz.prom").read_text() == good
    assert relay_status(tmp_path)[0] == 1
    assert [f for f in os.listdir(tmp_path / "tf") if f.startswith(".relay")] == []


def test_fetch_failure_keeps_the_last_accepted_one(tmp_path):
    good = alcatraz_body()
    stub_ssh(tmp_path, good)
    collect(tmp_path)
    stub_ssh(tmp_path, fail=True)
    p = collect(tmp_path)
    assert "rc=1" in p.stdout and "fetch failed" in p.stdout
    assert (tmp_path / "tf" / "homelabscope-deploy-alcatraz.prom").read_text() == good
    assert relay_status(tmp_path)[0] == 2


def test_first_ever_failure_publishes_nothing_but_status(tmp_path):
    stub_ssh(tmp_path, fail=True)
    collect(tmp_path)
    assert sorted(os.listdir(tmp_path / "tf")) == ["homelabscope-relay.prom"]
    status, text = relay_status(tmp_path)
    assert status == 2 and "last_success_seconds" not in text


def test_status_is_logged_on_transition_only(tmp_path):
    stub_ssh(tmp_path, fail=True)
    (tmp_path / "tf").mkdir()
    p = bash("collect_alcatraz_deploy; collect_alcatraz_deploy; collect_alcatraz_deploy", tmp_path,
             {"ALCATRAZ_DEPLOY_RELAY": "1"})
    assert p.stdout.count("fetch failed") == 1


def test_sourcing_runs_nothing(tmp_path):
    p = bash("echo sourced", tmp_path)
    assert p.stdout.strip() == "sourced"
    assert not (tmp_path / "tf").exists()
