"""Read-only GitHub: is a linked PR open, merged or closed?

The dispatcher's PAT has Pull requests: read only (plan, "Dispatcher"). It
never writes; Jobs keep bench-cloud's own GitHub App.
"""

from __future__ import annotations

import json
import urllib.request


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def pr_state(self, repo: str, number: int) -> str:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/pulls/{number}",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(req, timeout=30) as r:
            pr = json.load(r)
        if pr.get("merged"):
            return "merged"
        return pr.get("state", "open")
