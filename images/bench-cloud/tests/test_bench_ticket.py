"""bench-ticket and bench-session: session events (agent sessions plan, step 2).

The script runs as a subprocess against one fake HTTP server that plays both
Plane and agent-sessions, so the tests see exactly the requests a Job makes.
"""

import importlib.machinery
import importlib.util
import json
import os
import shutil
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
        self.patch_status = 200
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
            if fake.patch_status >= 400:
                self._reply(fake.patch_status, {})
                return
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
                  + "0123456789abcdef" * 4 + " by bench-cloud[bot]", full=True)
    assert "averyprivatevalue" not in s and "ghp_" not in s and "sk-ant" not in s
    assert "eyJ" not in s and "0123456789abcdef" not in s
    assert "bench-cloud[bot]" in s  # AUTHOR isn't AUTH: not a secret name


def test_redact_assignments_headers_and_urls():
    s = bt.redact('curl -H "Authorization: Bearer abcdefghijklmnop" '
                  "https://u:pw12345@x.io/?token=zzz9 PLANE_API_KEY=abc --password hunter22", full=True)
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

@pytest.mark.parametrize("keep", ["neither", "url-only", "token-only"])
def test_phase1_job_sends_no_session_events(server, tmp_path, keep):
    env = env_for(server, tmp_path, session=keep != "neither")
    if keep == "url-only":
        del env["BENCH_SESSION_TOKEN"]
    elif keep == "token-only":
        ticket = json.loads(env["TASK_TICKET"])
        del ticket["sessions_url"]
        env["TASK_TICKET"] = json.dumps(ticket)
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


def open_window(home):
    sf = home / ".bench-ticket.json"
    st = json.loads(sf.read_text())
    st["sent"] = st.get("sent", 0) - 11
    sf.write_text(json.dumps(st))


def test_failed_send_keeps_the_batch_and_resends_it(server, tmp_path):
    env = env_for(server, tmp_path)
    run(env, "init")
    server.sessions_status = 503
    r = hook(env, "Bash", {"command": "ls"})
    assert r.returncode == 0 and r.stdout == ""            # never fails the tool call
    st = json.loads((tmp_path / ".bench-ticket.json").read_text())
    assert [e["summary"] for e in st["pending"]] == ["ls"]
    server.sessions_status = 202
    hook(env, "Bash", {"command": "pwd"})                  # inside the window: held
    open_window(tmp_path)
    hook(env, "Bash", {"command": "date"})
    ok = [r for r in session_posts(server)][-1]["body"]["events"]
    assert [e["summary"] for e in ok] == ["ls", "pwd", "date"]


@pytest.mark.parametrize("code,kept", [(400, False), (401, False), (413, False),
                                       (408, True), (429, True), (500, True)])
def test_permanent_refusals_drop_the_batch(server, tmp_path, code, kept):
    env = env_for(server, tmp_path)
    run(env, "init")
    server.sessions_status = code
    r = hook(env, "Bash", {"command": "ls"})
    st = json.loads((tmp_path / ".bench-ticket.json").read_text())
    assert bool(st["pending"]) is kept
    assert ("dropped" in r.stderr) is (not kept)


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
        " &#60;ok&gt;</p>")  # bench-ticket's _esc: < as &#60;, as in the last-message comment
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
    assert svc.VENDOR == bt.SECRET_PATTERNS
    assert svc.CONTEXT == bt.SECRET_CONTEXT
    assert svc.GENERIC == bt.SECRET_GENERIC
    assert svc.ASSIGN == bt.SECRET_ASSIGN
    assert svc.REDACTED == bt.REDACTED and svc.MAX_SCAN == bt.SECRET_MAX_SCAN


def test_plane_down_still_sends_progress(server, tmp_path):
    env = env_for(server, tmp_path)
    ticket = json.loads(env["TASK_TICKET"])
    ticket["plane_url"] = "http://127.0.0.1:9"   # nothing listens
    env["TASK_TICKET"] = json.dumps(ticket)
    (tmp_path / ".bench-ticket.json").write_text(json.dumps({"seen": [], "checked": 0}))
    assert hook(env, "Bash", {"command": "ls"}).returncode == 0
    assert [e["summary"] for e in all_events(server)] == ["ls"]


# --- review of #2003 -----------------------------------------------------------

def test_a_secret_straddling_the_cut_is_removed_whole(monkeypatch):
    monkeypatch.setenv("SOME_API_TOKEN", "LITERALVALUE" * 4)
    pad = "echo " + "x" * 105 + " "                        # the value starts near 111
    out = bt.summarize("Bash", {"command": pad + "LITERALVALUE" * 4})
    assert "LITERAL" not in out and len(out) <= 120
    out = bt.summarize("Bash", {"command": pad + "deadbeef" * 6})
    assert "deadbee" not in out and "[redact" in out   # the marker itself may be cut


@pytest.mark.parametrize("s", ["x-" * 10000, "x-" * 100000])
def test_job_redaction_is_fast(s):
    t = time.perf_counter()
    bt.redact(s)
    bt.summarize("Bash", {"command": s})
    bt.update_plan([], "TodoWrite", {"todos": [{"content": s, "status": "pending"}]}, {})
    assert time.perf_counter() - t < 0.2


def test_redaction_levels(monkeypatch):
    monkeypatch.setenv("PLANE_API_KEY", "averyprivatevalue")
    sha = "12920e84a1b2c3d4e5f60718293a4b5c6d7e8f90"
    msg = bt.redact(f"pushed {sha} to feat/x, key averyprivatevalue, ghp_{'a' * 36}", full=False)
    assert sha in msg and "averyprivatevalue" not in msg and "ghp_" not in msg
    assert sha not in bt.redact(f"git show {sha}", full=True)
    monkeypatch.setenv("DB_PASS", "dbpassword1")
    monkeypatch.setenv("MY_APIKEY", "apikeyvalue1")
    assert bt.redact("dbpassword1 apikeyvalue1", full=False) == "[redacted] [redacted]"


def test_say_keeps_shas_on_the_ticket(server, tmp_path, bench_session):
    env = env_for(server, tmp_path)
    run(env, "init")
    sha = "12920e84a1b2c3d4e5f60718293a4b5c6d7e8f90"
    run(env, "say", f"Pushed {sha} to feat/agent-sessions-step2", argv0=bench_session)
    assert sha in server.comments[-1]["comment_html"]
    assert sha in all_events(server)[-1]["text"]


def test_ask_reports_a_failed_blocked_separately(server, tmp_path, bench_session):
    env = env_for(server, tmp_path)
    run(env, "init")
    server.patch_status = 500
    r = run(env, "ask", "Which branch?", argv0=bench_session)
    assert r.returncode == 1
    assert "question is posted" in r.stdout and "Don't post the question again" in r.stdout
    assert len(server.comments) == 1


@pytest.mark.skipif(not shutil.which("jq"), reason="needs jq")
def test_bench_task_prompt_only_for_a_session_job():
    src = (HERE.parent / "bench-task").read_text()
    snippet = src[src.index('  session_note=""'):]
    snippet = snippet[:snippet.index("\n  fi\n") + 6] + 'printf "%s" "$session_note"\n'
    for ticket, tok, want in [('{"sessions_url":"http://s"}', "t", True), ('{"sessions_url":"http://s"}', "", False),
                              ("{}", "t", False), ("{}", "", False)]:
        env = {"PATH": os.environ["PATH"], "TASK_TICKET": ticket}
        if tok:
            env["BENCH_SESSION_TOKEN"] = tok
        out = subprocess.run(["bash", "-c", snippet], env=env, capture_output=True, text=True, check=True).stdout
        assert ("bench-session" in out) is want


# --- against the real service -----------------------------------------------------

@pytest.fixture
def real_service(server):
    """agent_sessions.server on a free port, with the Plane fake beside it."""
    sys.path.insert(0, str(HERE.parents[1] / "agent-sessions"))
    from agent_sessions import server as svc
    sys.path.pop(0)
    log = []
    key = b"k" * 32

    class Quick(svc.BoundedServer):
        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    srv = Quick(("127.0.0.1", 0), svc.make_handler(key, log.append))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", log, key, svc
    srv.shutdown()


def test_hook_against_the_real_service(server, real_service, tmp_path):
    url, log, key, svc = real_service
    wid = "3f1c2a9e-0000-4000-8000-0000000000aa"
    env = env_for(server, tmp_path)
    ticket = json.loads(env["TASK_TICKET"])
    ticket.update(work_item_id=wid, sessions_url=url)
    env["TASK_TICKET"] = json.dumps(ticket)
    env["BENCH_SESSION_TOKEN"] = svc.token(key, wid, env["TASK_ID"])
    (tmp_path / ".bench-ticket.json").write_text(json.dumps({"seen": [], "checked": time.time()}))
    todos = [{"content": f"step {i} " + "z" * 600, "status": "pending"} for i in range(101)]
    hook(env, "Bash", {"command": f"curl -H 'X-API-Key: {FAKE_KEY}' https://x"})
    open_window(tmp_path)
    r = hook(env, "TodoWrite", {"todos": todos})
    assert r.returncode == 0 and "refused" not in r.stderr, r.stderr
    events = [x for x in log if "logged" in x]
    assert [e["type"] for e in events] == ["progress", "progress", "plan"]
    assert len(events[-1]["items"]) == 100 and all(len(i["content"]) <= 200 for i in events[-1]["items"])
    assert not any("warning" in x or "refused" in x for x in log)
    assert FAKE_KEY not in json.dumps(log)
    # A wrong token is refused, and the Job drops rather than retries forever.
    env["BENCH_SESSION_TOKEN"] = "0" * 64
    open_window(tmp_path)
    r = hook(env, "Bash", {"command": "ls"})
    assert "dropped" in r.stderr and any(x.get("refused") == 401 for x in log)


# --- second review of #2003 ---------------------------------------------------------

def _pem(body_chars):
    body = "\n".join(("MIIEow" + "Ab9+" * 16)[:64] for _ in range(body_chars // 65 + 1))
    return f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize("full", [False, True])
def test_a_large_pem_key_goes_whole(full):
    pem = _pem(13000)
    assert len(pem) > 13000
    out = bt.redact(f"key:\n{pem}\ndone", env={}, full=full)
    assert "MIIEow" not in out and "Ab9+" not in out and "PRIVATE KEY" not in out
    assert out.endswith("done")


@pytest.mark.parametrize("text,gone", [
    ("ghp_" + "a" * 300, "a" * 8),
    ("sk-ant-api03-" + "b" * 5000, "b" * 8),
    ("GITHUB_TOKEN=" + "c" * 5000, "c" * 8),
    ("Authorization: Bearer" + " " * 12 + "d" * 40, "d" * 8),
    ("https://u:" + "e" * 1000 + "@host/", "e" * 8),
])
def test_over_long_secrets_leave_no_tail(text, gone):
    for full in (False, True):
        out = bt.redact(text, env={}, full=full)
        assert gone not in out, (full, out[:80])


def test_an_env_secret_straddling_the_scan_limit_leaves_no_fragment():
    secret = "S3CRETVALUE-" + "z" * 20
    text = "x " * ((bt.SECRET_MAX_SCAN - 10) // 2) + secret + " tail"
    out = bt.redact(text, env={"MY_TOKEN": secret})
    assert "S3CRET" not in out and "zzzz" not in out
    assert len(out) <= bt.SECRET_MAX_SCAN


def test_say_too_long_is_refused(server, tmp_path, bench_session):
    env = env_for(server, tmp_path)
    run(env, "init")
    r = run(env, "say", stdin="word " * 4000, argv0=bench_session)       # 20,000 characters
    assert r.returncode == 1 and "must be under" in r.stderr
    r = run(env, "say", stdin="&" * 14000, argv0=bench_session)          # escapes past MAX_COMMENT
    assert r.returncode == 1 and "not posted" in r.stderr
    assert server.comments == [] and session_posts(server) == []
    r = run(env, "say", stdin="word " * 2900, argv0=bench_session)       # long but fine
    assert r.returncode == 0 and bt.lint_comment(server.comments[-1]["comment_html"]) == []


SAMPLES = [
    "git status", "GITHUB_TOKEN=abcdEFGH1234ijkl and ghp_" + "a" * 36,
    "pushed 12920e84a1b2c3d4e5f60718293a4b5c6d7e8f90 to feat/x",
    "curl -H 'Authorization: Token X' -u admin:pw https://u:pw@x.io/",
    '{"password": "hunter2xx", "api_key":"abc123xyz"}', "mysql -u root -phunter2 db",
    "the key: it is in vault; Bearer abcdefghijklmnopqrstuv", _pem(200), "x-" * 15000,
    "A1b2C3d4" * 6 + " eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk",
]


def test_job_and_service_redact_the_same():
    sys.path.insert(0, str(HERE.parents[1] / "agent-sessions"))
    try:
        from agent_sessions import redact as svc
    finally:
        sys.path.pop(0)
    for text in SAMPLES:
        for full in (False, True):
            assert bt.redact(text, env={}, full=full) == svc.redact(text, full=full), (full, text[:40])
