"""Secret-shaped strings: redaction and detection.

VENDOR, CONTEXT, GENERIC and ASSIGN are the same (name, pattern) lists as
images/bench-cloud/bench-ticket's SECRET_PATTERNS, SECRET_CONTEXT,
SECRET_GENERIC and SECRET_ASSIGN (a bench-cloud test compares them). The Job
redacts with them before sending, and the service redacts again before
logging, so a pattern the Job missed is still caught here and reported.

Two levels:
- message (say/ask text, which is also a Plane comment, and the wrapper's
  last-message comment): VENDOR and CONTEXT, so commit SHAs, branch names and
  prose like "the key: ..." survive;
- full (progress summaries, plan items): all four lists.
The Job also removes its own env secrets at both levels.

Every quantifier is bounded and strings are capped at MAX_SCAN before any
regex runs, so no input can make a pattern backtrack for long.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"
MAX_SCAN = 20000
# Token shapes a vendor issues: redacted at both levels.
VENDOR = [
    # A whole PEM private key (to its END line, or the end of the text); the
    # header alone if the END is too far off to find.
    ("private-key-block", r"(?s)-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----.{0,10000}?"
                          r"(?:-----END [A-Z ]{0,40}PRIVATE KEY-----|\Z)"),
    ("private-key", r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----"),
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})"),
    ("gitlab-token", r"\bglpat-[A-Za-z0-9_-]{20,255}"),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{8,255}"),
    ("openai-style-key", r"\bsk-[A-Za-z0-9_-]{20,255}"),
    ("stripe-key", r"\b(?:sk|rk)_live_[A-Za-z0-9]{10,255}"),
    ("plane-key", r"\bplane_api_[A-Za-z0-9]{8,255}"),
    ("slack-token", r"\b(?:xox[abposr]|xapp)-[A-Za-z0-9-]{10,255}"),
    ("huggingface-token", r"\bhf_[A-Za-z0-9]{30,255}"),
    ("npm-token", r"\bnpm_[A-Za-z0-9]{30,255}"),
    ("aws-key-id", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,2048}\.[A-Za-z0-9_-]{8,2048}\.[A-Za-z0-9_-]{8,2048}"),
]
# A secret by its context, as precise as a Plane comment needs (HOMELAB-120):
# both levels. Group 1 stays, the rest of the match goes.
CONTEXT = [
    ("bearer", r"(?i)(\bbearer\s{1,8})[A-Za-z0-9._~+/=-]{16,4096}"),
    # NAME=value, NAME: value, "name": "value", with the name anywhere in an
    # identifier: GITHUB_TOKEN=, DB_PASSWORD:, client_secret=, "api_key": ...
    ("secret-assignment", r"(?i)((?<![A-Za-z0-9])[A-Za-z0-9_-]{0,64}(?:api[_-]?key|token|secret|password|passwd)"
                          r"[A-Za-z0-9_-]{0,64}[\"']?\s{0,8}[:=]\s{0,8})[\"']?[^\s'\"<>,}]{8,4096}"),
    # https://user:password@host
    ("url-credentials", r"(://[^:/\s@]{1,128}:)[^@\s/]{1,512}(?=@)"),
]
# Shapes that are usually secrets but sometimes not (a 40-hex git SHA): full
# level only.
GENERIC = [
    ("long-hex", r"\b[0-9a-fA-F]{32,1024}\b"),
    # 32+ characters mixing upper, lower and digits, with no path separator.
    ("high-entropy", r"(?<![A-Za-z0-9+_-])(?=[A-Za-z0-9+_-]{0,1024}[a-z])(?=[A-Za-z0-9+_-]{0,1024}[A-Z])"
                     r"(?=[A-Za-z0-9+_-]{0,1024}[0-9])[A-Za-z0-9+_-]{32,1024}={0,2}"),
]
# More secrets by context, broader than a comment can afford (they would eat
# prose like "the key: ..."): full level only. Group 1 stays, group 2 (the
# value) goes, group 3 (if any) stays. Names start at a word boundary
# ((?<![\w.-])), so a long run of word characters is tried once.
_NAME = r"(?:key|token|secret|passw(?:or)?d|pass|pwd|credential|auth)"
ASSIGN = [
    ("auth-header", r"(?i)(\bauthorization[\"']?\s{0,8}[:=]\s{0,8}[\"']?(?:bearer|basic|token|digest)?\s{0,8})"
                    r"(?!\[redacted\])([^\s\"']{1,4096})"),
    ("bearer-short", r"(?i)(\b(?:bearer|basic)\s{1,8})(?!\[redacted\])([A-Za-z0-9._~+/=-]{8,4096})"),
    ("assignment", r"(?i)((?<![\w.-])[\"']?[\w.-]{0,64}" + _NAME + r"[\w.-]{0,64}[\"']?\s{0,8}[=:]\s{0,8})"
                   r"(?!\[redacted\])(\"[^\"]{0,4096}\"|'[^']{0,4096}'|[^\s\"',;&]{1,4096})"),
    ("flag", r"(?i)((?<![\w-])--?[\w-]{0,64}(?:key|token|secret|password|passwd|pass|credential)[\w-]{0,64}"
             r"(?:=|\s{1,8}))(?!\[redacted\])(\"[^\"]{0,4096}\"|'[^']{0,4096}'|[^\s\"'-][^\s\"']{0,4095})"),
    ("spaced-value", r"(?i)((?<![\w.-])[\w.-]{0,64}(?:secret|token|passw(?:or)?d|api[_-]?key|access[_-]?key|"
                     r"private[_-]?key)[\w.-]{0,64}\s{1,8})(?!\[redacted\])([A-Za-z0-9+/=_.-]{16,4096})"),
    ("mysql-password", r"(\bmysql\w{0,16}\b[^\n|;&]{0,200}?\s-p)(?!\[redacted\])([^\s\"']{1,4096})"),
    ("sshpass", r"(\bsshpass\s{1,8}(?:-[a-zA-Z]{1,8}\s{1,8}){0,5}-p\s{0,8})(?!\[redacted\])([^\s\"']{1,4096})"),
    ("curl-user", r"((?<!\S)(?:-u|--user)(?:=|\s{0,8})[^\s:\"']{1,128}:)(?!\[redacted\])([^\s\"']{1,4096})"),
    ("url-password", r"(://[^/\s:@]{1,128}:)(?!\[redacted\])([^@\s/]{1,512})(@)"),
]


def _sub(pat: str, s: str) -> str:
    """Keep group 1 (the name) and group 3 (a suffix), if the pattern has them."""
    return re.sub(pat, lambda m: (m.group(1) if m.re.groups >= 1 else "") + REDACTED
                  + (m.group(3) if m.re.groups >= 3 else ""), s)


def levels(full: bool) -> list:
    return VENDOR + CONTEXT + (ASSIGN + GENERIC if full else [])


def redact(s: str, full: bool = True) -> str:
    s = str(s or "").replace("\r\n", "\n")[:MAX_SCAN]
    for _, pat in levels(full):
        s = _sub(pat, s)
    return s


def find(s: str, full: bool = True) -> list[str]:
    """The names of the patterns that match s; never the matched text."""
    s = str(s or "")[:MAX_SCAN]
    # A value already replaced isn't a finding.
    return [name for name, pat in levels(full)
            if any(REDACTED not in m.group(0)[len(m.group(1)) if m.re.groups else 0:]
                   for m in re.finditer(pat, s))]
