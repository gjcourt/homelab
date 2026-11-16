"""Unit tests for staging_ready.py: when staging-deploy may merge a PR.

The race these close (step 1 review): staging-deploy runs after Lint and Flux
tests, and a bench PR whose bench-paths had not been posted yet had nothing
failing or pending, so it went into staging unchecked.
"""

import subprocess

import gate
import pytest
import staging_ready as sr

REPO = "gjcourt/homelab"
HEAD = "c" * 40


def chk(name, bucket="pass"):
    return {"name": name, "state": bucket.upper(), "bucket": bucket}


def status(state="success", creator_id=sr.ACTIONS_ID, login="github-actions[bot]", at="2026-11-01T10:00:00Z", sid=1, context=gate.PATHS):
    return {"id": sid, "context": context, "state": state, "created_at": at, "creator": {"id": creator_id, "login": login}}


GREEN = [chk("yaml-lint"), chk("validate-kustomize")]


# --- decide ---------------------------------------------------------------


def test_bench_pr_without_bench_paths_is_not_ready():
    # The race: Lint and Flux tests are green, bench-paths not posted yet.
    ok, why = sr.decide(GREEN, bench=True, paths_status=None)
    assert not ok and "not been posted" in why


def test_bench_pr_with_bench_paths_success_is_ready():
    assert sr.decide(GREEN + [chk(gate.PATHS)], bench=True, paths_status=status())[0]


def test_bench_pr_with_bench_paths_failure_is_not_ready():
    ok, why = sr.decide(GREEN + [chk(gate.PATHS, "fail")], bench=True, paths_status=status("failure"))
    assert not ok and gate.PATHS in why


@pytest.mark.parametrize("state", ["failure", "error", "pending"])
def test_bench_paths_must_be_success_even_if_checks_list_lags(state):
    # gh pr checks hasn't caught up, but the SHA-pinned status says no.
    ok, why = sr.decide(GREEN, bench=True, paths_status=status(state))
    assert not ok and state in why


def test_bench_paths_from_someone_else_does_not_count():
    ok, why = sr.decide(GREEN, bench=True, paths_status=status(creator_id=356410, login="gjcourt"))
    assert not ok and "not GitHub Actions" in why


def test_bench_paths_with_no_creator_does_not_count():
    s = status()
    s.pop("creator")
    assert not sr.decide(GREEN, bench=True, paths_status=s)[0]


def test_unapproved_bench_pr_still_previews():
    # Decision 2: bench-approval failure is ignored for staging.
    checks = GREEN + [chk(gate.PATHS), chk(gate.APPROVAL, "fail")]
    assert sr.decide(checks, bench=True, paths_status=status())[0]


def test_own_job_pending_is_ignored():
    assert sr.decide(GREEN + [chk("sync-staging", "pending")], bench=False, paths_status=None)[0]


def test_non_bench_pr_without_bench_paths_is_ready():
    # George's and Renovate's PRs don't wait for the gate.
    assert sr.decide(GREEN, bench=False, paths_status=None)[0]


def test_non_bench_pr_with_no_checks_is_ready():
    # Unchanged from before: a PR with no checks previews.
    assert sr.decide([], bench=False, paths_status=None)[0]


def test_bench_pr_with_no_checks_needs_bench_paths():
    assert not sr.decide([], bench=True, paths_status=None)[0]


def test_failing_check_blocks_everyone():
    for bench in (False, True):
        ok, why = sr.decide(GREEN + [chk("flux-test", "fail")], bench=bench, paths_status=status())
        assert not ok and "flux-test" in why


def test_pending_check_blocks_everyone():
    for bench in (False, True):
        ok, why = sr.decide(GREEN + [chk("flux-test", "pending")], bench=bench, paths_status=status())
        assert not ok and "pending" in why


def test_bench_paths_pending_in_checks_blocks():
    ok, why = sr.decide(GREEN + [chk(gate.PATHS, "pending")], bench=True, paths_status=status())
    assert not ok and gate.PATHS in why


# --- latest_status --------------------------------------------------------


def test_latest_status_none():
    assert sr.latest_status([status(context="other")], gate.PATHS) is None


def test_latest_status_is_by_time_not_list_order():
    old_ok = status("success", at="2026-11-01T10:00:00Z", sid=1)
    new_fail = status("failure", at="2026-11-01T11:00:00Z", sid=2)
    assert sr.latest_status([old_ok, new_fail], gate.PATHS)["state"] == "failure"
    assert sr.latest_status([new_fail, old_ok], gate.PATHS)["state"] == "failure"


def test_latest_status_ignores_other_contexts():
    s = [status("failure", context=gate.APPROVAL, sid=9, at="2026-11-01T12:00:00Z"), status("success", sid=1)]
    assert sr.latest_status(s, gate.PATHS)["state"] == "success"


# --- judge / main, with the GitHub calls faked ----------------------------


def fake_pr(author_id=gate.GEORGE_ID, head_repo=REPO):
    return {"user": {"id": author_id}, "head": {"sha": HEAD, "ref": "feat/x", "repo": {"full_name": head_repo}}}


@pytest.fixture
def github(monkeypatch):
    """Fake gate.gh_api and pr_checks. Set .pr, .activity, .statuses, .checks;
    set .fail to a path prefix to make that call raise."""

    class GH:
        pr = fake_pr()
        activity: list = []
        statuses: list = []
        checks = GREEN
        fail = None
        calls: list = []

    gh = GH()
    gh.calls = []

    def gh_api(path, method="GET", fields=None, paginate=False):
        gh.calls.append(path)
        assert method == "GET"
        if gh.fail and path.startswith(gh.fail):
            raise subprocess.CalledProcessError(1, ["gh", "api", path])
        if "/activity" in path:
            return gh.activity
        if "/statuses" in path:
            return gh.statuses
        if "/pulls/" in path:
            return gh.pr
        raise AssertionError(path)

    monkeypatch.setattr(gate, "gh_api", gh_api)
    monkeypatch.setattr(sr, "pr_checks", lambda repo, n: gh.checks)
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    return gh


def test_judge_bot_pr_before_gate_ran(github):
    github.pr = fake_pr(author_id=gate.BOT_ID)
    sha, ok, why = sr.judge(REPO, 7)
    assert sha == HEAD and not ok and "not been posted" in why
    assert f"repos/{REPO}/commits/{HEAD}/statuses?per_page=100" in github.calls


def test_judge_bot_pr_after_gate_ran(github):
    github.pr = fake_pr(author_id=gate.BOT_ID)
    github.statuses = [status()]
    assert sr.judge(REPO, 7) == (HEAD, True, f"2 checks passed, {gate.PATHS} success")


def test_judge_bot_pushed_to_george_branch(github):
    # Same rule as gate.py: the pusher in the activity API counts.
    github.activity = [{"actor": {"id": gate.BOT_ID}, "activity_type": "push"}]
    assert not sr.judge(REPO, 7)[1]
    github.statuses = [status()]
    assert sr.judge(REPO, 7)[1]


def test_judge_george_pr_does_not_read_statuses(github):
    sha, ok, _ = sr.judge(REPO, 7)
    assert ok and not any("/statuses" in c for c in github.calls)


def test_judge_fork_head_is_bench(github):
    github.pr = fake_pr(head_repo="someone/homelab")
    assert not sr.judge(REPO, 7)[1]
    assert not any("/activity" in c for c in github.calls)


def test_judge_activity_error_fails_closed(github):
    github.fail = f"repos/{REPO}/activity"
    assert not sr.judge(REPO, 7)[1]  # treated as bench, no bench-paths
    github.statuses = [status()]
    assert sr.judge(REPO, 7)[1]  # the gate's "not a bench PR" success lets it in


def test_judge_status_error_is_not_ready(github):
    github.pr = fake_pr(author_id=gate.BOT_ID)
    github.fail = f"repos/{REPO}/commits/"
    sha, ok, why = sr.judge(REPO, 7)
    assert not ok and "could not read the statuses" in why


def test_judge_pr_error_is_not_ready(github):
    github.fail = f"repos/{REPO}/pulls/"
    sha, ok, why = sr.judge(REPO, 7)
    assert sha is None and not ok and "could not read the PR" in why


def test_judge_retries_transient_errors(github, monkeypatch):
    real = gate.gh_api
    left = {"n": 2}

    def flaky(path, **kw):
        if left["n"]:
            left["n"] -= 1
            raise subprocess.CalledProcessError(1, ["gh"])
        return real(path, **kw)

    monkeypatch.setattr(gate, "gh_api", flaky)
    assert sr.judge(REPO, 7)[1]


def test_main_prints_only_the_sha_when_ready(github, capsys):
    assert sr.main(["--pr", "7"]) == 0
    out = capsys.readouterr()
    assert out.out == HEAD + "\n"
    assert "ready" in out.err


def test_main_prints_nothing_when_not_ready(github, capsys):
    github.pr = fake_pr(author_id=gate.BOT_ID)
    assert sr.main(["--pr", "7"]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "not ready" in out.err


# --- pr_checks ------------------------------------------------------------


def test_pr_checks_ignores_exit_code_and_bad_output(monkeypatch):
    class R:
        def __init__(self, out):
            self.stdout = out

    for out, want in [("", []), ("not json", []), ('{"a": 1}', []), ('[{"name": "x", "bucket": "fail"}]', [{"name": "x", "bucket": "fail"}])]:
        monkeypatch.setattr(sr.subprocess, "run", lambda *a, _o=out, **k: R(_o))
        assert sr.pr_checks(REPO, 1) == want
