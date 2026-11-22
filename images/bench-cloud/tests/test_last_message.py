"""HOMELAB-120: bench-ticket last-message, the wrapper's post-exit comment."""

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bench-ticket")


def _load():
    loader = importlib.machinery.SourceFileLoader("bench_ticket", SCRIPT)
    spec = importlib.util.spec_from_loader("bench_ticket", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


bt = _load()
TASK = "ad-homelab-120-abc"
STARTED = "2026-11-22T10:00:00Z"
ME, OWNER = "u-jobs", "u-george"


class FakeChannel:
    """PlaneComments without the network: records what would be posted."""

    def __init__(self, state="In Progress", comments=(), fail=False):
        self.ticket = {"plane_url": "http://plane", "workspace": "ws", "project_id": "p",
                       "work_item_id": "w", "owner_id": OWNER}
        self.key, self.state, self.comments, self.fail = "k", state, list(comments), fail
        self.posted, self.listed = [], False

    def current_state(self):
        if self.fail:
            raise OSError("plane down")
        return self.state

    def all(self):
        self.listed = True
        if self.fail:
            raise OSError("plane down")
        return self.comments

    def _get(self, url):
        assert url.endswith("/api/v1/users/me/")
        return {"id": ME}

    def _base(self):
        return "http://plane/api/v1/workspaces/ws/projects/p"


@pytest.fixture
def run(tmp_path, monkeypatch):
    """last_message against a result.json and a hook state file; returns (log, posted)."""
    monkeypatch.setattr(bt, "post_comment", lambda ch, body: ch.posted.append(body))

    def go(result=None, raw=None, state=None, channel=None):
        path = tmp_path / "result.json"
        if raw is not None:
            path.write_text(raw)
        elif result is not None:
            path.write_text(json.dumps(result))
        sp = tmp_path / "state.json"
        if state is not False:  # False: no hook state file (init never ran)
            sp.write_text(json.dumps(state or {"seen": [], "checked": 0}))
        ch = channel or FakeChannel()
        log = bt.last_message(str(path), ch, TASK, STARTED, state_path=str(sp))
        return log, ch.posted

    return go


SUMMARY = {"type": "result", "is_error": False,
           "result": "I couldn't reach Plane, so here's where things are.\n\n"
                     "- Opened **PR #12**: https://github.com/gjcourt/homelab/pull/12\n"
                     "- Tests pass (`pytest -q`)"}


# --- the four cases the ticket names ---

def test_agent_commented_posts_nothing(run):
    log, posted = run(SUMMARY, state={"seen": [], "commented": True})
    assert posted == [] and "commented" in log


def test_agent_silent_posts_its_last_message(run):
    log, posted = run(SUMMARY)
    assert len(posted) == 1 and "posted" in log
    body = posted[0]
    assert bt.lint_comment(body) == []
    assert body.startswith(f"<p><strong>{bt.LAST_MESSAGE_HEADER}</strong></p>")
    assert f"<code>{bt.LAST_MESSAGE_MARKER} {TASK}</code>" in body
    assert "<li>Opened <strong>PR #12</strong>: <a href=\"https://github.com/gjcourt/homelab/pull/12\">" in body
    assert "<code>pytest -q</code>" in body


@pytest.mark.parametrize("text", ["", "   \n", None])
def test_empty_result_posts_nothing(run, text):
    log, posted = run({"type": "result", "result": text})
    assert posted == [] and "empty" in log


def test_job_failed_without_result_json_posts_nothing(run):
    log, posted = run()  # no file written
    assert posted == [] and "no result.json" in log


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]"])
def test_unreadable_result_json_posts_nothing(run, raw):
    _, posted = run(raw=raw)
    assert posted == []


# --- the other ways the agent can have updated the ticket ---

@pytest.mark.parametrize("state", ["In review", "Blocked", "Done"])
def test_agent_moved_the_ticket_posts_nothing(run, state):
    log, posted = run(SUMMARY, channel=FakeChannel(state=state))
    assert posted == [] and state in log


def test_agent_comment_seen_in_plane_posts_nothing(run):
    # The hook missed it (or it went another way), but Plane has a comment
    # from the Jobs' key since the run started.
    c = {"id": "c1", "actor": ME, "comment_html": "<p>Done.</p>", "created_at": "2026-11-22T10:05:00.1Z"}
    _, posted = run(SUMMARY, state=False, channel=FakeChannel(comments=[c]))
    assert posted == []


def test_hook_state_known_skips_the_comment_lookups(run):
    ch = FakeChannel()
    _, posted = run(SUMMARY, channel=ch)
    assert len(posted) == 1 and not ch.listed


@pytest.mark.parametrize("note", [
    "<p>Started bench-cloud Job <code>ad-homelab-120-abc</code> (playbook <code>docs-fix</code>).</p>",
    "<p>Cap reached. Add <code>over-cap</code> to start this one now, or it starts when a slot frees.</p>",
    "<p>George requested changes on https://github.com/x/y/pull/1.</p>",
])
def test_agent_desk_notes_on_the_same_user_dont_count(run, note):
    # agent-desk's dispatcher and the Jobs share one Plane user (two keys).
    c = {"id": "c1", "actor": ME, "comment_html": note, "created_at": "2026-11-22T10:00:01Z"}
    _, posted = run(SUMMARY, state=False, channel=FakeChannel(comments=[c]))
    assert len(posted) == 1


def test_earlier_runs_comments_and_georges_dont_count(run):
    old = {"id": "c0", "actor": ME, "comment_html": "<p>old run</p>", "created_at": "2026-11-21T09:00:00Z"}
    george = {"id": "c1", "actor": OWNER, "comment_html": "<p>any news?</p>",
              "created_at": "2026-11-22T10:05:00Z"}
    _, posted = run(SUMMARY, state=False, channel=FakeChannel(comments=[old, george]))
    assert len(posted) == 1


def test_never_posted_twice_for_one_run(run):
    mine = {"id": "c9", "actor": "someone", "created_at": "2026-11-22T09:00:00Z",
            "comment_html": f"<p><code>{bt.LAST_MESSAGE_MARKER} {TASK}</code></p>"}
    log, posted = run(SUMMARY, state=False, channel=FakeChannel(comments=[mine]))
    assert posted == [] and "already posted" in log


def test_plane_lookups_failing_still_posts(run):
    # The 2026-11-08 shape: reads fail, but the post is still worth trying.
    _, posted = run(SUMMARY, channel=FakeChannel(fail=True))
    assert len(posted) == 1


# --- never a secret, never too long, always valid HTML ---

def test_secrets_are_redacted(monkeypatch):
    env = {"PLANE_API_KEY": "plane_api_0123456789abcdef", "CLAUDE_CODE_OAUTH_TOKEN": "oat-SECRETVALUE-999",
           "HOME": "/home/node"}
    text = ("Key was plane_api_0123456789abcdef and oat-SECRETVALUE-999.\n\n"
            "gh token ghs_" + "a" * 36 + ", Anthropic sk-ant-api03-abcdefghijkl, "
            "AWS AKIAABCDEFGHIJKLMNOP, header `Authorization: Bearer abcdefghijklmnopqrstu`.\n\n"
            "password = hunter2hunter2\n\n"
            "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaA\n-----END OPENSSH PRIVATE KEY-----")
    body = bt.last_message_html(text, TASK, env=env)
    for secret in ("plane_api_0123", "SECRETVALUE", "ghs_aaaa", "sk-ant-api03", "AKIAABCD",
                   "abcdefghijklmnopqrstu", "hunter2", "b3BlbnNzaA", "PRIVATE KEY"):
        assert secret not in body, secret
    assert body.count(bt.REDACTED) >= 8
    assert bt.redact("/home/node", env) == "/home/node"  # HOME is not a secret name


@pytest.mark.parametrize("text,secret", [
    ("GITHUB_TOKEN=abcdEFGH1234ijkl", "abcdEFGH1234"),
    ("export PLANE_API_KEY=0123456789abcdef", "0123456789abcdef"),
    ("DB_PASSWORD: correcthorsebattery", "correcthorse"),
    ("client_secret=s3cr3tvalue99", "s3cr3tvalue"),
    ('{"api_key": "zyxwvutsrq987654"}', "zyxwvutsrq"),
    ("auth_token = 'qwertyuiop12345'", "qwertyuiop"),
    ("clone https://george:hunter2pw@git.example.com/r.git", "hunter2pw"),
    ("postgres://app:pa55w0rd@db:5432/x", "pa55w0rd"),
])
def test_key_value_and_url_credentials_are_redacted(text, secret):
    out = bt.redact(text, env={})
    assert secret not in out and bt.REDACTED in out, out


def test_url_without_credentials_is_untouched():
    s = "see https://github.com/gjcourt/homelab/pull/12 and http://host:8080/x"
    assert bt.redact(s, env={}) == s


def test_env_secret_with_crlf_and_multiple_lines_is_redacted():
    pem = "-----BEGIN CERT-----\r\nAAAABBBBCCCCDDDD\r\nEEEEFFFFGGGGHHHH\r\n-----END CERT-----"
    env = {"SOME_CREDENTIAL": pem, "OTHER_TOKEN": "tok-12345678\r\n"}
    text = "line one: EEEEFFFFGGGGHHHH\r\nand the token tok-12345678 and\nAAAABBBBCCCCDDDD"
    out = bt.redact(text, env=env)
    for s in ("EEEEFFFF", "tok-1234", "AAAABBBB"):
        assert s not in out, out
    assert "\r" not in out


def test_long_message_is_capped():
    body = bt.last_message_html("word " * 5000, TASK)
    assert body is not None and bt.lint_comment(body) == []
    assert "Truncated at" in body and len(body) < bt.MAX_COMMENT


def test_worst_case_escaping_stays_under_the_comment_limit():
    body = bt.last_message_html("&" * 10000, TASK)
    assert body is not None and len(body) < bt.MAX_COMMENT


@pytest.mark.parametrize("md", [
    "# Heading\n\nSome *em* and __strong__ text.",
    "1. one\n2. two\n\n- a\n- b\n  continued",
    "```bash\nkubectl get pods <ns> && echo '<p>'\n```",
    "> quoted\n> lines\n\n---\n\nafter",
    "Unclosed **bold and `code and <script>alert(1)</script>",
    "[a link](https://example.com/x?a=1&b=2) and [bad](javascript:alert(1))",
    "snake_case_name and 2*3*4",
])
def test_markdown_always_makes_a_valid_comment(md):
    body = bt.last_message_html(md, TASK)
    assert body is not None and bt.lint_comment(body) == [], body
    assert "<script>" not in body and 'href="javascript' not in body


@pytest.mark.parametrize("md", ["\x000\x00 and \x0099\x00", "`a`\x001\x00", "\x00"])
def test_nul_bytes_cant_break_the_placeholders(md):
    body = bt.last_message_html(md + " text", TASK)
    assert body is not None and bt.lint_comment(body) == [] and "\x00" not in body


def test_deep_blockquotes_are_capped():
    body = bt.last_message_html(">" * 2000 + " deep", TASK)
    assert body is not None and bt.lint_comment(body) == [] and "deep" in body
    assert body.count("<blockquote>") <= bt.MAX_QUOTE_DEPTH + 1


def test_a_converter_crash_falls_back_to_plain(monkeypatch):
    def boom(md, depth=0):
        raise RuntimeError("bug")
    monkeypatch.setattr(bt, "md_to_html", boom)
    body = bt.last_message_html("**hi** there", TASK)
    assert body is not None and "**hi** there" in body


def test_markdown_shapes():
    h = bt.md_to_html("## Done\n\n1. first\n2. second\n\n```\na < b\n```")
    assert h == "<h2>Done</h2><ol><li>first</li><li>second</li></ol><pre><code>a &#60; b</code></pre>"


# --- the CLI, as bench-task calls it ---

def test_cli_never_fails_the_job(tmp_path):
    env = {**os.environ, "TASK_TICKET": json.dumps({"plane_url": "http://127.0.0.1:9", "workspace": "ws",
                                                     "project_id": "p", "work_item_id": "w"}),
           "PLANE_API_KEY": "k", "TASK_ID": TASK, "HOME": str(tmp_path), "BENCH_OUT": str(tmp_path)}
    r = subprocess.run([sys.executable, SCRIPT, "last-message", str(tmp_path / "missing.json")],
                       check=False, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "no result.json" in r.stdout


def test_cli_outside_ticket_mode_does_nothing(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("TASK_TICKET", "PLANE_API_KEY")}
    r = subprocess.run([sys.executable, SCRIPT, "last-message", str(tmp_path / "r.json")],
                       check=False, env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout == ""
