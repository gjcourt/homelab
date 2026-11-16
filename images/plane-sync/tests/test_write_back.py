"""write_back starts a fresh sync branch even when a merged PR left the old one behind."""

import subprocess
from pathlib import Path

from plane_sync import cli
from plane_sync import github as ghmod


def run(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


class FakeGH:
    def __init__(self):
        self.created = []

    def pull_for_branch(self, owner, repo, branch):
        return None  # the old sync PR was merged, so none is open

    def create_pull(self, owner, repo, head, base, title, body):
        self.created.append(head)
        return {"number": 1, "node_id": "x", "html_url": "u"}

    def update_pull_body(self, *a):
        raise AssertionError("no open PR to update")

    def enable_auto_merge(self, node_id):
        return {}


class FakeSyncer:
    class report:
        brief_writes = {"a.md": "Done"}
        conflicts = []

    def apply_brief_writes(self, root):
        p = Path(root) / "a.md"
        p.write_text("status: Done\n")
        return [p]


def test_stale_remote_branch_is_replaced(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    run("init", "--quiet", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    seed = tmp_path / "seed"
    run("clone", "--quiet", str(remote), str(seed), cwd=tmp_path)
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        run("config", k, v, cwd=seed)
    (seed / "a.md").write_text("status: Not Started\n")
    run("add", "-A", cwd=seed)
    run("commit", "--quiet", "-m", "init", cwd=seed)
    run("push", "--quiet", "origin", "main", cwd=seed)
    # A leftover sync branch with a commit main doesn't have.
    run("checkout", "--quiet", "-b", cli.SYNC_BRANCH, cwd=seed)
    (seed / "old.md").write_text("old\n")
    run("add", "-A", cwd=seed)
    run("commit", "--quiet", "-m", "old sync", cwd=seed)
    run("push", "--quiet", "origin", cli.SYNC_BRANCH, cwd=seed)

    def fake_clone(owner, repo, dest, token, branch=None):
        run("clone", "--quiet", str(remote), str(dest), cwd=tmp_path)
        return dest

    monkeypatch.setattr(ghmod, "clone", fake_clone)
    cfg = type("C", (), {"lab_owner": "o", "lab_repo": "r", "lab_root": None})()
    gh = FakeGH()
    cli.write_back(FakeSyncer(), cfg, gh, None, tmp_path / "work", git_dry_run=False)

    files = subprocess.run(["git", "ls-tree", "--name-only", cli.SYNC_BRANCH],
                           cwd=remote, check=True, capture_output=True, text=True).stdout.split()
    assert files == ["a.md"]  # rebuilt from main: the stale commit is gone
    assert gh.created == [cli.SYNC_BRANCH]
