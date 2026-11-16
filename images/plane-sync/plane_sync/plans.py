"""Read repo plans (docs/plans/*.md), on master and in open PRs.

For plans the repo owns status; Plane only mirrors it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PLAN_DIR = "docs/plans"
H1_RE = re.compile(r"^# (.+?)\s*$", re.MULTILINE)
# "Ticket: PLANEMOBILE-4", "Ticket: [TOPPINGCTL-2](https://...)": the Plane
# ticket a plan was written for, as the agent-desk plans in other repos state it
# (at a line start or mid-paragraph). Capital T and singular, so "Related
# tickets: ..." doesn't count.
TICKET_RE = re.compile(r"(?<![A-Za-z])Ticket:\s*\[?([A-Z][A-Z0-9]*-\d+)\b")
# A conventional-commit scope naming a ticket: "docs(PLANEMOBILE-26): ...".
PR_TICKET_RE = re.compile(r"^\w+\(([A-Z][A-Z0-9]*-\d+)\)!?:")


@dataclass
class Plan:
    repo: str  # "homelab"
    filename: str  # "2026-10-12-music-assistant.md"
    title: str
    status: str  # frontmatter status ("planned", ...); "" for a proposed plan without one
    summary: str
    lab: list[str] = field(default_factory=list)  # briefs this plan implements
    superseded_by: str = ""
    proposed: bool = False  # only in an open PR, not on the default branch yet
    pr_number: int | None = None
    pr_url: str = ""
    tickets: list[str] = field(default_factory=list)  # Plane tickets the plan names, "FAX-3"

    @property
    def external_source(self) -> str:
        return f"plan:{self.repo}"


def parse_frontmatter(text: str) -> dict[str, str]:
    """Same tolerant key: value parse that scripts/plans-index uses."""
    out: dict[str, str] = {}
    if not text.startswith("---\n"):
        return out
    end = text.find("\n---", 4)
    if end < 0:
        return out
    for line in text[4:end].split("\n"):
        key, sep, value = line.partition(":")
        if not sep or line[:1].isspace():
            continue
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def parse_lab_refs(value: str) -> list[str]:
    """`lab: 03-043` or `lab: [03-043, 01-016]`."""
    return re.findall(r"\b\d{2}-\d{3}\b", value or "")


def parse_plan(repo: str, filename: str, text: str) -> Plan:
    fm = parse_frontmatter(text)
    h1 = H1_RE.search(text)
    return Plan(
        repo=repo,
        filename=filename,
        title=h1.group(1) if h1 else filename.removesuffix(".md"),
        status=fm.get("status", ""),
        summary=fm.get("summary", ""),
        lab=parse_lab_refs(fm.get("lab", "")),
        superseded_by=fm.get("superseded_by", ""),
        tickets=TICKET_RE.findall(text),
    )


def plans_on_branch(repo: str, root: Path) -> list[Plan]:
    d = root / PLAN_DIR
    out = []
    if not d.is_dir():  # a repo with no plans yet (modemscope)
        return out
    for f in sorted(d.glob("*.md")):
        if f.name == "README.md":
            continue
        out.append(parse_plan(repo, f.name, f.read_text(encoding="utf-8")))
    return out


def proposed_plans(repo: str, gh, owner: str, existing: set[str]) -> list[Plan]:
    """Plans added (not just modified) by open PRs, i.e. not on master yet."""
    out = []
    for pr in gh.open_pulls(owner, repo):
        for f in gh.pull_files(owner, repo, pr["number"]):
            name = f["filename"]
            if not (name.startswith(PLAN_DIR + "/") and name.endswith(".md")):
                continue
            base = name.rsplit("/", 1)[1]
            if base == "README.md" or base in existing or f["status"] != "added":
                continue
            text = gh.file_at(owner, repo, name, pr["head"]["sha"])
            p = parse_plan(repo, base, text)
            p.proposed = True
            p.pr_number = pr["number"]
            p.pr_url = pr["html_url"]
            m = PR_TICKET_RE.match(pr.get("title") or "")
            if m and m.group(1) not in p.tickets:
                p.tickets.append(m.group(1))
            out.append(p)
    return out
