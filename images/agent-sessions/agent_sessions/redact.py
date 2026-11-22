"""Secret-shaped strings: redaction and detection.

PATTERNS and ASSIGN are the same lists as images/bench-cloud/bench-ticket's
SECRET_PATTERNS and SECRET_ASSIGN (a bench-cloud test compares them): the Job
redacts with them before sending, and the service redacts again before
logging, so a pattern the Job missed is still caught here and reported.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"
PATTERNS = [
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{10,}"),
    ("openai-style-key", r"\bsk-[A-Za-z0-9_-]{20,}"),
    ("plane-key", r"\bplane_api_[A-Za-z0-9]{16,}"),
    ("slack-token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    ("aws-key-id", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("long-hex", r"\b[0-9a-fA-F]{32,}\b"),
    # 32+ characters mixing upper, lower and digits, with no path separator.
    ("high-entropy", r"(?<![A-Za-z0-9+_-])(?=[A-Za-z0-9+_-]*[a-z])(?=[A-Za-z0-9+_-]*[A-Z])"
                     r"(?=[A-Za-z0-9+_-]*[0-9])[A-Za-z0-9+_-]{32,}={0,2}"),
]
# name=value, --flag value, Authorization headers, URL passwords and query
# parameters: the name stays, the value goes.
ASSIGN = [
    r"(?i)(\b(?:bearer|basic)\s+)(?!\[redacted\])([A-Za-z0-9._~+/=-]{8,})",
    r"(?i)(\b[\w.-]*(?:key|token|secret|passw(?:or)?d|pwd|credential|auth)[\w.-]*\s*[=:]\s*)"
    r"(?!\[redacted\])(\"[^\"]*\"|'[^']*'|[^\s\"',;&]+)",
    r"(?i)(--?[\w-]*(?:key|token|secret|password|passwd|credential)[\w-]*[= ]\s*)"
    r"(?!\[redacted\])(\"[^\"]*\"|'[^']*'|[^\s\"']+)",
    r"(://[^/\s:@]+:)(?!\[redacted\])([^@\s/]+)(@)",
]


def redact(s: str) -> str:
    for pat in ASSIGN:
        s = re.sub(pat, lambda m: m.group(1) + REDACTED + (m.group(3) if m.re.groups >= 3 else ""), s)
    for _, pat in PATTERNS:
        s = re.sub(pat, REDACTED, s)
    return s


def find(s: str) -> list[str]:
    """The names of the patterns that match s; never the matched text."""
    hits = [name for name, pat in PATTERNS if re.search(pat, s)]
    if any(re.search(pat, s) for pat in ASSIGN):
        hits.append("assignment")
    return hits
