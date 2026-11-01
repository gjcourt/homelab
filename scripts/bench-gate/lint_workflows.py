#!/usr/bin/env python3
"""Fail if any workflow other than bench-gate.yaml can write commit statuses.

The bench-gate statuses are pinned to the GitHub Actions app, so a status
posted by ANY workflow in this repo would satisfy them. Only
.github/workflows/bench-gate.yaml may hold `statuses: write`, at the
workflow or job level, and no workflow may use `write-all`.

    lint_workflows.py [WORKFLOW_DIR]   # default .github/workflows

Plan: docs/plans/2026-11-01-bench-homelab-paths-plan.md
"""

from __future__ import annotations

import glob
import os
import sys

import yaml

ALLOWED = {"bench-gate.yaml"}


def permission_blocks(doc: dict):
    """(where, permissions) for the workflow and each job."""
    if not isinstance(doc, dict):
        return
    if "permissions" in doc:
        yield "workflow", doc["permissions"]
    for name, job in (doc.get("jobs") or {}).items():
        if isinstance(job, dict) and "permissions" in job:
            yield f"job {name}", job["permissions"]


def violations_for(name: str, doc) -> list[str]:
    out = []
    for where, perms in permission_blocks(doc):
        if perms == "write-all":
            out.append(f"{name}: {where} uses permissions: write-all")
        elif isinstance(perms, dict) and str(perms.get("statuses", "")).strip() == "write":
            if name not in ALLOWED:
                out.append(f"{name}: {where} has statuses: write (only bench-gate.yaml may)")
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    wf_dir = argv[0] if argv else ".github/workflows"
    files = sorted(glob.glob(os.path.join(wf_dir, "*.yaml")) + glob.glob(os.path.join(wf_dir, "*.yml")))
    if not files:
        print(f"no workflows found in {wf_dir}", file=sys.stderr)
        return 1
    problems = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except yaml.YAMLError as e:
            problems.append(f"{os.path.basename(path)}: cannot parse: {e}")
            continue
        problems += violations_for(os.path.basename(path), doc)
    for p in problems:
        print(p)
    if not problems:
        print(f"ok: {len(files)} workflows, statuses: write only in {', '.join(sorted(ALLOWED))}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
