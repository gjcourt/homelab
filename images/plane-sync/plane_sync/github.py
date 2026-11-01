"""GitHub REST/GraphQL and git plumbing for plane-sync.

The bot never pushes to a default branch and never merges: it opens (or adds
to) one status-sync PR on gjcourt/lab and enables GitHub auto-merge, which
merges only after the repo's required checks pass.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"


class GitHub:
    def __init__(self, token: str | None):
        self.token = token

    def _req(self, method: str, url: str, body=None):
        if not url.startswith("http"):
            url = API + url
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read().decode()
                return json.loads(txt) if txt else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"GitHub {method} {url} -> {e.code}: {e.read().decode()[:300]}") from None

    def _paged(self, path: str):
        page = 1
        while True:
            sep = "&" if "?" in path else "?"
            batch = self._req("GET", f"{path}{sep}per_page=100&page={page}")
            if not batch:
                return
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    def default_branch(self, owner: str, repo: str) -> str | None:
        try:
            return self._req("GET", f"/repos/{owner}/{repo}").get("default_branch")
        except RuntimeError:
            return None

    def open_pulls(self, owner: str, repo: str):
        return list(self._paged(f"/repos/{owner}/{repo}/pulls?state=open"))

    def pull_files(self, owner: str, repo: str, number: int):
        return list(self._paged(f"/repos/{owner}/{repo}/pulls/{number}/files"))

    def file_at(self, owner: str, repo: str, path: str, ref: str) -> str:
        body = self._req("GET", f"/repos/{owner}/{repo}/contents/{path}?ref={ref}")
        return base64.b64decode(body["content"]).decode("utf-8")

    def pull_for_branch(self, owner: str, repo: str, branch: str):
        pulls = self._req("GET", f"/repos/{owner}/{repo}/pulls?state=open&head={owner}:{branch}")
        return pulls[0] if pulls else None

    def create_pull(self, owner: str, repo: str, head: str, base: str, title: str, body: str):
        return self._req("POST", f"/repos/{owner}/{repo}/pulls",
                         {"head": head, "base": base, "title": title, "body": body})

    def update_pull_body(self, owner: str, repo: str, number: int, body: str):
        return self._req("PATCH", f"/repos/{owner}/{repo}/pulls/{number}", {"body": body})

    def enable_auto_merge(self, node_id: str):
        q = ("mutation($id:ID!){enablePullRequestAutoMerge(input:{pullRequestId:$id,"
             "mergeMethod:SQUASH}){pullRequest{number autoMergeRequest{enabledAt}}}}")
        return self._req("POST", f"{API}/graphql", {"query": q, "variables": {"id": node_id}})


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        # Never echo a URL that may carry a token.
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def clone(owner: str, repo: str, dest: Path, token: str | None, branch: str | None = None) -> Path:
    url = (f"https://x-access-token:{token}@github.com/{owner}/{repo}.git" if token
           else f"https://github.com/{owner}/{repo}.git")
    args = ["clone", "--quiet", "--depth", "50"]
    if branch:
        args += ["--branch", branch]
    git(*args, url, str(dest))
    return dest
