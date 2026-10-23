"""Minimal Plane REST client (API v1, `X-API-Key`).

Copied from images/plane-sync/plane_sync/plane.py (the two images build from
separate contexts), trimmed to what the dispatcher uses, plus reads for one
item, its activity and its comments. Keep fixes to the transport in step.

Behaviour measured on staging Plane 1.4.2 (see the plan's API findings):
- 60 requests/min per key; 429 carries Retry-After. We pace at 1 req/s.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

PER_PAGE = 100


class PlaneError(RuntimeError):
    def __init__(self, status: int, body, method: str, path: str):
        super().__init__(f"{method} {path} -> {status}: {str(body)[:300]}")
        self.status = status
        self.body = body


class Plane:
    def __init__(self, base_url: str, workspace: str, token: str, *, min_interval: float = 1.0,
                 dry_run: bool = False, log=print):
        self.base = f"{base_url.rstrip('/')}/api/v1/workspaces/{workspace}"
        self.token = token
        self.min_interval = min_interval
        self.dry_run = dry_run
        self.log = log
        self._last = 0.0
        self.requests = 0
        self.writes = 0

    # -- transport ------------------------------------------------------------

    def _request(self, method: str, path: str, body=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(6):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            req = urllib.request.Request(url, data=data, method=method, headers={
                "X-API-Key": self.token, "Content-Type": "application/json",
                "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    txt = r.read().decode()
                    return r.status, (json.loads(txt) if txt else None)
            except urllib.error.HTTPError as e:
                txt = e.read().decode()
                try:
                    payload = json.loads(txt)
                except ValueError:
                    payload = txt
                if e.code == 429:
                    time.sleep(int(e.headers.get("Retry-After", "10")) + 1)
                    continue
                if e.code >= 500 and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                return e.code, payload
            except urllib.error.URLError:
                if attempt >= 4:
                    raise
                time.sleep(2 * (attempt + 1))
        raise PlaneError(429, "rate limited repeatedly", method, path)

    def get(self, path: str):
        st, body = self._request("GET", path)
        if st >= 400:
            raise PlaneError(st, body, "GET", path)
        return body

    def write(self, method: str, path: str, body=None, *, ok_conflict: bool = False):
        """POST/PATCH/DELETE. In dry-run, log and return a placeholder."""
        if self.dry_run:
            self.log(f"[dry-run] {method} {path} {json.dumps(body)[:160] if body else ''}")
            return {"id": f"dry-{self.writes}"}
        self.writes += 1
        st, resp = self._request(method, path, body)
        if st == 409 and ok_conflict and isinstance(resp, dict) and resp.get("id"):
            return {"id": resp["id"], "_existing": True}
        if st >= 400:
            raise PlaneError(st, resp, method, path)
        return resp

    def paged(self, path: str):
        sep = "&" if "?" in path else "?"
        cursor = None
        while True:
            q = f"{path}{sep}per_page={PER_PAGE}" + (f"&cursor={cursor}" if cursor else "")
            page = self.get(q)
            if isinstance(page, list):
                yield from page
                return
            yield from page.get("results", [])
            if not page.get("next_page_results"):
                return
            cursor = page.get("next_cursor")

    # -- resources --------------------------------------------------------------

    def projects(self) -> list[dict]:
        return list(self.paged("/projects/"))

    def workspace_members(self) -> list[dict]:
        body = self.get("/members/")
        return body if isinstance(body, list) else body.get("results", [])

    def states(self, pid: str) -> list[dict]:
        return list(self.paged(f"/projects/{pid}/states/"))

    def labels(self, pid: str) -> list[dict]:
        return list(self.paged(f"/projects/{pid}/labels/"))

    def items(self, pid: str) -> list[dict]:
        return list(self.paged(
            f"/projects/{pid}/work-items/?fields=id,name,state,labels,sequence_id,"
            "external_source,created_at,created_by"))

    def item(self, pid: str, iid: str) -> dict:
        return self.get(f"/projects/{pid}/work-items/{iid}/")

    def activities(self, pid: str, iid: str) -> list[dict]:
        return sorted(self.paged(f"/projects/{pid}/work-items/{iid}/activities/"),
                      key=lambda a: a.get("created_at") or "")

    def comments(self, pid: str, iid: str) -> list[dict]:
        return sorted(self.paged(f"/projects/{pid}/work-items/{iid}/comments/"),
                      key=lambda c: c.get("created_at") or "")

    def links(self, pid: str, iid: str) -> list[dict]:
        return list(self.paged(f"/projects/{pid}/work-items/{iid}/links/"))

    def update_item(self, pid: str, iid: str, body: dict) -> dict:
        return self.write("PATCH", f"/projects/{pid}/work-items/{iid}/", body)

    def comment(self, pid: str, iid: str, html: str):
        return self.write("POST", f"/projects/{pid}/work-items/{iid}/comments/",
                          {"comment_html": html})
