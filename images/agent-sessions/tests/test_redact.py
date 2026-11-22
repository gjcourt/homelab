import time

import pytest

from agent_sessions import redact as rd


@pytest.mark.parametrize("text,secret", [
    ('{"password": "hunter2xx", "x": 1}', "hunter2xx"),
    ('{"api_key":"abc123xyz"}', "abc123xyz"),
    ("DB_PASS=hunter2xx", "hunter2xx"),
    ("export MY_PASSWORD='s3cr3t pw'", "s3cr3t pw"),
    ("mysql -u root -phunter2xx db", "hunter2xx"),
    ("curl -u admin:pw12345 https://x.io", "pw12345"),
    ("sshpass -p pw12345 ssh host", "pw12345"),
    ("aws configure set aws_secret_access_key wJalrXUtnFEMIK7MDENGbPxRfiCY", "wJalrXUtnFEMIK7MDENGbPxRfiCY"),
    ("glpat-" + "a1" * 10, "glpat-"),
    ("hf_" + "a" * 34, "hf_"),
    ("npm_" + "b" * 36, "npm_"),
    ("sk_live_" + "c" * 24, "sk_live_"),
    ("rk_live_" + "c" * 24, "rk_live_"),
    ("xapp-1-A0123-456789abcd", "xapp-"),
    ("curl -H 'Authorization: Token X'", " X"),
    ("curl -H 'Authorization: Bearer abcdefghijk'", "abcdefghijk"),
    ("https://u:pw12345@x.io/", "pw12345"),
    ("--token=abcdef123", "abcdef123"),
    ("ghp_" + "a" * 36, "ghp_"),
])
def test_full_level_redacts(text, secret):
    out = rd.redact(text)
    assert secret not in out, out
    assert rd.find(out) == [], (out, rd.find(out))


@pytest.mark.parametrize("text", [
    "git status", "pytest -q tests", "src/components/Button.tsx", "make lint && go test ./...",
    "git checkout feat/agent-sessions-step2",
])
def test_full_level_leaves_ordinary_work_alone(text):
    assert rd.redact(text) == text


def test_message_level_keeps_shas_and_prose_but_not_vendor_tokens():
    sha = "12920e84a1b2c3d4e5f60718293a4b5c6d7e8f90"
    text = f"Pushed {sha} on feat/x-y. The key: it's in the README. Token ghp_{'a' * 36}"
    out = rd.redact(text, full=False)
    assert sha in out and "The key: it's" in out and "ghp_" not in out
    assert rd.find(out, full=False) == []


@pytest.mark.parametrize("s", ["x-" * 10000, "x." * 10000, "a" * 20000, "key" * 7000, "-p " * 7000,
                               "A1b" * 7000, "mysql " + "x " * 9000, "://" + "a:" * 9000, '"' * 20000,
                               "token " * 3000, "x-" * 100000])
def test_no_pathological_backtracking(s):
    t = time.perf_counter()
    rd.redact(s)
    rd.find(s)
    assert time.perf_counter() - t < 0.2
