"""Step 2's exit check, scripted: did a Job's events reach the log, clean?

    kubectl -n <ns> logs deploy/agent-sessions | \\
      python -m agent_sessions.check --work-item <id> --job <Job name>

Passes (exit 0) when the log holds at least one progress event for that Job,
every event for it has its plan shape, and no string in any of them is
secret-shaped. It also fails on a "redacted on arrival" warning for the Job:
the service caught something the Job's own redaction let through.

--secret-env NAME (repeatable) also checks that the value of each named
environment variable appears nowhere in the Job's events, e.g. the run's
PLANE_API_KEY, exported in the checking shell. Values are compared, never
printed.

--expect-none is the other half of the exit: a run of the same ticket without
the session token must leave no events at all. It passes only if the log has
nothing for that Job.

Findings name the event's line and the pattern, never the matched text.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from . import redact as rd
from .server import SHAPES, Invalid, clean


def strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from strings(x)


def check(lines, work_item: str, job: str, secret_values=(), expect_none=False):
    """(ok, report lines)."""
    findings, kinds = [], Counter()
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("job") != job or rec.get("session") != work_item:
            continue
        if "warning" in rec:
            findings.append(f"line {n}: the service redacted {rec.get('fields')} in a "
                            f"{rec.get('type')} event on arrival")
            continue
        if rec.get("type") not in SHAPES:
            continue
        kinds[rec["type"]] += 1
        event = {k: v for k, v in rec.items() if k not in ("logged", "session", "job")}
        try:
            cleaned, changed = clean(event)
        except Invalid as e:
            findings.append(f"line {n}: {rec['type']} event out of shape ({e})")
            continue
        if set(event) != set(cleaned):
            findings.append(f"line {n}: {rec['type']} event has fields {sorted(set(event) - set(cleaned))}")
        for s in strings(event):
            for name in rd.find(s):
                findings.append(f"line {n}: {rec['type']} event matches {name}")
            for i, v in enumerate(secret_values):
                if v and v in s:
                    findings.append(f"line {n}: {rec['type']} event contains --secret-env #{i + 1}")
    total = sum(kinds.values())
    report = [f"session {work_item}, job {job}: {total} events "
              + (", ".join(f"{k} {c}" for k, c in sorted(kinds.items())) or "")]
    if expect_none:
        ok = total == 0 and not findings
        report.append("ok: no session events, as for a phase-1 run" if ok
                      else "FAIL: a run without the session token sent events")
        return ok, report + findings
    if not kinds["progress"]:
        findings.append("no progress events for this Job")
    report += findings or ["ok: every event is in shape and nothing is secret-shaped"]
    return not findings, report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="agent_sessions.check")
    p.add_argument("--work-item", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--log", default="-", help="a log file, or - for stdin (default)")
    p.add_argument("--secret-env", action="append", default=[],
                   help="name of an env var whose value must not appear (repeatable)")
    p.add_argument("--expect-none", action="store_true")
    a = p.parse_args(argv)
    missing = [n for n in a.secret_env if not os.environ.get(n)]
    if missing:
        print(f"--secret-env names unset variables: {', '.join(missing)}", file=sys.stderr)
        return 2
    values = [os.environ[n] for n in a.secret_env]
    src = sys.stdin if a.log == "-" else open(a.log)
    with src:
        ok, report = check(src, a.work_item, a.job, values, a.expect_none)
    print("\n".join(report))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
