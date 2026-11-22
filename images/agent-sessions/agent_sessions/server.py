"""The session service's HTTP side (agent sessions plan, "Where the events go").

Step 2 is the skeleton: it takes a Job's events, checks the Job's token,
validates and re-redacts them, and writes one JSON line per event to its log.
Nothing is stored and nothing goes to a transport yet (step 3A).

  POST /sessions/<work item id>/events   {"job": "<Job name>", "events": [...]}
       Authorization: Bearer <HMAC-SHA256(session-key, "<work item id>|<Job name>"), hex>
  GET  /sessions/<work item id>/messages?job=<Job name>   (same token)
       George's messages for poll(); always empty until step 3A
  GET  /healthz

Event shapes (the plan's "Job side: session events" table, plus `type`):

  {"type": "progress", "tool": str, "summary": str, "at": str}
  {"type": "plan", "items": [{"content": str, "status": str}], "at": str}
  {"type": "say", "text": str, "at": str}
  {"type": "ask", "text": str, "at": str}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import redact as rd

MAX_BODY = 256 * 1024
MAX_EVENTS = 100
WORK_ITEM = re.compile(r"^[0-9a-fA-F-]{8,64}$")   # Plane work-item ids are UUIDs
JOB = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")  # a Kubernetes Job name
EVENTS_PATH = re.compile(r"^/sessions/([^/]+)/events$")
MESSAGES_PATH = re.compile(r"^/sessions/([^/]+)/messages$")
# field -> (type, max length); every event also has "type" and "at".
SHAPES = {
    "progress": {"tool": (str, 100), "summary": (str, 200)},
    "plan": {"items": (list, 100)},
    "say": {"text": (str, 20000)},
    "ask": {"text": (str, 20000)},
}
ITEM = {"content": (str, 500), "status": (str, 32)}


def token(key: bytes, work_item: str, job: str) -> str:
    """The plan's per-Job credential: HMAC(session-key, work_item_id | job_name)."""
    return hmac.new(key, f"{work_item}|{job}".encode(), hashlib.sha256).hexdigest()


def token_ok(key: bytes, work_item: str, job: str, header: str | None) -> bool:
    if not key or not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(token(key, work_item, job), header[len("Bearer "):].strip())


class Invalid(ValueError):
    pass


def _field(value, kind, limit, name):
    if not isinstance(value, kind):
        raise Invalid(f"{name} must be a {kind.__name__}")
    if len(value) > limit:
        raise Invalid(f"{name} is longer than {limit}")
    return value


def clean(event) -> tuple[dict, list[str]]:
    """One event in its plan shape, redacted, plus the fields redaction changed.

    Unknown fields are dropped; a wrong type or an unknown event type is refused.
    """
    if not isinstance(event, dict) or event.get("type") not in SHAPES:
        raise Invalid("each event needs a type: " + ", ".join(SHAPES))
    out = {"type": event["type"], "at": _field(event.get("at", ""), str, 40, "at")}
    changed = []

    def scrub(s, name):
        r = rd.redact(s)
        if r != s:
            changed.append(name)
        return r

    for name, (kind, limit) in SHAPES[event["type"]].items():
        value = _field(event.get(name), kind, limit, name)
        if name == "items":
            items = []
            for i, item in enumerate(value):
                if not isinstance(item, dict):
                    raise Invalid("plan items are objects")
                items.append({k: scrub(_field(item.get(k, ""), t, n, f"items[{i}].{k}"), f"items[{i}].{k}")
                              for k, (t, n) in ITEM.items()})
            out[name] = items
        else:
            out[name] = scrub(value, name)
    return out, changed


def make_handler(key: bytes, emit, health=lambda: True):
    """emit(dict) writes one log record."""

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: dict | None = None):
            data = json.dumps(body or {}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _refuse(self, code, why, **ctx):
            emit({"refused": code, "why": why, "path": urlsplit(self.path).path[:120], **ctx})
            self._reply(code, {"error": why})

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/healthz":
                ok = health()
                self._reply(200 if ok else 503, {"ok": ok})
                return
            m = MESSAGES_PATH.match(url.path)
            if not m:
                self._reply(404, {"error": "not found"})
                return
            wid, job = m[1], (parse_qs(url.query).get("job") or [""])[0]
            if not WORK_ITEM.match(wid) or not JOB.match(job):
                self._refuse(400, "bad work item or job")
                return
            if not token_ok(key, wid, job, self.headers.get("Authorization")):
                self._refuse(401, "bad token", session=wid, job=job)
                return
            # Step 3A gives this a store; until then there is nothing to relay.
            self._reply(200, {"messages": []})

        def do_POST(self):
            m = EVENTS_PATH.match(urlsplit(self.path).path)
            if not m:
                self._reply(404, {"error": "not found"})
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self._refuse(413, "body too large")
                return
            wid = m[1]
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                self._refuse(400, "body is not JSON")
                return
            job = body.get("job") if isinstance(body, dict) else None
            if not WORK_ITEM.match(wid) or not isinstance(job, str) or not JOB.match(job):
                self._refuse(400, "bad work item or job")
                return
            # The token before anything else is looked at: an unauthenticated
            # caller learns nothing about what a valid body would be.
            if not token_ok(key, wid, job, self.headers.get("Authorization")):
                self._refuse(401, "bad token", session=wid, job=job)
                return
            events = body.get("events")
            if not isinstance(events, list) or not events or len(events) > MAX_EVENTS:
                self._refuse(400, f"events must be a list of 1 to {MAX_EVENTS}", session=wid, job=job)
                return
            try:
                cleaned = [clean(e) for e in events]
            except Invalid as e:
                self._refuse(400, str(e), session=wid, job=job)
                return
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for event, changed in cleaned:
                if changed:
                    # The Job's redaction missed something: worth knowing, never the value.
                    emit({"warning": "redacted on arrival", "session": wid, "job": job,
                          "type": event["type"], "fields": changed})
                emit({"logged": now, "session": wid, "job": job, **event})
            self._reply(202, {"accepted": len(cleaned)})

        def log_message(self, *a):
            pass

    return Handler


def serve(port: int, key: bytes, emit) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("", port), make_handler(key, emit))
