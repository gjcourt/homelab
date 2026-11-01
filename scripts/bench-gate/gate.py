#!/usr/bin/env python3
"""bench-gate: post the `bench-paths` and `bench-approval` commit statuses.

Run by .github/workflows/bench-gate.yaml, always from master's checkout:

    gate.py --pr 123     # evaluate one PR
    gate.py --all        # evaluate every open PR into the default branch

For a PR that is not a bench PR, both statuses are success. For a bench PR:

- bench-paths: success only if check.py finds no violation in the PR's diff.
- bench-approval: success only if gjcourt's latest deciding review approves
  the PR's current head SHA.

A bench PR is one authored by bench-cloud[bot], or whose head branch the bot
has pushed to (from the repository activity API, which records the pusher;
commit author fields are not trusted). The PR is read as data: metadata from
the API, contents with `git fetch` + `git show`. Nothing from the PR runs.

Needs GH_TOKEN (statuses: write, pull-requests: read, contents: read) and
GITHUB_REPOSITORY. DRY_RUN=1 prints the statuses instead of posting them.

Plan: docs/plans/2026-11-01-bench-homelab-paths-plan.md
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check  # noqa: E402

BOT_ID = 334337744  # bench-cloud[bot]
GEORGE_ID = 356410  # gjcourt
PATHS = "bench-paths"
APPROVAL = "bench-approval"
DECIDING = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}


# --------------------------------------------------------------------------
# Pure decisions (unit tested)


def is_bench_pr(pr: dict, activity: list[dict], repo: str, bot_id: int = BOT_ID) -> tuple[bool, str]:
    """(is bench, why). `activity` is the repo activity list for the head ref."""
    if (pr.get("user") or {}).get("id") == bot_id:
        return True, "opened by bench-cloud[bot]"
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    if head_repo != repo:
        # The activity API can't see pushes to another repo, so fail closed.
        return True, f"head branch is in {head_repo or 'an unknown repo'}"
    for a in activity:
        if (a.get("actor") or {}).get("id") == bot_id:
            return True, f"bench-cloud[bot] pushed to the branch ({a.get('activity_type', 'push')})"
    return False, "not a bench PR"


def approval_state(reviews: list[dict], head_sha: str, george_id: int = GEORGE_ID) -> tuple[bool, str]:
    """(approved, why). George's latest deciding review must approve head_sha.

    COMMENTED reviews don't change the result. Reviews are ordered by
    submitted_at, then id, since the API's own order is not documented as
    chronological.
    """
    mine = [
        r
        for r in reviews
        if (r.get("user") or {}).get("id") == george_id and r.get("state") in DECIDING
    ]
    if not mine:
        return False, "waiting for gjcourt to approve"
    mine.sort(key=lambda r: (r.get("submitted_at") or "", r.get("id") or 0))
    last = mine[-1]
    if last["state"] != "APPROVED":
        return False, f"gjcourt's latest review is {last['state'].lower().replace('_', ' ')}"
    if last.get("commit_id") != head_sha:
        return False, f"gjcourt approved {str(last.get('commit_id'))[:7]}, not the head {head_sha[:7]}"
    return True, f"gjcourt approved {head_sha[:7]}"


def paths_description(violations: list) -> str:
    if not violations:
        return "all changed paths are allowed"
    first = violations[0]
    more = f" (+{len(violations) - 1} more)" if len(violations) > 1 else ""
    return f"{first.path}: {first.rule}{more}"


# --------------------------------------------------------------------------
# GitHub


def gh_api(path: str, method: str = "GET", fields: dict | None = None, paginate: bool = False):
    cmd = ["gh", "api", "-X", method, "-H", "Accept: application/vnd.github+json", path]
    if paginate:
        cmd += ["--paginate", "--slurp"]
    for k, v in (fields or {}).items():
        cmd += ["-f", f"{k}={v}"]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    data = json.loads(out) if out.strip() else None
    if paginate:
        return [item for page in data for item in page]
    return data


def post_status(repo: str, sha: str, context: str, state: str, description: str, target_url: str) -> None:
    description = description[:140]
    print(f"  {context} = {state}: {description}")
    if os.environ.get("DRY_RUN") == "1":
        return
    fields = {"state": state, "context": context, "description": description}
    if target_url:
        fields["target_url"] = target_url
    gh_api(f"repos/{repo}/statuses/{sha}", method="POST", fields=fields)


def fetch_head(repo_dir: str, number: int) -> str:
    ref = f"refs/bench-gate/pr-{number}"
    subprocess.run(
        ["git", "-C", repo_dir, "fetch", "--quiet", "--no-tags", "origin", f"+refs/pull/{number}/head:{ref}"],
        check=True,
        capture_output=True,
    )
    return check.git(repo_dir, "rev-parse", ref).decode().strip()


def evaluate(repo: str, number: int, repo_dir: str, rules: dict, target_url: str) -> None:
    pr = gh_api(f"repos/{repo}/pulls/{number}")
    default = pr["base"]["repo"]["default_branch"]
    if pr["state"] != "open" or pr["base"]["ref"] != default:
        print(f"PR #{number}: skipped (state={pr['state']}, base={pr['base']['ref']})")
        return
    head_sha = pr["head"]["sha"]  # read fresh, never from the event payload
    print(f"PR #{number} head {head_sha[:7]}")
    try:
        activity = []
        if ((pr["head"].get("repo") or {}).get("full_name")) == repo:
            activity = gh_api(f"repos/{repo}/activity?ref={urllib.parse.quote(pr['head']['ref'], safe='')}&per_page=100", paginate=True)
        bench, why = is_bench_pr(pr, activity, repo)
    except Exception as e:  # noqa: BLE001 - fail closed on any lookup error
        for ctx in (PATHS, APPROVAL):
            post_status(repo, head_sha, ctx, "error", f"could not tell if this is a bench PR: {e}", target_url)
        raise
    print(f"  {why}")
    if not bench:
        post_status(repo, head_sha, PATHS, "success", "not a bench PR", target_url)
        post_status(repo, head_sha, APPROVAL, "success", "not a bench PR", target_url)
        return

    try:
        fetched = fetch_head(repo_dir, number)
        if fetched != head_sha:
            post_status(repo, head_sha, PATHS, "error", "head moved while checking; the next run re-checks", target_url)
        else:
            violations = check.check_range(repo_dir, f"origin/{default}", head_sha, rules)
            for v in violations:
                print(f"    {v}")
            state = "failure" if violations else "success"
            post_status(repo, head_sha, PATHS, state, paths_description(violations), target_url)
    except Exception as e:  # noqa: BLE001
        post_status(repo, head_sha, PATHS, "error", f"path check could not run: {e}", target_url)
        print(f"  path check error: {e}", file=sys.stderr)

    try:
        reviews = gh_api(f"repos/{repo}/pulls/{number}/reviews?per_page=100", paginate=True)
        ok, why = approval_state(reviews, head_sha)
        post_status(repo, head_sha, APPROVAL, "success" if ok else "failure", why, target_url)
    except Exception as e:  # noqa: BLE001
        post_status(repo, head_sha, APPROVAL, "error", f"review lookup failed: {e}", target_url)
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pr", type=int)
    g.add_argument("--all", action="store_true")
    ap.add_argument("--repo-dir", default=".")
    args = ap.parse_args(argv)

    repo = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ.get("GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    target_url = f"{server}/{repo}/actions/runs/{run_id}" if run_id else ""
    rules = check.load_rules()

    if args.all:
        numbers = [p["number"] for p in gh_api(f"repos/{repo}/pulls?state=open&per_page=100", paginate=True)]
    else:
        numbers = [args.pr]
    failed = 0
    for n in numbers:
        try:
            evaluate(repo, n, args.repo_dir, rules, target_url)
        except Exception as e:  # noqa: BLE001 - keep going through --all
            failed += 1
            print(f"PR #{n}: error: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
