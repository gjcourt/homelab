"""bench-ticket and bench-session: session events (agent sessions plan, step 2).

The script runs as a subprocess against one fake HTTP server that plays both
Plane and agent-sessions, so the tests see exactly the requests a Job makes.
"""

import importlib.machinery
import importlib.util
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


class QuickServer(ThreadingHTTPServer):
    """No reverse DNS lookup on bind (HTTPServer's getfqdn can take 30 s on macOS)."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "bench-ticket"
OWNER = "u-george"
WID = "wi-1"
FAKE_KEY = "plane_api_" + "Zq9" * 8          # token-shaped on purpose
FAKE_SESSION = "f" * 64                       # an HMAC-SHA256 hex
FAKE_OAUTH = "sk-ant-oat01-" + "Ab3" * 12


def load():
    loader = importlib.machinery.SourceFileLoader("bench_ticket", str(SCRIPT))
    spec = importlib.util.spec_from_loader("bench_ticket", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


bt = load()


class Fake:
    def __init__(self):
        self.requests = []
        self.comments = []
        self.state = "s-progress"
        self.sessions_status = 202
        self.lock = threading.Lock()


@pytest.fixture
def server():
    fake = Fake()

    class H(BaseHTTPRequestHandler):
        def _reply(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _record(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if n else None
            with fake.lock:
                fake.requests.append({"method": self.command, "path": self.path,
                                      "headers": dict(self.headers), "body": body})
            return body

        def do_GET(self):
            self._record()
            p = self.path.split("?")[0]
            if p.endswith("/comments/"):
                self._reply(200, {"results": fake.comments, "next_page_results": False})
            elif p.endswith("/states/"):
                self._reply(200, {"results": [{"id": "s-blocked", "name": "Blocked"},
                                              {"id": "s-progress", "name": "In Progress"}]})
            elif "/states/" in p:
                self._reply(200, {"name": {"s-blocked": "Blocked"}.get(p.rstrip("/").rsplit("/", 1)[1],
                                                                       "In Progress")})
            elif p.endswith(f"/work-items/{WID}/"):
                self._reply(200, {"id": WID, "state": fake.state})
            else:
                self._reply(404, {})

        def do_POST(self):
            body = self._record()
            if self.path.startswith("/sessions/"):
                self._reply(fake.sessions_status, {})
            elif self.path.endswith("/comments/"):
                fake.comments.append({"id": f"c{len(fake.comments)}", "actor": "u-jobs",
                                      "comment_html": body["comment_html"]})
                self._reply(201, {"id": "new"})
            else:
                self._reply(404, {})

        def do_PATCH(self):
            body = self._record()
            fake.state = body.get("state", fake.state)
            self._reply(200, {})

        def log_message(self, *a):
            pass

    srv = QuickServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fake.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield fake
    srv.shutdown()


def env_for(fake, home, session=True):
    ticket = {"project_id": "p1", "work_item_id": WID, "plane_url": fake.url, "workspace": "ws",
              "owner_id": OWNER, "key": "SANDBOX-9", "seen_comments": []}
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "TASK_ID": "ad-sandbox-9-x",
           "PLANE_API_KEY": FAKE_KEY, "CLAUDE_CODE_OAUTH_TOKEN": FAKE_OAUTH,
           "WORKSPACE": "/home/node/work", "BENCH_WAIT_MINUTES": "0.02",
           "BENCH_WAIT_POLL": "0.2"}
    if session:
        ticket["sessions_url"] = fake.url
        env["BENCH_SESSION_TOKEN"] = FAKE_SESSION
    env["TASK_TICKET"] = json.dumps(ticket)
    return env


def run(env, *args, stdin="", argv0=None):
    exe = argv0 or str(SCRIPT)
    return subprocess.run([sys.executable, exe, *args], input=stdin, env=env,
                          capture_output=True, text=True, timeout=30)


def hook(env, tool, inp, resp=None):
    return run(env, "hook", stdin=json.dumps({"tool_name": tool, "tool_input": inp,
                                              "tool_response": resp or {}}))


def session_posts(fake):
    return [r for r in fake.requests if r["path"].startswith("/sessions/")]


def all_events(fake):
    return [e for r in session_posts(fake) for e in r["body"]["events"]]


@pytest.fixture
def bench_session(tmp_path):
    link = tmp_path / "bin" / "bench-session"
    link.parent.mkdir()
    link.symlink_to(SCRIPT)
    return str(link)


# --- redaction and summaries ---------------------------------------------------

def test_redact_env_values_and_token_shapes(monkeypatch):
    monkeypatch.setenv("PLANE_API_KEY", "averyprivatevalue")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "bench-cloud[bot]")
    s = bt.redact("echo averyprivatevalue ghp_" + "a1" * 15 + " sk-ant-api03-xyzxyzxyzxyz "
                  "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk "
                  + "0123456789abcdef" * 4 + " by bench-cloud[bot]")
    assert "averyprivatevalue" not in s and "ghp_" not in s and "sk-ant" not in s
    assert "eyJ" not in s and "0123456789abcdef" not in s
    assert "bench-cloud[bot]" in s  # AUTHOR isn't AUTH: not a secret name


def test_redact_assignments_headers_and_urls():
    s = bt.redact('curl -H "Authorization: Bearer abcdefghijklmnop" '
                  "https://u:pw12345@x.io/?token=zzz9 PLANE_API_KEY=abc --password hunter22")
    for secret in ("abcdefghijklmnop", "pw12345", "zzz9", "=abc", "hunter22"):
        assert secret not in s
    assert "https://u:[redacted]@x.io/" in s


def test_redact_leaves_ordinary_work_alone():
    for s in ("git status", "pytest -q tests/test_bench_ticket.py", "src/components/Button.tsx",
              "make lint && go test ./...", "rg -n 'def flush' images/"):
        assert bt.redact(s) == s


def test_summaries_are_fixed_form():
    assert bt.summarize("Bash", {"command": "git status\nrm -rf x"}) == "git status"
    assert len(bt.summarize("Bash", {"command": "echo " + "x" * 500})) <= 120
    assert bt.summarize("Read", {"file_path": "/home/node/work/repo/src/a.py"}) == "src/a.py"
    assert bt.summarize("Edit", {"file_path": "/etc/x", "old_string": "SECRET", "new_string": "y"}) == "/etc/x"
    assert bt.summarize("Grep", {"pattern": "flush", "path": "/home/node/work/repo/img"}) == "flush in img"
    assert bt.summarize("WebSearch", {"query": "cilium netpol"}) == "cilium netpol"
    assert bt.summarize("mcp__plane__workitem_comment", {"action": "create", "comment_html": "<p>hi</p>"}) == "create"
    assert bt.summarize("Write", {"file_path": "a", "content": "body"}) == "a"
    assert bt.summarize("Bash", {"command": "export T=ghp_" + "b" * 36}) == "export T=[redacted]"


def test_plan_from_todowrite_and_task_tools():
    items = bt.update_plan([], "TodoWrite", {"todos": [
        {"content": "read", "status": "completed", "activeForm": "Reading"},
        {"content": "fix", "status": "in_progress"}]}, {})
    assert bt.public_plan(items) == [{"content": "read", "status": "completed"},
                                     {"content": "fix", "status": "in_progress"}]
    items = bt.update_plan([], "TaskCreate", {"subject": "write tests"}, {"task": {"id": "1"}})
    items = bt.update_plan(items, "TaskCreate", {"subject": "open PR"}, {"task": {"id": "2"}})
    items = bt.update_plan(items, "TaskUpdate", {"taskId": "1", "status": "completed"}, {})
    items = bt.update_plan(items, "TaskUpdate", {"taskId": "2", "status": "deleted"}, {})
    assert bt.public_plan(items) == [{"content": "write tests", "status": "completed"}]
    assert bt.update_plan(items, "Bash", {}, {}) is None


# --- the hook ------------------------------------------------------------------

def test_phase1_job_sends_no_session_events(server, tmp_path):
    env = env_for(server, tmp_path, session=False)
    assert run(env, "init").returncode == 0
    r = hook(env, "Bash", {"command": "ls"})
    r2 = hook(env, "TodoWrite", {"todos": [{"content": "a", "status": "pending"}]})
    assert r.returncode == r2.returncode == 0
    assert r.stdout == r2.stdout == ""
    assert session_posts(server) == []
    state = json.loads((tmp_path / ".bench-ticket.json").read_text())
    assert not {"pending", "plan", "plan_items", "messages", "sent"} & set(state)


def test_session_hook_sends_batched_progress_and_plan(server, tmp_path):
    env = env_for(server, tmp_path)
    run(env, "init")
    hook(env, "Bash", {"command": "git status"})          # first call: sent at once
    hook(env, "Read", {"file_path": "/home/node/work/repo/README.md"})
    hook(env, "TodoWrite", {"todos": [{"content": "fix test", "status": "in_progress"}]})
    posts = session_posts(server)
    assert len(posts) == 1                                 # the rest wait for the 10 s window
    p = posts[0]
    assert p["path"] == f"/sessions/{WID}/events"
    assert p["headers"]["Authorization"] == f"Bearer {FAKE_SESSION}"
    assert p["body"]["job"] == "ad-sandbox-9-x"
    ev = p["body"]["events"]
    assert [(e["type"], e["tool"], e["summary"]) for e in ev] == [("progress", "Bash", "git status")]
    assert set(ev[0]) == {"type", "tool", "summary", "at"}

    # Open the window: the held progress and the latest plan go in one send.
    sf = tmp_path / ".bench-ticket.json"
    st = json.loads(sf.read_text())
    st["sent"] -= 11
    sf.write_text(json.dumps(st))
    hook(env, "Bash", {"command": "pytest -q"})
    posts = session_posts(server)
    assert len(posts) == 2
    ev = posts[1]["body"]["events"]
    assert [e["type"] for e in ev] == ["progress", "progress", "progress", "plan"]
    assert ev[-1]["items"] == [{"content": "fix test", "status": "in_progress"}]
    assert set(ev[-1]) == {"type", "items", "at"}


def test_session_hook_holds_at_most_20_progress_events(server, tmp_path):
    env = env_for(server, tmp_path)
    run(env, "init")
    sf = tmp_path / ".bench-ticket.json"
    st = json.loads(sf.read_text())
    st["sent"] = time.time() + 3600                       # window shut for the test
    sf.write_text(json.dumps(st))
    for i in range(25):
        hook(env, "Bash", {"command": f"echo {i}"})
    st = json.loads(sf.read_text())
    assert len(st["pending"]) == 20 and st["pending"][-1]["summary"] == "echo 24"
    assert session_posts(server) == []


def test_failed_send_keeps_the_batch(server, tmp_path):
    env = env_for(server, tmp_path)
    run(env, "init")
    server.sessions_status = 503
    r = hook(env, "Bash", {"command": "ls"})
    assert r.returncode == 0 and r.stdout == ""            # never fails the tool call
    st = json.loads((tmp_path / ".bench-ticket.json").read_text())
    assert [e["summary"] for e in st["pending"]] == ["ls"]


def test_events_never_carry_env_secrets(server, tmp_path):
    env = env_for(server, tmp_path)
    run(env, "init")
    hook(env, "Bash", {"command": f"curl -H 'X-API-Key: {FAKE_KEY}' {server.url} # {FAKE_OAUTH}"})
    raw = json.dumps(session_posts(server))
    for secret in (FAKE_KEY, FAKE_OAUTH):
        assert secret not in raw
    assert all_events(server)[0]["summary"].startswith("curl -H 'X-API-Key: [redacted]")


def test_stop_flushes_held_events(server, tmp_path):
    env = env_for(server, tmp_path)
    run(env, "init")
    hook(env, "Bash", {"command": "one"})
    hook(env, "Bash", {"command": "two"})
    assert len(session_posts(server)) == 1
    run(env, "stop", stdin=json.dumps({"stop_hook_active": True}))
    assert [e["summary"] for e in session_posts(server)[1]["body"]["events"]] == ["two"]


# --- bench-session ---------------------------------------------------------------

def test_say_posts_a_comment_and_a_say_event(server, tmp_path, bench_session):
    env = env_for(server, tmp_path)
    run(env, "init")
    r = run(env, "say", "Opened https://github.com/gjcourt/x/pull/3 <ok>", argv0=bench_session)
    assert r.returncode == 0, r.stderr
    assert server.comments[-1]["comment_html"] == (
        '<p>Opened <a href="https://github.com/gjcourt/x/pull/3">https://github.com/gjcourt/x/pull/3</a>'
        " &lt;ok&gt;</p>")
    assert bt.lint_comment(server.comments[-1]["comment_html"]) == []
    ev = all_events(server)
    assert ev[-1]["type"] == "say" and ev[-1]["text"].startswith("Opened https://")
    assert set(ev[-1]) == {"type", "text", "at"}
    assert not any(r["method"] == "PATCH" for r in server.requests)   # no state change
    assert json.loads((tmp_path / ".bench-ticket.json").read_text())["commented"] is True


def test_say_without_session_is_plane_only(server, tmp_path, bench_session):
    env = env_for(server, tmp_path, session=False)
    run(env, "init")
    assert run(env, "say", "hello", argv0=bench_session).returncode == 0
    assert server.comments[-1]["comment_html"] == "<p>hello</p>"
    assert session_posts(server) == []


def test_say_reads_stdin_and_redacts(server, tmp_path, bench_session):
    env = env_for(server, tmp_path)
    run(env, "init")
    assert run(env, "say", stdin=f"key is {FAKE_KEY}\n\nsecond para", argv0=bench_session).returncode == 0
    assert FAKE_KEY not in server.comments[-1]["comment_html"]
    assert server.comments[-1]["comment_html"] == "<p>key is [redacted]</p><p>second para</p>"


def test_ask_posts_blocks_and_waits(server, tmp_path, bench_session):
    env = env_for(server, tmp_path)
    run(env, "init")
    r = run(env, "ask", "Which branch?", argv0=bench_session)
    assert r.returncode == 0, r.stderr
    assert server.comments[-1]["comment_html"] == "<p>Which branch?</p>"
    assert any(q["method"] == "PATCH" and q["body"] == {"state": "s-blocked"} for q in server.requests)
    assert "No comment from George" in r.stdout              # the wait ran (and timed out)
    assert all_events(server)[-1]["type"] == "ask"


def test_ask_returns_georges_answer(server, tmp_path, bench_session):
    env = env_for(server, tmp_path)
    env["BENCH_WAIT_MINUTES"] = "1"
    run(env, "init")
    threading.Timer(1.0, lambda: server.comments.append(
        {"id": "g1", "actor": OWNER, "comment_html": "<p>use main</p>", "created_at": "z"})).start()
    r = run(env, "ask", "Which branch?", argv0=bench_session)
    assert "use main" in r.stdout


def test_say_fails_loudly_when_plane_is_down(tmp_path, bench_session):
    env = env_for(type("F", (), {"url": "http://127.0.0.1:9"})(), tmp_path)
    r = run(env, "say", "hi", argv0=bench_session)
    assert r.returncode == 1 and "not posted" in r.stderr


def test_bench_session_outside_a_ticket_job(tmp_path, bench_session):
    r = subprocess.run([sys.executable, bench_session, "say", "hi"], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    assert r.returncode == 1 and "only in a ticket Job" in r.stdout


def test_patterns_match_the_session_service():
    """The Job and the service redact and check with the same patterns."""
    sys.path.insert(0, str(HERE.parents[1] / "agent-sessions"))
    try:
        from agent_sessions import redact as svc
    finally:
        sys.path.pop(0)
    assert svc.PATTERNS == bt.SECRET_PATTERNS
    assert svc.ASSIGN == bt.SECRET_ASSIGN


def test_plane_down_still_sends_progress(server, tmp_path):
    env = env_for(server, tmp_path)
    ticket = json.loads(env["TASK_TICKET"])
    ticket["plane_url"] = "http://127.0.0.1:9"   # nothing listens
    env["TASK_TICKET"] = json.dumps(ticket)
    (tmp_path / ".bench-ticket.json").write_text(json.dumps({"seen": [], "checked": 0}))
    assert hook(env, "Bash", {"command": "ls"}).returncode == 0
    assert [e["summary"] for e in all_events(server)] == ["ls"]
