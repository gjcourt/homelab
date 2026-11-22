import io
import json
import threading
import urllib.error
import urllib.request

import pytest
import socketserver

from agent_sessions import __main__ as cli
from agent_sessions import check, server

class QuickServer(server.ThreadingHTTPServer):
    """No reverse DNS lookup on bind (HTTPServer's getfqdn can take 30 s on macOS)."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


KEY = b"test-session-key-of-at-least-32-bytes"
WID = "3f1c2a9e-0000-4000-8000-0000000000aa"
JOB = "ad-sandbox-9-20261122-abc123"


@pytest.fixture
def svc():
    log = []
    srv = QuickServer(("127.0.0.1", 0), server.make_handler(KEY, log.append))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", log
    srv.shutdown()


def call(url, method="GET", body=None, tok=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def events(*evs, job=JOB):
    return {"job": job, "events": list(evs)}


PROGRESS = {"type": "progress", "tool": "Bash", "summary": "git status", "at": "2026-11-22T10:00:00Z"}
AT = "2026-11-22T10:00:00Z"
PLAN = {"type": "plan", "items": [{"content": "fix test", "status": "in_progress"}], "at": AT}


def test_token_is_the_plans_hmac():
    import hashlib
    import hmac
    assert server.token(KEY, WID, JOB) == hmac.new(KEY, f"{WID}|{JOB}".encode(), hashlib.sha256).hexdigest()


def test_good_events_are_logged(svc):
    url, log = svc
    st, body = call(f"{url}/sessions/{WID}/events", "POST",
                    events(PROGRESS, PLAN, {"type": "say", "text": "hi", "at": AT},
                           {"type": "ask", "text": "which?", "at": AT}),
                    server.token(KEY, WID, JOB))
    assert (st, body) == (202, {"accepted": 4})
    assert [r["type"] for r in log] == ["progress", "plan", "say", "ask"]
    assert all(r["session"] == WID and r["job"] == JOB and r["logged"] for r in log)
    assert log[0]["summary"] == "git status" and log[1]["items"] == PLAN["items"]


@pytest.mark.parametrize("tok", [None, "0" * 64, server.token(KEY, WID, "ad-other-job"),
                                 server.token(KEY, "3f1c2a9e-0000-4000-8000-0000000000bb", JOB),
                                 server.token(b"another-key-another-key-another-key", WID, JOB)])
def test_any_other_token_is_refused(svc, tok):
    url, log = svc
    st, _ = call(f"{url}/sessions/{WID}/events", "POST", events(PROGRESS), tok)
    assert st == 401
    assert [r.get("refused") for r in log] == [401]
    assert not any(r.get("type") for r in log)


def test_a_token_is_for_its_own_ticket_only(svc):
    url, _ = svc
    other = "3f1c2a9e-0000-4000-8000-0000000000bb"
    st, _ = call(f"{url}/sessions/{other}/events", "POST", events(PROGRESS), server.token(KEY, WID, JOB))
    assert st == 401


def test_no_key_refuses_everything():
    log = []
    h = server.make_handler(b"", log.append)
    srv = QuickServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st, _ = call(f"http://127.0.0.1:{srv.server_address[1]}/sessions/{WID}/events", "POST",
                     events(PROGRESS), server.token(b"", WID, JOB))
        assert st == 401
    finally:
        srv.shutdown()


@pytest.mark.parametrize("body", [
    {"job": JOB, "events": []},
    {"job": JOB, "events": [{"type": "nope"}]},
    {"job": JOB, "events": [{"type": "progress", "tool": 3, "summary": "", "at": AT}]},
    {"job": JOB, "events": [{"type": "plan", "items": ["x"], "at": AT}]},
    {"job": JOB, "events": [{"type": "say", "text": "x" * 20001, "at": AT}]},
    {"job": JOB, "events": [PROGRESS] * 101},
])
def test_out_of_shape_bodies_are_refused(svc, body):
    url, log = svc
    st, _ = call(f"{url}/sessions/{WID}/events", "POST", body, server.token(KEY, WID, JOB))
    assert st == 400
    assert not any(r.get("type") for r in log)


def test_bad_ids_and_bodies(svc):
    url, _ = svc
    tok = server.token(KEY, WID, JOB)
    assert call(f"{url}/sessions/not-a-uuid!/events", "POST", events(PROGRESS), tok)[0] == 400
    assert call(f"{url}/sessions/{WID}/events", "POST", events(PROGRESS, job="Bad_Job"), tok)[0] == 400
    assert call(f"{url}/sessions/{WID}/events", "POST", tok=tok, raw=b"{nope")[0] == 400
    assert call(f"{url}/sessions/{WID}/events", "POST", tok=tok, raw=b"x" * (server.MAX_BODY + 1))[0] == 413
    assert call(f"{url}/nope", "POST", {})[0] == 404


def test_unknown_fields_are_dropped_and_secrets_redacted_on_arrival(svc):
    url, log = svc
    ev = dict(PROGRESS, summary="export T=ghp_" + "a" * 36, output="file contents")
    call(f"{url}/sessions/{WID}/events", "POST", events(ev), server.token(KEY, WID, JOB))
    warn, rec = log
    assert warn == {"warning": "redacted on arrival", "session": WID, "job": JOB,
                    "type": "progress", "fields": ["summary"]}
    assert "output" not in rec and "ghp_" not in rec["summary"]


def test_messages_needs_the_token_and_is_empty_for_now(svc):
    url, _ = svc
    tok = server.token(KEY, WID, JOB)
    assert call(f"{url}/sessions/{WID}/messages?job={JOB}", tok=tok) == (200, {"messages": []})
    assert call(f"{url}/sessions/{WID}/messages?job={JOB}")[0] == 401
    assert call(f"{url}/sessions/{WID}/messages?job=x-y", tok=tok)[0] == 401


def test_healthz(svc):
    url, _ = svc
    assert call(f"{url}/healthz") == (200, {"ok": True})


def test_secret_manifest(tmp_path, monkeypatch, capsys):
    kf = tmp_path / "key"
    kf.write_bytes(KEY + b"\n")
    monkeypatch.setenv("SESSION_KEY_FILE", str(kf))
    assert cli.main(["secret", "--work-item", WID, "--job", JOB]) == 0
    m = json.loads(capsys.readouterr().out)
    assert m["metadata"] == {"name": f"{JOB}-session", "namespace": "bench-cloud",
                             "labels": {"agent-sessions/job": JOB}}
    assert m["stringData"]["BENCH_SESSION_TOKEN"] == server.token(KEY, WID, JOB)
    monkeypatch.setenv("SESSION_KEY_FILE", str(tmp_path / "missing"))
    assert cli.main(["secret", "--work-item", WID, "--job", JOB]) == 2


# --- the scripted check ----------------------------------------------------------

def logged(*recs):
    return [json.dumps({"logged": "t", "session": WID, "job": JOB, **r}) for r in recs]


def test_check_passes_a_clean_run():
    lines = ["listening", json.dumps({"listening": 8080})] + logged(PROGRESS, PLAN)
    ok, report = check.check(lines, WID, JOB)
    assert ok, report
    assert report[0].endswith("2 events plan 1, progress 1")


def test_check_fails_on_secret_shapes_without_echoing_them():
    tok = "ghp_" + "Q" * 36
    ok, report = check.check(logged(PROGRESS, dict(PROGRESS, summary=f"echo {tok}")), WID, JOB)
    assert not ok
    assert any("matches github-token" in r for r in report)
    assert tok not in "\n".join(report)


def test_check_fails_on_known_values_and_warnings():
    ok, report = check.check(logged(dict(PROGRESS, summary="cat hunter2-but-long")), WID, JOB,
                             secret_values=["hunter2-but-long"])
    assert not ok and any("--secret-env #1" in r for r in report)
    warn = json.dumps({"warning": "redacted on arrival", "session": WID, "job": JOB,
                       "type": "progress", "fields": ["summary"]})
    ok, report = check.check(logged(PROGRESS) + [warn], WID, JOB)
    assert not ok and any("redacted" in r for r in report)


def test_check_fails_without_progress_or_on_bad_shape():
    assert not check.check(logged(PLAN), WID, JOB)[0]
    assert not check.check(logged(PROGRESS, dict(PROGRESS, extra=1)), WID, JOB)[0]
    assert not check.check([], WID, JOB)[0]


def test_check_ignores_other_jobs_and_expect_none():
    other = [json.dumps({"logged": "t", "session": WID, "job": "ad-other", **PROGRESS})]
    assert check.check(other, WID, JOB, expect_none=True)[0]
    assert not check.check(logged(PROGRESS), WID, JOB, expect_none=True)[0]


def test_check_cli(tmp_path, monkeypatch, capsys):
    f = tmp_path / "log"
    f.write_text("\n".join(logged(PROGRESS)) + "\n")
    monkeypatch.setenv("PLANE_API_KEY", "not-in-the-log-value")
    assert check.main(["--work-item", WID, "--job", JOB, "--log", str(f), "--secret-env", "PLANE_API_KEY"]) == 0
    assert check.main(["--work-item", WID, "--job", JOB, "--log", str(f), "--secret-env", "UNSET_VAR_X"]) == 2
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert check.main(["--work-item", WID, "--job", JOB]) == 1


# --- hardening (review of #2003) ---------------------------------------------------

def raw_post(url, path, head: bytes, body: bytes = b""):
    import socket
    host, port = url.split("//")[1].split(":")
    with socket.create_connection((host, int(port)), timeout=5) as c:
        c.sendall(b"POST " + path.encode() + b" HTTP/1.1\r\nHost: x\r\n" + head + b"\r\n" + body)
        return int(c.recv(1024).split(b" ")[1])


@pytest.mark.parametrize("head,code", [
    (b"", 411),
    (b"Content-Length: abc\r\n", 400),
    (b"Content-Length: -5\r\n", 400),
    (b"Content-Length: 999999999\r\n", 413),
])
def test_content_length_is_checked_before_reading(svc, head, code):
    url, _ = svc
    assert raw_post(url, f"/sessions/{WID}/events", head) == code


def test_non_ascii_bearer_is_401_not_500(svc):
    url, log = svc
    body = json.dumps(events(PROGRESS)).encode()
    head = (b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            + "Authorization: Bearer ünïcode\r\n".encode("latin-1"))
    assert raw_post(url, f"/sessions/{WID}/events", head, body) == 401


def test_a_stalled_client_is_dropped(monkeypatch):
    import socket
    monkeypatch.setattr(server, "SOCKET_TIMEOUT", 0.5)
    srv = QuickServer(("127.0.0.1", 0), server.make_handler(KEY, lambda r: None))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(srv.server_address, timeout=5) as c:
            c.sendall(b"POST /sessions/x/events HTTP/1.1\r\n")  # and never finishes
            assert c.recv(1024) == b""                        # closed by the server
    finally:
        srv.shutdown()


@pytest.mark.parametrize("at", ["", "x", "2026-11-22 10:00:00", "2026-11-22T10:00:00+01:00",
                                "2026-13-40T10:00:00Z", "2026-11-22T10:00:00.1234567Z"])
def test_at_must_be_iso_utc(svc, at):
    url, _ = svc
    st, _ = call(f"{url}/sessions/{WID}/events", "POST", events(dict(PROGRESS, at=at)),
                 server.token(KEY, WID, JOB))
    assert st == 400


def test_say_keeps_shas_and_branches_progress_does_not(svc):
    url, log = svc
    sha = "12920e84a1b2c3d4e5f60718293a4b5c6d7e8f90"
    call(f"{url}/sessions/{WID}/events", "POST",
         events({"type": "say", "text": f"pushed {sha} to feat/agent-sessions-step2", "at": AT},
                dict(PROGRESS, summary=f"git show {sha}")), server.token(KEY, WID, JOB))
    say = next(r for r in log if r.get("type") == "say" and "logged" in r)
    prog = next(r for r in log if r.get("type") == "progress" and "logged" in r)
    assert sha in say["text"] and "feat/agent-sessions-step2" in say["text"]
    assert sha not in prog["summary"]


def test_short_session_key_is_refused(tmp_path, monkeypatch):
    kf = tmp_path / "key"
    kf.write_bytes(b"too-short")
    monkeypatch.setenv("SESSION_KEY_FILE", str(kf))
    assert cli.main(["serve"]) == 2
    assert cli.main(["secret", "--work-item", WID, "--job", JOB]) == 2
    with pytest.raises(ValueError):
        server.serve(0, b"short", lambda r: None)


def test_bounded_server_caps_threads():
    srv = server.BoundedServer(("127.0.0.1", 0), server.make_handler(KEY, lambda r: None), max_threads=2,
                               bind_and_activate=False)
    assert srv._slots._value == 2
    srv.server_close()


def test_check_fails_on_a_refused_request():
    refused = json.dumps({"refused": 401, "why": "bad token", "path": "/x", "session": WID, "job": JOB})
    ok, report = check.check(logged(PROGRESS) + [refused], WID, JOB)
    assert not ok and any("refused" in r for r in report)
