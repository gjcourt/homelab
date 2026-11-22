"""The session service's HTTP side (agent sessions plan, "Where the events go").

Step 2 is the skeleton: it takes a Job's events, checks the Job's token,
validates and re-redacts them, and writes one JSON line per event to its log.
Nothing is stored and nothing goes to a transport yet (step 3A).

  POST /sessions/<work item id>/events   {"job": "<Job name>", "events": [...]}
       Authorization: Bearer v1.<expires>.<HMAC-SHA256(session-key,
                             "<work item id>|<Job name>|<expires>"), hex>
       (expires: Unix seconds, the Job's deadline + 15 minutes; George's D2)
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
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import redact as rd

MAX_BODY = 256 * 1024
MAX_EVENTS = 100
MIN_KEY = 32           # bytes; a shorter session-key is refused at startup
SOCKET_TIMEOUT = 10    # seconds a client may take to send its request
MAX_THREADS = 32       # requests handled at once; more wait to be accepted
AT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$")  # ISO-8601 UTC
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


# No leading zeros, so one expiry has one spelling; [0-9], not \d, which
# would match other Unicode digits.
TOKEN = re.compile(r"^v1\.([1-9][0-9]{0,11})\.([0-9a-f]{64})$")
# George, 2026-11-22 (D2): a token "expires on its own at its playbook deadline
# plus 15 minutes, besides being deleted when the Job ends".
TOKEN_GRACE = 15 * 60
# The longest a token may live, from now: the longest playbook deadline (3h)
# with room, plus the grace. A token signed further out is refused as a mint
# mistake.
MAX_TOKEN_LIFE = 12 * 3600 + TOKEN_GRACE


def token(key: bytes, work_item: str, job: str, expires: int) -> str:
    """The per-Job credential: v1.<expires>.HMAC(session-key, work_item | job | expires).

    The fields are joined with "|", so each must be one that can't hold one:
    otherwise token(k, "a|b", "c", e) == token(k, "a", "b|c", e). Raises
    ValueError for anything else."""
    if not WORK_ITEM.match(work_item) or not JOB.match(job):
        raise ValueError("token: bad work item id or Job name")
    sig = hmac.new(key, f"{work_item}|{job}|{int(expires)}".encode(), hashlib.sha256).hexdigest()
    return f"v1.{int(expires)}.{sig}"


def token_expires(deadline_seconds: int, now: float | None = None) -> int:
    """When a token for a Job with this activeDeadlineSeconds expires (D2)."""
    return int((time.time() if now is None else now) + deadline_seconds + TOKEN_GRACE)


def token_problem(key: bytes, work_item: str, job: str, header: str | None,
                  now: float | None = None) -> str:
    """Why this Authorization header doesn't let this Job write this session;
    "" if it does. The signature is checked before the expiry, so the reason
    never tells an unauthenticated caller anything."""
    if not key or not header or not header.startswith("Bearer "):
        return "bad token"
    try:
        got = header[len("Bearer "):].strip()
        got.encode("ascii")
    except UnicodeError:
        return "bad token"
    m = TOKEN.match(got)
    if not m or not WORK_ITEM.match(work_item) or not JOB.match(job):
        return "bad token"
    if not hmac.compare_digest(token(key, work_item, job, int(m[1])).encode(), got.encode()):
        return "bad token"
    now = time.time() if now is None else now
    if int(m[1]) <= now:
        return "expired token"
    if int(m[1]) > now + MAX_TOKEN_LIFE:
        return "token lives too long"
    return ""


def token_ok(key: bytes, work_item: str, job: str, header: str | None, now: float | None = None) -> bool:
    return not token_problem(key, work_item, job, header, now)


def at_ok(value: str) -> bool:
    if not AT.match(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


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
    at = _field(event.get("at", ""), str, 40, "at")
    if not at_ok(at):
        raise Invalid("at must be an ISO-8601 UTC time, e.g. 2026-11-22T10:00:00Z")
    out = {"type": event["type"], "at": at}
    changed = []
    # say/ask are also Plane comments: the message level, as on the Job.
    full = event["type"] in ("progress", "plan")

    def scrub(s, name):
        r = rd.redact(s, full)
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
        timeout = SOCKET_TIMEOUT  # a client that stalls mid-request is dropped

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
            why = token_problem(key, wid, job, self.headers.get("Authorization"))
            if why:
                self._refuse(401, why, session=wid, job=job)
                return
            # Step 3A gives this a store; until then there is nothing to relay.
            self._reply(200, {"messages": []})

        def do_POST(self):
            m = EVENTS_PATH.match(urlsplit(self.path).path)
            if not m:
                self._reply(404, {"error": "not found"})
                return
            raw_len = self.headers.get("Content-Length")
            if raw_len is None:
                self._refuse(411, "Content-Length required")
                return
            if not raw_len.strip().isdigit():
                self._refuse(400, "bad Content-Length")
                return
            n = int(raw_len)
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
            why = token_problem(key, wid, job, self.headers.get("Authorization"))
            if why:
                self._refuse(401, why, session=wid, job=job)
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


class BoundedServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with at most MAX_THREADS requests at once: the
    accept loop waits for a free slot instead of starting another thread."""

    daemon_threads = True

    def __init__(self, *a, max_threads: int = MAX_THREADS, **kw):
        self._slots = threading.BoundedSemaphore(max_threads)
        super().__init__(*a, **kw)

    def process_request(self, request, client_address):
        self._slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def serve(port: int, key: bytes, emit) -> ThreadingHTTPServer:
    if len(key) < MIN_KEY:
        raise ValueError(f"the session-key must be at least {MIN_KEY} bytes")
    return BoundedServer(("", port), make_handler(key, emit))
