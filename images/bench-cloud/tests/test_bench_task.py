"""HOMELAB-120: bench-task's post-exit step, run end to end with stub tools."""

import json
import os
import shutil
import stat
import subprocess
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_TASK = os.path.join(HERE, "..", "bench-task")

STUBS = {
    # A clone is just a directory; nothing else git does matters here.
    "git": '#!/bin/sh\n[ "$1" = clone ] && mkdir -p "$4"\n[ "$1" = symbolic-ref ] && exit 1\nexit 0\n',
    "rsync": "#!/bin/sh\nexit 0\n",
    "claude": ('#!/bin/sh\ncat >/dev/null\n'
               'echo \'{"type":"result","is_error":false,"num_turns":1,"result":"done"}\'\n'
               'exit "${STUB_CLAUDE_RC:-0}"\n'),
    "bench-ticket": ('#!/bin/sh\necho "$*" >> "$STUB_LOG"\n'
                     '[ "$1" = last-message ] && sleep "${STUB_SLEEP:-0}"\nexit 0\n'),
}


@pytest.fixture
def task(tmp_path):
    if not shutil.which("jq") or not shutil.which("timeout"):
        pytest.skip("needs jq and timeout")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in STUBS.items():
        p = bin_ / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    log.touch()

    def go(ticket=True, **extra):
        env = {"PATH": f"{bin_}:{os.environ['PATH']}", "HOME": str(tmp_path / "home"),
               "WORKSPACE": str(tmp_path / "work"), "TMPDIR": str(tmp_path),
               "TASK_ID": "ad-homelab-120-abc", "TASK_REPO": "gjcourt/x", "TASK_PROMPT": "do it",
               "STUB_LOG": str(log), **extra}
        if ticket:
            env["TASK_TICKET"] = json.dumps({"key": "HOMELAB-120", "project_id": "p", "work_item_id": "w",
                                             "plane_url": "http://plane", "workspace": "ws"})
            env["PLANE_API_KEY"] = "k"
        os.makedirs(env["HOME"], exist_ok=True)
        os.makedirs(env["WORKSPACE"], exist_ok=True)  # bench-entrypoint makes it
        t0 = time.time()
        r = subprocess.run(["bash", BENCH_TASK], env=env, capture_output=True, text=True,
                           timeout=60, check=False)
        calls = [line for line in log.read_text().splitlines() if line.startswith("last-message")]
        log.write_text("")
        return r, calls, time.time() - t0

    return go


def test_posts_after_a_normal_exit(task):
    r, calls, _ = task()
    assert r.returncode == 0, r.stderr
    assert len(calls) == 1 and calls[0].endswith("/result.json")
    assert "record: hestia:" in r.stdout


def test_skipped_after_sigterm(task):
    r, calls, _ = task(STUB_CLAUDE_RC="143")
    assert r.returncode == 143 and calls == []


def test_failed_run_still_tries_and_keeps_its_exit_code(task):
    r, calls, _ = task(STUB_CLAUDE_RC="1")
    assert r.returncode == 1 and len(calls) == 1


def test_not_in_ticket_mode(task):
    r, calls, _ = task(ticket=False)
    assert r.returncode == 0 and calls == []


def test_a_hung_post_is_cut_off_and_the_record_still_uploads(task):
    r, calls, took = task(STUB_SLEEP="30", BENCH_LAST_MESSAGE_TIMEOUT="2")
    assert r.returncode == 0 and len(calls) == 1
    assert took < 20 and "record: hestia:" in r.stdout
