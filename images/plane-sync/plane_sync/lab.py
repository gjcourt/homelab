"""Read lab briefs and make the three status edits the sync PR carries.

Every edit is line-local so an unchanged file stays byte-identical, and an
edited one passes the lab repo's prettier (printWidth 100, proseWrap always)
and scripts/check_invariants.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .mapping import LAB_STATUSES

CATEGORY_DIR_RE = re.compile(r"^(\d{2})-[a-z0-9-]+$")
BRIEF_FILE_RE = re.compile(r"^(\d{2})-(\d{3})-.+\.md$")
STATUS_LINE_RE = re.compile(r"^status:\s*(['\"]?)(.*?)\1\s*$")
PROJECTS_HEADING_RE = re.compile(r"^# (.+?) Projects \((\d+)\)\s*$", re.MULTILINE)
README_STATUS_RE = re.compile(
    r"(\d+) briefs \((\d+) done, (\d+) in progress(?:, (\d+) cancelled)?\)"
)
PRINT_WIDTH = 100

# Plane project identifiers: at most 12 letters/digits (measured on staging).
CATEGORY_IDENTIFIERS = {
    "01": "AUDIO",
    "02": "WOOD",
    "03": "AUTO",
    "04": "FIN",
    "05": "PIANO",
    "06": "COFFEE",
    "07": "CROSS",
}


@dataclass
class Category:
    prefix: str  # "03"
    dirname: str  # "03-homelab-automation"
    name: str  # "Homelab & Automation"
    identifier: str  # "AUTO"


@dataclass
class Brief:
    number: str  # "03-043"
    path: Path  # relative to the repo root
    title: str
    status: str
    difficulty: str
    time_commitment: str
    depends_on: list[str] = field(default_factory=list)
    summary: str = ""

    @property
    def category_prefix(self) -> str:
        return self.number[:2]


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated frontmatter")
    return text[4 : end + 1], text[end + 5 :]


def first_description_paragraph(body: str) -> str:
    m = re.search(r"^## Description\s*\n(.*?)(?:\n\s*\n|\Z)", body, re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    return " ".join(line.strip() for line in m.group(1).strip().splitlines())


def parse_brief(root: Path, rel: Path) -> Brief:
    text = (root / rel).read_text(encoding="utf-8")
    fm_text, body = split_frontmatter(text)
    fm = yaml.safe_load(fm_text) or {}
    deps = fm.get("depends_on") or []
    if isinstance(deps, str):
        deps = [deps]
    return Brief(
        number=str(fm["number"]),
        path=rel,
        title=str(fm["title"]),
        status=str(fm["status"]),
        difficulty=str(fm.get("difficulty", "")),
        time_commitment=str(fm.get("time_commitment", "")),
        depends_on=[str(d) for d in deps],
        summary=first_description_paragraph(body),
    )


def categories(root: Path) -> list[Category]:
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and CATEGORY_DIR_RE.match(p.name)):
        prefix = d.name[:2]
        heading = PROJECTS_HEADING_RE.search((d / "projects.md").read_text(encoding="utf-8"))
        name = heading.group(1) if heading else d.name[3:].replace("-", " ").title()
        out.append(Category(prefix, d.name, name, CATEGORY_IDENTIFIERS.get(prefix, f"CAT{prefix}")))
    return out


def briefs(root: Path) -> list[Brief]:
    out = []
    for cat in categories(root):
        for f in sorted((root / cat.dirname).iterdir()):
            if f.is_file() and BRIEF_FILE_RE.match(f.name):
                out.append(parse_brief(root, f.relative_to(root)))
    return out


# --- edits -----------------------------------------------------------------


def set_frontmatter_status(text: str, status: str) -> str:
    """Rewrite only the `status:` line, keeping its quote style."""
    if status not in LAB_STATUSES:
        raise ValueError(f"invalid lab status {status!r}")
    fm_text, _ = split_frontmatter(text)
    lines = text.split("\n")
    fm_end = len(fm_text.split("\n"))  # lines 1..fm_end-1 are frontmatter
    for i in range(1, fm_end):
        m = STATUS_LINE_RE.match(lines[i])
        if m:
            quote = m.group(1) or "'"
            new = f"status: {quote}{status}{quote}"
            if lines[i] == new:
                return text
            lines[i] = new
            return "\n".join(lines)
    raise ValueError("no status line in frontmatter")


def set_projects_row_status(text: str, number: str, status: str) -> str:
    """Set the Status cell of one projects.md row, keeping column widths.

    prettier pads every cell to its column's width. Every lab status is at most
    as wide as "Not Started"/"In Progress" (11), which is the minimum the
    column already has, so re-padding to the existing cell width keeps the
    table byte-for-byte prettier-clean.
    """
    lines = text.split("\n")
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("|") and "Status" in line and "Number" in line:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("no projects table")
    header = [c.strip() for c in lines[header_idx].strip("|").split("|")]
    col = header.index("Status")
    for i in range(header_idx + 2, len(lines)):
        line = lines[i]
        if not line.startswith("|"):
            break
        cells = line.split("|")[1:-1]
        if cells[0].strip() == number:
            width = len(cells[col]) - 2  # one space of padding each side
            if len(status) > width:
                raise ValueError(f"status {status!r} wider than column ({width})")
            cells[col] = f" {status.ljust(width)} "
            lines[i] = "|" + "|".join(cells) + "|"
            return "\n".join(lines)
    raise ValueError(f"row {number} not found")


def wrap_paragraph(text: str, width: int = PRINT_WIDTH) -> str:
    """Greedy fill, the way prettier's proseWrap: always lays out prose."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def recount_readme(text: str, statuses: list[str]) -> str:
    """Recompute the README Status line counts and rewrap its paragraph."""
    total = len(statuses)
    done = statuses.count("Done")
    prog = statuses.count("In Progress")
    canc = statuses.count("Cancelled")
    m = README_STATUS_RE.search(text)
    if not m:
        raise ValueError("README Status line not found")
    new = f"{total} briefs ({done} done, {prog} in progress" + (
        f", {canc} cancelled)" if canc else ")"
    )
    if m.group(0) == new:
        return text
    # Rewrap the whole paragraph containing the match.
    start = text.rfind("\n\n", 0, m.start())
    start = 0 if start < 0 else start + 2
    end = text.find("\n\n", m.end())
    end = len(text) if end < 0 else end
    para = text[start:end].replace(m.group(0), new)
    return text[:start] + wrap_paragraph(" ".join(para.split("\n"))) + text[end:]
