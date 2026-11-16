"""Unit tests for gate.py's decisions: what is a bench PR, and the approval rule."""

import gate

REPO = "gjcourt/homelab"
HEAD = "a" * 40
OLD = "b" * 40


def pr(author_id=gate.GEORGE_ID, head_repo=REPO):
    return {"user": {"id": author_id}, "head": {"ref": "feat/x", "repo": {"full_name": head_repo} if head_repo else None}}


def review(state, commit=HEAD, user=gate.GEORGE_ID, at="2026-11-01T10:00:00Z", rid=1):
    return {"id": rid, "user": {"id": user}, "state": state, "commit_id": commit, "submitted_at": at}


# --- is_bench_pr ----------------------------------------------------------


def test_bot_author_is_bench():
    assert gate.is_bench_pr(pr(author_id=gate.BOT_ID), [], REPO)[0]


def test_george_pr_without_bot_pushes_is_not_bench():
    activity = [{"actor": {"id": gate.GEORGE_ID}, "activity_type": "push"}]
    assert gate.is_bench_pr(pr(), activity, REPO) == (False, "not a bench PR")


def test_renovate_style_pr_is_not_bench():
    # Renovate pushes with George's token.
    assert not gate.is_bench_pr(pr(), [{"actor": {"id": gate.GEORGE_ID}, "activity_type": "force_push"}], REPO)[0]


def test_bot_push_to_george_branch_is_bench():
    activity = [{"actor": {"id": gate.GEORGE_ID}, "activity_type": "branch_creation"}, {"actor": {"id": gate.BOT_ID}, "activity_type": "push"}]
    bench, why = gate.is_bench_pr(pr(), activity, REPO)
    assert bench and "pushed" in why


def test_fork_head_fails_closed():
    assert gate.is_bench_pr(pr(head_repo="someone/homelab"), [], REPO)[0]


def test_deleted_head_repo_fails_closed():
    assert gate.is_bench_pr(pr(head_repo=None), [], REPO)[0]


def test_commit_author_fields_are_not_used():
    # A bot-authored-looking commit pushed by George doesn't matter; only the
    # pusher in the activity API counts.
    activity = [{"actor": {"id": gate.GEORGE_ID, "login": "bench-cloud[bot]"}}]
    assert not gate.is_bench_pr(pr(), activity, REPO)[0]


# --- approval_state -------------------------------------------------------


def test_no_reviews():
    assert gate.approval_state([], HEAD) == (False, "waiting for gjcourt to approve")


def test_approved_at_head():
    assert gate.approval_state([review("APPROVED")], HEAD)[0]


def test_approved_at_old_sha():
    ok, why = gate.approval_state([review("APPROVED", commit=OLD)], HEAD)
    assert not ok and "not the head" in why


def test_comment_after_approval_keeps_it():
    rs = [review("APPROVED", rid=1), review("COMMENTED", at="2026-11-01T11:00:00Z", rid=2)]
    assert gate.approval_state(rs, HEAD)[0]


def test_comment_only_is_not_approval():
    assert not gate.approval_state([review("COMMENTED")], HEAD)[0]


def test_changes_requested_after_approval():
    rs = [review("APPROVED", rid=1), review("CHANGES_REQUESTED", at="2026-11-01T11:00:00Z", rid=2)]
    ok, why = gate.approval_state(rs, HEAD)
    assert not ok and "changes requested" in why


def test_approval_after_changes_requested():
    rs = [review("CHANGES_REQUESTED", rid=1), review("APPROVED", at="2026-11-01T11:00:00Z", rid=2)]
    assert gate.approval_state(rs, HEAD)[0]


def test_dismissed_approval():
    assert not gate.approval_state([review("DISMISSED")], HEAD)[0]


def test_other_users_approval_does_not_count():
    assert not gate.approval_state([review("APPROVED", user=gate.BOT_ID), review("APPROVED", user=12345)], HEAD)[0]


def test_order_is_by_time_not_list_position():
    rs = [review("APPROVED", at="2026-11-01T12:00:00Z", rid=2), review("CHANGES_REQUESTED", at="2026-11-01T11:00:00Z", rid=1)]
    assert gate.approval_state(rs, HEAD)[0]


def test_pending_review_is_ignored():
    rs = [review("APPROVED", rid=1), {"id": 2, "user": {"id": gate.GEORGE_ID}, "state": "PENDING", "commit_id": HEAD, "submitted_at": None}]
    assert gate.approval_state(rs, HEAD)[0]


# --- descriptions ---------------------------------------------------------


def test_paths_description():
    import check

    assert gate.paths_description([]) == "all changed paths are allowed"
    vs = [check.Violation("infra/x", "path", "d"), check.Violation("b", "kind", "d")]
    assert gate.paths_description(vs) == "infra/x: path (+1 more)"


# --- fetch_head: private repos ---------------------------------------------


def test_git_auth_env_without_token_adds_nothing(monkeypatch):
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    env = gate.git_auth_env(None)
    assert "GIT_CONFIG_COUNT" not in env


def test_git_auth_env_appends_after_existing_config(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    env = gate.git_auth_env("tok")
    assert env["GIT_CONFIG_COUNT"] == "3"
    assert env["GIT_CONFIG_KEY_2"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_2"] == "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46dG9r"  # x-access-token:tok


def test_fetch_head_keeps_the_token_out_of_argv(monkeypatch):
    seen = {}

    class R:
        returncode = 0
        stderr = ""

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return R()

    monkeypatch.setenv("GH_TOKEN", "s3cret")
    monkeypatch.setattr(gate.subprocess, "run", run)
    monkeypatch.setattr(gate.check, "git", lambda *a: b"abc\n")
    assert gate.fetch_head(".", 5) == "abc"
    assert not any("s3cret" in a or "basic" in a for a in seen["cmd"])
    assert "AUTHORIZATION: basic" in seen["env"][f"GIT_CONFIG_VALUE_{int(seen['env']['GIT_CONFIG_COUNT']) - 1}"]


def test_fetch_head_reports_gits_message(monkeypatch):
    class R:
        returncode = 128
        stderr = "fatal: could not read Username for 'https://github.com'\n"

    monkeypatch.setattr(gate.subprocess, "run", lambda cmd, **kw: R())
    import pytest

    with pytest.raises(RuntimeError, match="could not read Username"):
        gate.fetch_head(".", 5)


def test_fetch_head_against_a_private_style_remote(tmp_path, monkeypatch):
    # End to end with a real git: a local "origin" with a refs/pull/N/head ref.
    import subprocess as sp

    def g(*a, cwd):
        return sp.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    origin, work = tmp_path / "origin", tmp_path / "work"
    origin.mkdir()
    g("init", "-q", "-b", "main", cwd=origin)
    g("-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x", cwd=origin)
    sha = g("rev-parse", "HEAD", cwd=origin)
    g("update-ref", "refs/pull/9/head", sha, cwd=origin)
    g("clone", "-q", str(origin), str(work), cwd=tmp_path)
    monkeypatch.setenv("GH_TOKEN", "tok")  # harmless for a file:// remote
    assert gate.fetch_head(str(work), 9) == sha
