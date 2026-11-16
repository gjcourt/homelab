"""The bench-cloud pre-push hook (images/bench-cloud/git-hooks/pre-push).

Real `git push` runs to a local bare origin, with core.hooksPath pointed at the
hook the way bench-task sets it (plan step 3, "Defence in depth in the Job").
"""

import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(GATE))
HOOKS = os.path.join(ROOT, "images", "bench-cloud", "git-hooks")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def git(path, *args, env=None, check=True):
    return subprocess.run(["git", "-C", str(path), *args], check=check, capture_output=True,
                          text=True, env=env)


@pytest.fixture
def clone(tmp_path):
    """A clone of a bare origin whose master carries homelab's rules.yaml."""
    seed, origin, work = tmp_path / "seed", tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "master", str(origin))
    git(tmp_path, "init", "-q", "-b", "master", str(seed))
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(seed, "config", k, v)
    os.makedirs(seed / "scripts" / "bench-gate")
    shutil.copy(os.path.join(GATE, "rules.yaml"), seed / "scripts" / "bench-gate" / "rules.yaml")
    (seed / "README.md").write_text("x\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "base")
    git(seed, "push", "-q", str(origin), "master")
    git(tmp_path, "clone", "-q", str(origin), str(work))
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(work, "config", k, v)
    # What bench-task does after the clone.
    rules = tmp_path / "home" / ".bench-gate" / "rules.yaml"
    os.makedirs(rules.parent)
    rules.write_text(git(work, "show", "origin/master:scripts/bench-gate/rules.yaml").stdout)
    git(work, "config", "core.hooksPath", HOOKS)
    env = dict(os.environ, BENCH_GATE_RULES=str(rules),
               BENCH_GATE_CHECK=os.path.join(GATE, "check.py"), BENCH_GATE_PYTHON=sys.executable)
    return work, env, rules


def push_change(work, env, files, branch="docs/x"):
    git(work, "checkout", "-q", "-B", branch, "origin/master")
    for name, text in files.items():
        p = work / name
        os.makedirs(p.parent, exist_ok=True)
        p.write_text(text)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "change")
    return git(work, "push", "-q", "origin", branch, env=env, check=False)


def test_allowed_change_pushes(clone):
    work, env, _ = clone
    r = push_change(work, env, {"docs/x.md": "hello\n"})
    assert r.returncode == 0, r.stderr
    assert git(work, "ls-remote", "origin", "docs/x").stdout.strip()


def test_denied_path_is_refused_with_the_rule_named(clone):
    work, env, _ = clone
    r = push_change(work, env, {"infra/x.yaml": "a: 1\n"}, branch="fix/infra")
    assert r.returncode != 0
    assert "infra/x.yaml: [path]" in r.stderr
    assert "push refused" in r.stderr
    assert git(work, "ls-remote", "origin", "fix/infra").stdout.strip() == ""


def test_denied_content_is_refused(clone):
    work, env, _ = clone
    r = push_change(work, env, {"apps/staging/x/ns.yaml": "apiVersion: v1\nkind: Namespace\n"})
    assert r.returncode != 0
    assert "[kind]" in r.stderr


def test_deleting_a_branch_is_not_checked(clone):
    work, env, _ = clone
    assert push_change(work, env, {"docs/x.md": "hello\n"}).returncode == 0
    r = git(work, "push", "-q", "origin", "--delete", "docs/x", env=env, check=False)
    assert r.returncode == 0, r.stderr


def test_missing_rules_fail_closed(clone):
    work, env, rules = clone
    os.remove(rules)
    r = push_change(work, env, {"docs/x.md": "hello\n"})
    assert r.returncode != 0
    assert "no rules" in r.stderr
