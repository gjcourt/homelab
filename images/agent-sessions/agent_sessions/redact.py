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

No pattern can backtrack for long: names are bounded, and a value is either
the end of its pattern or matched possessively. Values themselves are
unbounded, so an over-long token is redacted whole, not left with a tail.
A text is scanned up to MAX_SCAN plus a margin, cut to MAX_SCAN, and scanned
again, so a secret that straddles the cut leaves no fragment.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"
MAX_SCAN = 20000
SCAN_MARGIN = 4096
# Token shapes a vendor issues: redacted at both levels.
VENDOR = [
    # A whole PEM private key (to its END line, or the end of the text); the
    # header alone if the END is too far off to find.
    ("private-key-block", r"(?s)-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----.{0,20000}?"
                          r"(?:-----END [A-Z ]{0,40}PRIVATE KEY-----|\Z)"),
    ("private-key", r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----"),
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("gitlab-token", r"\bglpat-[A-Za-z0-9_-]{20,}"),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{8,}"),
    ("openai-style-key", r"\bsk-[A-Za-z0-9_-]{20,}"),
    ("stripe-key", r"\b(?:sk|rk)_live_[A-Za-z0-9]{10,}"),
    ("plane-key", r"\bplane_api_[A-Za-z0-9]{8,}"),
    ("slack-token", r"\b(?:xox[abposr]|xapp)-[A-Za-z0-9-]{10,}"),
    ("huggingface-token", r"\bhf_[A-Za-z0-9]{30,}"),
    ("npm-token", r"\bnpm_[A-Za-z0-9]{30,}"),
    ("aws-key-id", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}+\.[A-Za-z0-9_-]{8,}+\.[A-Za-z0-9_-]{8,}"),
]
# A secret by its context, as precise as a Plane comment needs (HOMELAB-120):
# both levels. Group 1 stays, the rest of the match goes.
CONTEXT = [
    ("bearer", r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
    # NAME=value, NAME: value, "name": "value", with the name anywhere in an
    # identifier: GITHUB_TOKEN=, DB_PASSWORD:, client_secret=, "api_key": ...
    ("secret-assignment", r"(?i)((?<![A-Za-z0-9])[A-Za-z0-9_-]{0,64}(?:api[_-]?key|token|secret|password|passwd)"
                          r"[A-Za-z0-9_-]{0,64}[\"']?\s*+[:=]\s*+)[\"']?[^\s'\"<>,}]{8,}"),
    # https://user:password@host
    ("url-credentials", r"(://[^:/\s@]{1,128}:)[^@\s/]++(?=@)"),
]
# Shapes that are usually secrets but sometimes not (a 40-hex git SHA): full
# level only.
GENERIC = [
    ("long-hex", r"\b[0-9a-fA-F]{32,}+\b"),
    # 32+ characters mixing upper, lower and digits, with no path separator.
    ("high-entropy", r"(?<![A-Za-z0-9+_-])(?=[A-Z0-9+_-]*+[a-z])(?=[a-z0-9+_-]*+[A-Z])"
                     r"(?=[A-Za-z+_-]*+[0-9])[A-Za-z0-9+_-]{32,}+={0,2}"),
]
# More secrets by context, broader than a comment can afford (they would eat
# prose like "the key: ..."): full level only. Group 1 stays, group 2 (the
# value) goes, group 3 (if any) stays. Names start at a word boundary
# ((?<![\w.-])), so a long run of word characters is tried once.
_NAME = r"(?:key|token|secret|passw(?:or)?d|pass|pwd|credential|auth)"
ASSIGN = [
    ("auth-header", r"(?i)(\bauthorization[\"']?\s*+[:=]\s*+[\"']?(?:bearer|basic|token|digest)?\s*+)"
                    r"(?!\[redacted\])([^\s\"']+)"),
    ("bearer-short", r"(?i)(\b(?:bearer|basic)\s+)(?!\[redacted\])([A-Za-z0-9._~+/=-]{8,})"),
    ("assignment", r"(?i)((?<![\w.-])[\"']?[\w.-]{0,64}" + _NAME + r"[\w.-]{0,64}[\"']?\s*+[=:]\s*+)"
                   r"(?!\[redacted\])(\"[^\"]*+\"|'[^']*+'|[^\s\"',;&]+)"),
    ("flag", r"(?i)((?<![\w-])--?[\w-]{0,64}(?:key|token|secret|password|passwd|pass|credential)[\w-]{0,64}"
             r"(?:=|\s+))(?!\[redacted\])(\"[^\"]*+\"|'[^']*+'|[^\s\"'-][^\s\"']*)"),
    ("spaced-value", r"(?i)((?<![\w.-])[\w.-]{0,64}(?:secret|token|passw(?:or)?d|api[_-]?key|access[_-]?key|"
                     r"private[_-]?key)[\w.-]{0,64}\s+)(?!\[redacted\])([A-Za-z0-9+/=_.-]{16,})"),
    ("mysql-password", r"(\bmysql\w{0,16}\b[^\n|;&]{0,200}?\s-p)(?!\[redacted\])([^\s\"']+)"),
    ("sshpass", r"(\bsshpass\s+(?:-[a-zA-Z]{1,8}\s+){0,5}-p\s*)(?!\[redacted\])([^\s\"']+)"),
    ("curl-user", r"((?<!\S)(?:-u|--user)(?:=|\s*)[^\s:\"']{1,128}:)(?!\[redacted\])([^\s\"']+)"),
    ("url-password", r"(://[^/\s:@]{1,128}:)(?!\[redacted\])([^@\s/]++)(@)"),
]


def _sub(pat: str, s: str) -> str:
    """Keep group 1 (the name) and group 3 (a suffix), if the pattern has them."""
    return re.sub(pat, lambda m: (m.group(1) if m.re.groups >= 1 else "") + REDACTED
                  + (m.group(3) if m.re.groups >= 3 else ""), s)


def levels(full: bool) -> list:
    return VENDOR + CONTEXT + (ASSIGN + GENERIC if full else [])


def redact(s: str, full: bool = True) -> str:
    s = str(s or "").replace("\r\n", "\n")[:MAX_SCAN + SCAN_MARGIN]
    for _, pat in levels(full):
        s = _sub(pat, s)
    if len(s) > MAX_SCAN:
        # Anything the cut made newly visible sits at the end: rescan that.
        head, tail = s[:MAX_SCAN - SCAN_MARGIN], s[MAX_SCAN - SCAN_MARGIN:MAX_SCAN]
        for _, pat in levels(full):
            tail = _sub(pat, tail)
        s = head + tail
    return s


def find(s: str, full: bool = True) -> list[str]:
    """The names of the patterns that match s; never the matched text."""
    s = str(s or "")[:MAX_SCAN]
    # A value already replaced isn't a finding.
    return [name for name, pat in levels(full)
            if any(REDACTED not in m.group(0)[len(m.group(1)) if m.re.groups else 0:]
                   for m in re.finditer(pat, s))]
