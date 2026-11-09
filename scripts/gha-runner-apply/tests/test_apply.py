"""Unit tests for the pure logic in scripts/gha-runner-apply/apply.py.

The host-side path (truenas_api_client, docker, GitHub) is exercised by the
dry-run on hestia described in docs/operations/apps/gha-runner.md section 6.
"""

import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.dirname(HERE))

import apply  # noqa: E402

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


def test_plan():
    assert apply.plan(PIN, PIN, PIN) == "converged"
    assert apply.plan(OLD, OLD, PIN) == "apply"
    # app.update no-op quirk: config updated, container still old.
    assert apply.plan(PIN, OLD, PIN) == "apply"
    # container missing entirely
    assert apply.plan(PIN, None, PIN) == "apply"


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
