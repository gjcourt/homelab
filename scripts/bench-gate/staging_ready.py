#!/usr/bin/env python3
"""bench-gate: may staging-deploy put this PR into the staging branch?

Run by .github/workflows/staging-deploy.yaml, from master's checkout:

    staging_ready.py --pr 123

Exit 0: ready. stdout is the head SHA that was judged, and nothing else;
staging-deploy merges exactly that commit. Exit 1: not ready. The reason is
on stderr either way.

The rules (HOMELAB-85):

- No check or status may be failing or pending. Two are ignored:
  `sync-staging` (staging-deploy's own job) and `bench-approval` (decision 2:
  an unapproved bench PR still previews in staging).
- A bench PR must also HAVE a passing `bench-paths`: the latest `bench-paths`
  status on the judged head SHA must be `success` and posted by GitHub
  Actions. Without this, a bench PR whose `bench-paths` had not been posted
  yet had nothing failing or pending, and went into staging unchecked.
- "Bench PR" is gate.py's rule (gate.is_bench_pr): opened by
  bench-cloud[bot], pushed to by it, or headed in another repo. If that
  lookup fails, the PR is treated as a bench PR. That fails closed, and costs
  a non-bench PR nothing once the gate has posted its "not a bench PR"
  success.

Plan: docs/plans/2026-11-01-bench-homelab-paths-plan.md
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate  # noqa: E402

ACTIONS_ID = 41898282  # github-actions[bot], the creator of GITHUB_TOKEN statuses
IGNORED = {"sync-staging", gate.APPROVAL}


# --------------------------------------------------------------------------
# Pure decisions (unit tested)


def latest_status(statuses: list[dict], context: str) -> dict | None:
    """The newest status for `context`. The API lists newest first, but the
    order is not relied on: sort by created_at, then id."""
    mine = [s for s in statuses if s.get("context") == context]
    if not mine:
        return None
    return max(mine, key=lambda s: (s.get("created_at") or "", s.get("id") or 0))


def decide(checks: list[dict], bench: bool, paths_status: dict | None) -> tuple[bool, str]:
    """(ready, why). `checks` is `gh pr checks --json name,state,bucket`."""
    counted = [c for c in checks if c.get("name") not in IGNORED]
    failing = sorted(c.get("name", "?") for c in counted if c.get("bucket") == "fail")
    pending = sorted(c.get("name", "?") for c in counted if c.get("bucket") == "pending")
    if failing:
        return False, f"failing: {', '.join(failing)}"
    if pending:
        return False, f"pending: {', '.join(pending)}"
    if bench:
        if paths_status is None:
            return False, f"bench PR and {gate.PATHS} has not been posted on the head yet"
        if paths_status.get("state") != "success":
            return False, f"bench PR and {gate.PATHS} is {paths_status.get('state')}"
        creator = paths_status.get("creator") or {}
        if creator.get("id") != ACTIONS_ID:
            return False, f"bench PR and {gate.PATHS} was posted by {creator.get('login') or 'unknown'}, not GitHub Actions"
    return True, f"{len(counted)} checks passed" + (f", {gate.PATHS} success" if bench else "")


# --------------------------------------------------------------------------
# GitHub


def retry(fn, attempts: int = 3, delays=(2, 5)):
    """fn(), retried on any error. Every call made through it is a GET."""
    for i in range(attempts):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            if i == attempts - 1:
                raise
            time.sleep(delays[min(i, len(delays) - 1)])


def pr_checks(repo: str, number: int) -> list[dict]:
    """`gh pr checks`, which exits non-zero when a check fails or is pending
    and prints nothing when there are no checks, so its exit code is ignored."""
    out = subprocess.run(
        ["gh", "pr", "checks", str(number), "-R", repo, "--json", "name,state,bucket"],
        capture_output=True,
        text=True,
    ).stdout
    try:
        data = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        data = []
    return data if isinstance(data, list) else []


def judge(repo: str, number: int) -> tuple[str | None, bool, str]:
    """(head sha, ready, why) for one PR, read fresh from the API."""
    try:
        pr = retry(lambda: gate.gh_api(f"repos/{repo}/pulls/{number}"))
    except Exception as e:  # noqa: BLE001
        return None, False, f"could not read the PR: {e}"
    sha = pr["head"]["sha"]
    try:
        activity = []
        if ((pr["head"].get("repo") or {}).get("full_name")) == repo:
            ref = urllib.parse.quote(pr["head"]["ref"], safe="")
            activity = retry(lambda: gate.gh_api(f"repos/{repo}/activity?ref={ref}&per_page=100", paginate=True))
        bench, why = gate.is_bench_pr(pr, activity, repo)
    except Exception as e:  # noqa: BLE001 - fail closed
        bench, why = True, f"could not tell if this is a bench PR ({e}); treating it as one"
    print(f"  head {sha[:7]}: {why}", file=sys.stderr)

    checks = pr_checks(repo, number)
    paths_status = None
    if bench:
        try:
            statuses = retry(lambda: gate.gh_api(f"repos/{repo}/commits/{sha}/statuses?per_page=100", paginate=True))
        except Exception as e:  # noqa: BLE001
            return sha, False, f"could not read the statuses on {sha[:7]}: {e}"
        paths_status = latest_status(statuses, gate.PATHS)
    ok, reason = decide(checks, bench, paths_status)
    return sha, ok, reason


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pr", type=int, required=True)
    args = ap.parse_args(argv)
    repo = os.environ["GITHUB_REPOSITORY"]
    sha, ok, why = judge(repo, args.pr)
    print(f"  {'ready' if ok else 'not ready'}: {why}", file=sys.stderr)
    if ok:
        print(sha)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
