"""Read-only GitHub: is a linked PR open, merged or closed, and has George
asked for changes on it?

The dispatcher's PAT has Pull requests: read only (plan, "Dispatcher"). It
never writes; Jobs keep bench-cloud's own GitHub App.
"""

from __future__ import annotations

import json
import urllib.request

# Review states that set a PR's review decision; COMMENTED and PENDING don't.
DECIDING = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def _get(self, path: str):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{path}",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def pr_state(self, repo: str, number: int) -> str:
        pr = self._get(f"{repo}/pulls/{number}")
        if pr.get("merged"):
            return "merged"
        return pr.get("state", "open")

    def changes_requested(self, repo: str, number: int, login: str) -> dict | None:
        """`login`'s review when his latest deciding review asks for changes:
        {"id", "body", "comments": [{"path", "line", "body"}]}; else None."""
        reviews = self._get(f"{repo}/pulls/{number}/reviews?per_page=100")
        mine = [r for r in reviews if (r.get("user") or {}).get("login", "").lower()
                == login.lower() and r.get("state") in DECIDING]
        if not mine or mine[-1]["state"] != "CHANGES_REQUESTED":
            return None
        r = mine[-1]
        inline = self._get(f"{repo}/pulls/{number}/reviews/{r['id']}/comments?per_page=100")
        return {"id": r["id"], "body": r.get("body") or "",
                "comments": [{"path": c.get("path", ""),
                              "line": c.get("line") or c.get("original_line"),
                              "body": c.get("body") or ""} for c in inline]}
