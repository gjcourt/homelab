import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check  # noqa: E402


@pytest.fixture(scope="session")
def rules():
    return check.load_rules()


class Repo:
    """A throwaway git repo: `base` on master, then `change()` on a branch."""

    def __init__(self, path):
        self.path = str(path)
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        return subprocess.run(["git", "-C", self.path, *args], check=True, capture_output=True, text=True).stdout

    def write(self, files):
        for name, content in files.items():
            full = os.path.join(self.path, name)
            if content is None:
                os.remove(full)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            mode = "wb" if isinstance(content, bytes) else "w"
            with open(full, mode) as f:
                f.write(content)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def base(self, files):
        self.write(files)
        return self.commit("base")

    def change(self, files):
        self.git("checkout", "-q", "-B", "pr", "master")
        self.write(files)
        return self.commit("change")

    def check(self, rules):
        return check.check_range(self.path, "master", "pr", rules)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def run(repo, rules, base, change):
    """Violations for changing `base` into `change` (dicts of path -> text)."""
    repo.base(base or {"README.md": "x\n"})
    repo.change(change)
    return repo.check(rules)


def rule_names(violations):
    return sorted({v.rule for v in violations})
