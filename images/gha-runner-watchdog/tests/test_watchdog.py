"""Unit tests for the pure logic in images/gha-runner-watchdog/watchdog.py.

The host-side path (truenas_api_client, docker, GitHub) is exercised by the
dry-run on hestia described in docs/operations/apps/gha-runner.md section 6.1.
"""

import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.dirname(HERE))

import watchdog as apply  # noqa: E402

PIN = "myoung34/github-runner:ubuntu-noble@sha256:" + "a" * 64
OLD = "myoung34/github-runner:ubuntu-noble@sha256:" + "b" * 64


def test_parse_pin_reads_the_real_compose():
    with open(os.path.join(ROOT, "hosts/hestia/actions-runner/docker-compose.yml")) as f:
        pin = apply.parse_pin(f.read())
    assert apply.PIN_RE.match(pin)


@pytest.mark.parametrize("line", [f"    image: {PIN}", f'    image: "{PIN}"', f"    image: {PIN}  # renovate"])
def test_parse_pin_forms(line):
    assert apply.parse_pin(f"services:\n  runner:\n{line}\n") == PIN


@pytest.mark.parametrize(
    "image",
    [
        "myoung34/github-runner:ubuntu-noble",  # tag only, not digest-pinned
        "myoung34/github-runner:latest@sha256:" + "a" * 63,
        "evil/runner:ubuntu-noble@sha256:" + "a" * 64,
    ],
)
def test_parse_pin_rejects_unpinned_or_foreign(image):
    with pytest.raises(ValueError):
        apply.parse_pin(f"services:\n  runner:\n    image: {image}\n")


def test_parse_pin_missing():
    with pytest.raises(ValueError):
        apply.parse_pin("services: {}\n")


def test_on_pin():
    assert apply.on_pin(PIN, PIN, PIN)
    assert not apply.on_pin(OLD, OLD, PIN)
    # app.update no-op quirk: config updated, container still old.
    assert not apply.on_pin(PIN, OLD, PIN)
    # container missing entirely
    assert not apply.on_pin(PIN, None, PIN)


def r(name="hestia", status="online", busy=False):
    return {"name": name, "status": status, "busy": busy}


def test_is_idle():
    assert apply.is_idle([r()], worker_running=False)
    assert not apply.is_idle([r()], worker_running=True)
    assert not apply.is_idle([r(busy=True)], worker_running=False)
    assert not apply.is_idle([r(status="offline")], worker_running=False)
    assert not apply.is_idle([], worker_running=False)  # API error / not registered
    assert not apply.is_idle([r(name="other")], worker_running=False)
    # a stale offline registration next to the live one does not block
    assert apply.is_idle([r(status="offline"), r()], worker_running=False)


def test_render_metrics_roundtrip():
    body = apply.render_metrics(1700000000, 0, 42)
    assert apply.previous_success(body) == 1700000000
    assert f'homelabscope_job_last_status{{job="{apply.JOB}"}} 0' in body
    assert f'homelabscope_job_max_age_seconds{{job="{apply.JOB}"}} {apply.MAX_AGE_SECONDS}' in body


def test_failure_without_prior_success_does_not_fabricate_one():
    body = apply.render_metrics(None, 1, 5)
    assert "homelabscope_job_last_success_seconds" not in body
    assert f'homelabscope_job_last_status{{job="{apply.JOB}"}} 1' in body


def test_help_strings_match_other_textfile_writers():
    # node-exporter drops a family whose HELP differs between files (PR #1864).
    src = open(os.path.join(ROOT, "images/homelabscope-heartbeat/heartbeat.sh")).read()
    for name, text in apply.HELP.items():
        assert re.search(r"# HELP " + re.escape(name) + " " + re.escape(text) + r"\s*$", src, re.M), name


NOW = 1_800_000_000.0
LONG_AGO = NOW - apply.UNHEALTHY_GRACE_S - 1
JUST_NOW = NOW - 60


def d(**kw):
    base = dict(drift=False, online=True, idle=True, worker=False, now=NOW, unhealthy_since=None, last_recovery=None)
    base.update(kw)
    return apply.decide(**base)


def test_decide_healthy_on_pin_is_ok():
    assert d() == "ok"


def test_decide_drift_applies_only_when_idle():
    assert d(drift=True) == "apply"
    assert d(drift=True, idle=False, worker=True) == "defer"
    assert d(drift=True, idle=False) == "defer"  # online but busy


def test_decide_drift_on_a_dead_runner_applies_after_grace():
    # The deprecated-runner case: never idle, nothing running, offline for long.
    dead = dict(drift=True, online=False, idle=False, worker=False)
    assert d(**dead, unhealthy_since=JUST_NOW) == "defer"
    assert d(**dead, unhealthy_since=LONG_AGO) == "apply"
    assert d(**{**dead, "worker": True}, unhealthy_since=LONG_AGO) == "defer"


def test_decide_offline_inside_grace_waits():
    # The ephemeral runner is briefly offline between every job.
    assert d(online=False, idle=False, unhealthy_since=JUST_NOW) == "wait"
    assert d(online=False, idle=False, unhealthy_since=None) == "wait"


def test_decide_dead_on_pin_recovers_with_cooldown():
    dead = dict(online=False, idle=False, unhealthy_since=LONG_AGO)
    assert d(**dead) == "recover"
    assert d(**dead, last_recovery=NOW - 60) == "cooldown"
    assert d(**dead, last_recovery=NOW - apply.RECOVERY_COOLDOWN_S - 1) == "recover"
    assert d(**dead, worker=True) == "defer"  # never kill a running job


@pytest.mark.parametrize(
    "header,expected",
    [
        ("2026-12-01 08:00:00 UTC", 1796112000),
        ("2026-12-01 00:00:00 -0800", 1796112000),
        (None, None),
        ("", None),
        ("not a date", None),
    ],
)
def test_parse_expiry(header, expected):
    assert apply.parse_expiry(header) == expected


def test_render_token_metrics():
    body = apply.render_token_metrics(True, 1796112000)
    assert 'homelabscope_gha_runner_token_valid{runner="hestia"} 1' in body
    assert 'homelabscope_gha_runner_token_expiry_seconds{runner="hestia"} 1796112000' in body
    # no expiry header -> series absent, never a made-up date
    body = apply.render_token_metrics(False, None)
    assert 'homelabscope_gha_runner_token_valid{runner="hestia"} 0' in body
    assert "token_expiry_seconds" not in body


def test_compose_mounts_what_the_script_writes():
    text = open(os.path.join(ROOT, "hosts/hestia/gha-runner-watchdog/docker-compose.yml")).read()
    assert "pid: host" in text and "privileged: true" in text
