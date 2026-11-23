"""Agent sessions pilots (plan step 2; George's D1-D3, 2026-11-22): which Jobs
are pilots, what a pilot Job gets, its token Secret, and the reaper."""

import hashlib
import hmac
import json
import os
import sys
import time

import pytest
from fakes import OWNER, FakeKube, FakePlane

from agent_desk import config
from agent_desk import jobs as jobmod
from agent_desk.dispatcher import Dispatcher

HERE = os.path.dirname(__file__)
TEMPLATE = os.path.join(HERE, "..", "..", "..", "scripts", "bench-cloud", "job.yaml")
KEY = b"k" * 32
WID = "3f1c2a9e-0000-4000-8000-0000000000aa"

BASE = """
ownerEmail: gjcourt@gmail.com
botEmail: gjcourt+agent-desk@gmail.com
projects:
  SANDBOX: gjcourt/agent-sandbox
  DOMESTIQUE: gjcourt/domestique
playbooks:
  docs-fix:
    deadline: 90m
    prompt: Make the docs-only change.
  dep-hold:
    deadline: 2h
    prompt: Investigate the held update.
"""
PILOTS = """
sessions:
  url: http://agent-sessions.agent-sessions-stage.svc:8080
  pilotProjects: [SANDBOX]
  pilotPlaybooks: [dep-hold]
"""


def make(tmp_path, sessions=PILOTS, key=KEY, dry_run=True):
    (tmp_path / "config.yaml").write_text(BASE + (sessions or ""))
    env = {"PLANE_URL": "http://plane", "PLANE_WORKSPACE": "ws",
           "DRY_RUN_JOBS": "true" if dry_run else "false"}
    if key is not None:
        kf = tmp_path / "session-key"
        kf.write_bytes(key + b"\n")
        os.environ["SESSION_KEY_FILE"] = str(kf)
    else:
        os.environ.pop("SESSION_KEY_FILE", None)
    try:
        cfg = config.load(str(tmp_path / "config.yaml"), env=env)
    finally:
        os.environ.pop("SESSION_KEY_FILE", None)
    cfg.job_template = TEMPLATE
    plane, kube = FakePlane(), FakeKube()
    plane.add_project("p-sb", "SANDBOX")
    plane.add_project("p-do", "DOMESTIQUE")
    logs = []
    return Dispatcher(cfg, plane, kube, log=logs.append), plane, kube, logs


def start(plane, d, pid="p-sb", iid=WID, playbook="docs-fix"):
    plane.add_item(pid, iid, name="t", seq=9)
    plane.label(pid, iid, f"playbook:{playbook}", OWNER)
    plane.label(pid, iid, "ready", OWNER)
    d.poll()


def env_map(job):
    return {e["name"]: e for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}


def assert_plain(job):
    """A Job that isn't a pilot gets none of the session parts."""
    env = env_map(job)
    assert "sessions_url" not in json.loads(env["TASK_TICKET"]["value"])
    assert jobmod.TOKEN_KEY not in env
    assert jobmod.PILOT_LABEL not in job["spec"]["template"]["metadata"]["labels"]
    assert jobmod.PILOT_LABEL not in job["metadata"]["labels"]


# --- config --------------------------------------------------------------------

def test_sessions_default_off(tmp_path):
    d, *_ = make(tmp_path, sessions=None)
    assert d.cfg.sessions == config.Sessions()
    assert not d.cfg.sessions.pilot("SANDBOX", "docs-fix")


def test_url_alone_pilots_nothing(tmp_path):
    d, *_ = make(tmp_path, sessions="sessions:\n  url: http://x:8080\n")
    assert not d.cfg.sessions.pilot("SANDBOX", "docs-fix")


def test_pilot_by_project_or_playbook(tmp_path):
    d, *_ = make(tmp_path)
    s = d.cfg.sessions
    assert s.pilot("SANDBOX", "docs-fix") and s.pilot("DOMESTIQUE", "dep-hold")
    assert not s.pilot("DOMESTIQUE", "docs-fix")


def test_production_config_pilots_nothing():
    prod = os.path.join(HERE, "..", "..", "..", "apps", "production", "agent-desk", "config.yaml")
    cfg = config.load(prod, env={"PLANE_URL": "http://plane", "PLANE_WORKSPACE": "ws"})
    assert not cfg.sessions.url and not cfg.sessions.pilot_projects and not cfg.sessions.pilot_playbooks


# --- the Job ---------------------------------------------------------------------

def test_pilot_job_gets_url_label_and_token_ref(tmp_path):
    d, plane, kube, _ = make(tmp_path)
    start(plane, d)
    job, dry = kube.created[0]
    tid = job["metadata"]["name"]
    env = env_map(job)
    assert json.loads(env["TASK_TICKET"]["value"])["sessions_url"] == \
        "http://agent-sessions.agent-sessions-stage.svc:8080"
    assert job["spec"]["template"]["metadata"]["labels"][jobmod.PILOT_LABEL] == "true"
    assert job["metadata"]["labels"][jobmod.PILOT_LABEL] == "true"
    assert env[jobmod.TOKEN_KEY]["valueFrom"]["secretKeyRef"] == {
        "name": f"{tid}-session", "key": jobmod.TOKEN_KEY, "optional": False}


def test_pilot_token_secret_is_owned_and_expires_at_deadline_plus_15m(tmp_path):
    d, plane, kube, _ = make(tmp_path)
    t0 = time.time()
    start(plane, d)
    job, _ = kube.created[0]
    tid = job["metadata"]["name"]
    (secret, dry), = kube.secrets
    assert dry is True                                  # dryRun=All when the Jobs are
    meta = secret["metadata"]
    assert (meta["name"], meta["namespace"]) == (f"{tid}-session", "bench-cloud")
    assert meta["labels"] == {jobmod.TOKEN_LABEL: "true", "agent-sessions/job": tid}
    assert meta["ownerReferences"] == [{"apiVersion": "batch/v1", "kind": "Job", "name": tid,
                                        "uid": f"uid-{tid}"}]
    tok = secret["stringData"][jobmod.TOKEN_KEY]
    v, exp, sig = tok.split(".")
    assert v == "v1" and t0 + 5400 + 900 - 2 <= int(exp) <= time.time() + 5400 + 900
    assert sig == hmac.new(KEY, f"{WID}|{tid}|{exp}".encode(), hashlib.sha256).hexdigest()


def test_playbook_deadline_sets_the_expiry(tmp_path):
    d, plane, kube, _ = make(tmp_path)
    t0 = time.time()
    start(plane, d, pid="p-do", playbook="dep-hold")       # a pilot by playbook, 2h
    tok = kube.secrets[0][0]["stringData"][jobmod.TOKEN_KEY]
    assert abs(int(tok.split(".")[1]) - (t0 + 7200 + 900)) < 3


@pytest.mark.parametrize("sessions,key,pid", [
    (None, KEY, "p-sb"),                                  # no sessions block
    (PILOTS, KEY, "p-do"),                                # neither project nor playbook listed
    (PILOTS, None, "p-sb"),                               # no session-key
    (PILOTS, b"short", "p-sb"),                           # session-key under 32 bytes
])
def test_a_job_that_isnt_a_pilot_gets_none_of_it(tmp_path, sessions, key, pid):
    d, plane, kube, logs = make(tmp_path, sessions=sessions, key=key)
    start(plane, d, pid=pid)
    assert len(kube.created) == 1
    assert_plain(kube.created[0][0])
    assert kube.secrets == []
    if sessions and key != KEY and pid == "p-sb":
        assert any("SESSION_KEY_FILE" in m for m in logs)


def test_token_failure_deletes_the_job_blocks_the_ticket_once(tmp_path):
    d, plane, kube, _ = make(tmp_path, dry_run=False)
    kube.deny_secret = "forbidden"
    start(plane, d)
    tid = kube.created[0][0]["metadata"]["name"]
    assert kube.deleted_jobs == [tid]
    assert plane.state_of("p-sb", WID) == "Blocked"
    notes = [w[2] for w in plane.writes if w[0] == "comment" and "session token" in w[2]]
    assert len(notes) == 1 and "Blocked" in notes[0] and "forbidden" in notes[0]
    d.poll()
    d.poll()                                             # no start-and-delete loop
    assert len(kube.created) == 1 and kube.deleted_jobs == [tid]


def test_token_failure_with_a_failed_delete_says_so_and_the_sweep_retries(tmp_path):
    d, plane, kube, _ = make(tmp_path, dry_run=False)
    kube.deny_secret = "forbidden"
    kube.fail_delete_job = "apiserver down"
    start(plane, d)
    tid = kube.created[0][0]["metadata"]["name"]
    note = next(w[2] for w in plane.writes if w[0] == "comment" and "session token" in w[2])
    assert "Deleting the Job failed too" in note
    kube.fail_delete_job = None
    kube.job_list = jobs((tid, True, True), ticket=WID)
    d.poll()                                             # the sweep: still no token, so delete
    assert kube.deleted_jobs == [tid]


def test_token_failure_on_a_dry_run_deletes_nothing(tmp_path):
    d, plane, kube, _ = make(tmp_path)
    kube.deny_secret = "forbidden"
    start(plane, d)
    assert kube.deleted_jobs == []


# --- the reaper ------------------------------------------------------------------------

def jobs(*specs, ticket="x", created_epoch=1_800_000_000.0, deadline=5400):
    return [{"name": n, "ticket": ticket, "playbook": "docs-fix", "active": a, "failed": False,
             "succeeded": not a, "reason": "", "created": "1", "pilot": p, "uid": f"uid-{n}",
             "created_epoch": created_epoch, "deadline": deadline} for n, a, p in specs]


def test_reaper_deletes_a_finished_pilots_token_once(tmp_path):
    d, plane, kube, _ = make(tmp_path)
    kube.job_list = jobs(("ad-sb-1", False, True), ("ad-sb-2", True, True), ("ad-sb-3", False, False))
    d.poll()
    d.poll()
    assert kube.deleted_secrets == ["ad-sb-1-session"]


def test_reaper_retries_a_failed_delete(tmp_path):
    d, plane, kube, _ = make(tmp_path)
    kube.job_list = jobs(("ad-sb-1", False, True))
    kube.fail_delete_secret = "apiserver down"
    d.poll()
    kube.fail_delete_secret = None
    d.poll()
    assert kube.deleted_secrets == ["ad-sb-1-session"]


# --- the token ---------------------------------------------------------------------------

def test_token_known_answer():
    """The vector from images/agent-sessions (homelab#2003) token(), so the two
    can't drift even where that image isn't in the tree."""
    assert jobmod.token(KEY, WID, "ad-sandbox-9-20261122-abc123", 1_900_000_000) == (
        "v1.1900000000.2a66375b698e1f3e3440a7767438dbf1e5834cfe73cfcec49d4e25c85a4a765d")


@pytest.mark.parametrize("wid,job", [("a|b", "c"), (WID, "a|b"), (WID, "Bad_Job"), ("x", "ad-1")])
def test_token_refuses_fields_that_could_collide(wid, job):
    with pytest.raises(ValueError):
        jobmod.token(KEY, wid, job, 1_900_000_000)


def test_max_token_life_matches_the_known_value():
    assert jobmod.MAX_TOKEN_LIFE == 12 * 3600 + 15 * 60


def test_token_matches_agent_sessions():
    path = os.path.join(HERE, "..", "..", "agent-sessions")
    if not os.path.isdir(os.path.join(path, "agent_sessions")):
        pytest.skip("images/agent-sessions isn't in this tree yet (homelab#2003)")
    sys.path.insert(0, path)
    try:
        from agent_sessions import server
    finally:
        sys.path.pop(0)
    for job, exp in (("ad-sandbox-9-20261122-abc123", 1_900_000_000), ("ad-x-1", 1_800_000_123)):
        assert jobmod.token(KEY, WID, job, exp) == server.token(KEY, WID, job, exp)
    assert jobmod.token_expires(5400, now=1000) == server.token_expires(5400, now=1000)
    assert jobmod.TOKEN_GRACE == server.TOKEN_GRACE
    assert jobmod.MAX_TOKEN_LIFE == server.MAX_TOKEN_LIFE


# --- review of #2006 ----------------------------------------------------------------------

def test_an_orphaned_pilot_gets_its_token_recreated_once(tmp_path):
    # agent-desk restarted between creating the Job and its Secret.
    d, plane, kube, logs = make(tmp_path, dry_run=False)
    kube.job_list = jobs(("ad-sb-1", True, True), ticket=WID, created_epoch=1_800_000_000.0)
    d.poll()
    d.poll()
    (secret, dry), = kube.secrets
    assert dry is False and secret["metadata"]["name"] == "ad-sb-1-session"
    assert secret["metadata"]["ownerReferences"][0]["uid"] == "uid-ad-sb-1"
    exp = int(secret["stringData"][jobmod.TOKEN_KEY].split(".")[1])
    assert exp == 1_800_000_000 + 5400 + 900               # from the Job's start, not now
    assert kube.deleted_jobs == []
    assert any("recreated the missing token" in m for m in logs)


def test_a_pilot_whose_token_exists_is_left_alone(tmp_path):
    d, plane, kube, logs = make(tmp_path, dry_run=False)
    kube.existing_secrets = {"ad-sb-1-session"}
    kube.job_list = jobs(("ad-sb-1", True, True), ticket=WID)
    d.poll()
    assert kube.secrets == [] and kube.deleted_jobs == []
    assert not any("recreated" in m for m in logs)


@pytest.mark.parametrize("why", ["deny", "nokey"])
def test_an_orphan_whose_token_cant_be_made_is_deleted(tmp_path, why):
    d, plane, kube, _ = make(tmp_path, dry_run=False, key=None if why == "nokey" else KEY)
    if why == "deny":
        kube.deny_secret = "forbidden"
    kube.job_list = jobs(("ad-sb-1", True, True), ticket=WID)
    d.poll()
    d.poll()
    assert kube.deleted_jobs == ["ad-sb-1"]


def test_the_sweep_skips_dry_runs_plain_jobs_and_finished_ones(tmp_path):
    d, plane, kube, _ = make(tmp_path)                    # dry run
    kube.job_list = jobs(("ad-sb-1", True, True), ticket=WID)
    d.poll()
    (tmp_path / "real").mkdir()
    d2, plane2, kube2, _ = make(tmp_path / "real", dry_run=False)
    kube2.job_list = jobs(("ad-sb-2", True, False), ("ad-sb-3", False, True), ticket=WID)
    d2.poll()
    assert kube.secrets == kube2.secrets == [] and kube.deleted_jobs == kube2.deleted_jobs == []


def test_token_cleanup_runs_even_when_reap_fails(tmp_path, monkeypatch):
    d, plane, kube, _ = make(tmp_path)
    kube.job_list = jobs(("ad-sb-1", False, True))

    def boom(*a):
        raise RuntimeError("plane down")
    monkeypatch.setattr(d, "reap", boom)
    with pytest.raises(RuntimeError):
        d.poll()
    assert kube.deleted_secrets == ["ad-sb-1-session"]


def test_memory_of_deleted_tokens_is_pruned(tmp_path):
    d, plane, kube, _ = make(tmp_path)
    kube.job_list = jobs(("ad-sb-1", False, True))
    d.poll()
    assert d.tokens_deleted == {"ad-sb-1"}
    kube.job_list = []                                    # the Job's TTL removed it
    d.poll()
    assert d.tokens_deleted == set()


def test_a_binary_session_key_loads(tmp_path):
    d, *_ = make(tmp_path, key=bytes(range(256)) + b"\xff" * 8)
    assert len(d.cfg.session_key) >= 32


def test_a_key_with_a_trailing_newline_is_the_same_key(tmp_path):
    d, *_ = make(tmp_path, key=KEY)                       # make() writes KEY + b"\n"
    assert d.cfg.session_key == KEY


def test_a_deadline_too_long_for_a_token_starts_a_plain_job(tmp_path):
    d, plane, kube, logs = make(tmp_path)
    d.cfg.playbooks["docs-fix"].deadline = 13 * 3600
    start(plane, d)
    assert_plain(kube.created[0][0])
    assert kube.secrets == []
    assert sum("past the longest token" in m for m in logs) == 1
