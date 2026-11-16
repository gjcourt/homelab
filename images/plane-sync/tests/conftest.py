import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

README = """<!-- readme-type: content -->

# lab

A curated list of 3 multidisciplinary engineering and skill-building projects

Intro paragraph.

**Status:** active — 3 briefs (1 done, 1 in progress), last content update 2026-10-08; the
espresso profiling build is installed on the machine and its controller app, Vibrato, runs in the
homelab (see [`06-coffee-espresso/AGENTS.md`](06-coffee-espresso/AGENTS.md)).

## More
"""

PROJECTS_03 = """# Homelab & Automation Projects (2)

This category focuses on infrastructure.

## Projects Index

| Number | Project                                  | Status      | Difficulty | Time Commitment |
| ------ | ---------------------------------------- | ----------- | ---------- | --------------- |
| 03-001 | [eBPF Analyzer](03-001-ebpf-analyzer.md) | Done        | Hard       | Months          |
| 03-002 | [IP Cleanup](03-002-ip-cleanup.md)       | Not Started | Medium     | 1-2 weeks       |
"""

PROJECTS_06 = """# Coffee & Espresso Projects (1)

Espresso.

## Projects Index

| Number | Project                          | Status      | Difficulty | Time Commitment |
| ------ | -------------------------------- | ----------- | ---------- | --------------- |
| 06-001 | [Profiling](06-001-profiling.md) | In Progress | Hard       | 2-4 wk          |
"""


def brief(number, title, status, difficulty="Medium", deps=None):
    dep = "".join(f"\n  - {d}" for d in (deps or []))
    return f"""---
title: '{title}'
number: '{number}'
category: 'x'
difficulty: '{difficulty}'
time_commitment: '1-2 weeks'
target_skills:
  'Skills that wrap onto a second line because the frontmatter value is long enough to wrap
  here'
status: '{status}'
depends_on:{dep or ' []'}
---

# {title}

## Description

First paragraph of {title}, which becomes the
Plane description.

Second paragraph.

## Exit Criteria

- [ ] done
"""


@pytest.fixture
def lab_root(tmp_path: Path) -> Path:
    root = tmp_path / "lab"
    (root / "03-homelab-automation").mkdir(parents=True)
    (root / "06-coffee-espresso").mkdir()
    (root / "README.md").write_text(README)
    (root / "03-homelab-automation/projects.md").write_text(PROJECTS_03)
    (root / "06-coffee-espresso/projects.md").write_text(PROJECTS_06)
    (root / "03-homelab-automation/03-001-ebpf-analyzer.md").write_text(
        brief("03-001", "eBPF Analyzer", "Done", "Hard"))
    (root / "03-homelab-automation/03-002-ip-cleanup.md").write_text(
        brief("03-002", "IP Cleanup", "Not Started", deps=["homelab/cilium"]))
    (root / "06-coffee-espresso/06-001-profiling.md").write_text(
        brief("06-001", "Profiling", "In Progress", "Hard"))
    return root


@pytest.fixture
def real_lab():
    """A real gjcourt/lab checkout, if LAB_PATH points at one (CI clones it)."""
    p = os.environ.get("LAB_PATH")
    if not p or not (Path(p) / "scripts/check_invariants.py").exists():
        pytest.skip("LAB_PATH not set to a gjcourt/lab checkout")
    return Path(p)


@pytest.fixture
def lab_copy(real_lab, tmp_path):
    dst = tmp_path / "labcopy"
    shutil.copytree(real_lab, dst, ignore=shutil.ignore_patterns(".git", "node_modules"))
    return dst


def run_invariants(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "scripts/check_invariants.py"], cwd=root,
                          capture_output=True, text=True)
