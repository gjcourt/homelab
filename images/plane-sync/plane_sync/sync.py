"""import and reconcile: lab briefs and repo plans <-> Plane.

Ownership (docs/plans/2026-10-21-plane-lab-project-sync.md):
- Briefs: the lab repo creates items and owns title/metadata; Plane owns
  status, written back to the lab repo by one auto-merging PR.
- Plans: the repo owns everything; Plane mirrors status, and a Plane-side
  state change is reverted and reported.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import lab as labmod
from .mapping import (PROPOSED, decide_brief, lab_status_for_group, state_for_lab,
                      state_for_plan)
from .plans import Plan, plans_on_branch, proposed_plans

LAB_SOURCE = "lab"
DIFFICULTY_COLORS = {"Easy": "#16A34A", "Medium": "#F59E0B", "Hard": "#DC2626"}
DEP_COLOR = "#64748B"
PROPOSED_COLOR = "#9AA4BC"
OWNER_ROLE = 20  # Plane "Admin" at project level


@dataclass
class RepoProject:
    repo: str  # "homelab"
    identifier: str  # "HOMELAB"
    name: str  # "Homelab"
    plans_path: Path | None = None  # checkout with docs/plans, if the repo has plans
    branch: str = "master"  # default branch, for plan links


@dataclass
class Config:
    lab_root: Path
    lab_owner: str = "gjcourt"
    lab_repo: str = "lab"
    lab_branch: str = "main"
    repo_projects: list[RepoProject] = field(default_factory=list)
    owner_email: str = ""
    state_file: Path = Path("plane-sync-state.json")


@dataclass
class Report:
    created_projects: list[str] = field(default_factory=list)
    created_items: Counter = field(default_factory=Counter)
    updated_items: Counter = field(default_factory=Counter)
    brief_writes: dict[str, str] = field(default_factory=dict)  # path -> new status
    conflicts: list[str] = field(default_factory=list)
    reverted_plans: list[str] = field(default_factory=list)
    plane_only: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    proposed: list[str] = field(default_factory=list)
    relations: int = 0
    skipped_projects: list[str] = field(default_factory=list)
    tracked_by_ticket: list[str] = field(default_factory=list)
    plans_without_status: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "created_projects": self.created_projects,
            "created_items": dict(self.created_items),
            "updated_items": dict(self.updated_items),
            "brief_status_writes": len(self.brief_writes),
            "conflicts": self.conflicts,
            "reverted_plan_edits": self.reverted_plans,
            "plane_only_items": len(self.plane_only),
            "orphaned_items": self.orphaned,
            "duplicate_external_ids": self.duplicates,
            "proposed_plans": self.proposed,
            "relations_added": self.relations,
            "skipped_projects": self.skipped_projects,
            "plans_tracked_by_ticket": self.tracked_by_ticket,
            "plans_without_status": self.plans_without_status,
        }


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"briefs": {}, "plans": {}, "conflicts_reported": []}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def norm_html(value: str | None) -> str:
    """Plane stores description_html wrapped in <div>...</div>, with quotes
    unescaped and rel="noopener noreferrer" on links, so compare on a
    normalized form or every run would re-update every item. Opening an item
    in Plane's editor also re-saves it with class, data-id and target
    attributes, so keep only href."""
    v = (value or "").strip()
    if v.startswith("<div>") and v.endswith("</div>"):
        v = v[5:-6]

    def keep_href(m: re.Match) -> str:
        href = re.search(r'\shref="[^"]*"', m.group(2) or "")
        return f"<{m.group(1)}{href.group(0) if href else ''}>"

    v = re.sub(r"<([A-Za-z][A-Za-z0-9]*)(\s[^>]*)?>", keep_href, v)
    return html.unescape(v)


def inline_md(text: str) -> str:
    """Escape a markdown paragraph and render its inline code, links and bold,
    so Plane shows formatting rather than raw backticks and asterisks."""
    spans: list[str] = []

    def stash(m: re.Match) -> str:
        spans.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(spans) - 1}\x00"

    v = re.sub(r"`([^`]+)`", stash, text)
    v = html.escape(v)
    v = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', v)
    v = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", v)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], v)


def differs(item: dict, body: dict) -> dict:
    """The subset of `body` that differs from the stored item."""
    out = {}
    for k, v in body.items():
        cur = item.get(k)
        if k == "labels":
            same = sorted(cur or []) == sorted(v)
        elif k == "description_html":
            same = norm_html(cur) == norm_html(v)
        else:
            same = cur == v
        if not same:
            out[k] = v
    return out


def project_name(name: str) -> str:
    """Plane rejects special characters in project names (measured: "&" and "-" -> 400)."""
    name = name.replace("&", "and").replace("-", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]", "", name)).strip()


def brief_url(cfg: Config, b: labmod.Brief) -> str:
    return f"https://github.com/{cfg.lab_owner}/{cfg.lab_repo}/blob/{cfg.lab_branch}/{b.path.as_posix()}"


def plan_url(owner: str, p: Plan, branch: str = "master") -> str:
    if p.proposed and p.pr_url:
        return p.pr_url
    return f"https://github.com/{owner}/{p.repo}/blob/{branch}/docs/plans/{p.filename}"


def norm_title(name: str) -> str:
    """Compare a plan's H1 with a hand-made ticket's name: case, punctuation
    and a leading "Plan:" or trailing ": plan" don't count."""
    v = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    v = re.sub(r"^plan\s+", "", v)
    return re.sub(r"\s+plan$", "", v)


def brief_body(cfg: Config, b: labmod.Brief, label_ids: list[str]) -> dict:
    meta = " · ".join(x for x in (b.difficulty, b.time_commitment) if x)
    desc = (f"<p>{inline_md(b.summary)}</p>" if b.summary else "") + (
        f'<p><a href="{brief_url(cfg, b)}">Brief {b.number}</a>'
        + (f" · {html.escape(meta)}" if meta else "") + "</p>")
    return {"name": f"{b.number} · {b.title}", "description_html": desc, "labels": sorted(label_ids)}


def plan_body(owner: str, p: Plan, branch: str = "master") -> dict:
    parts = []
    if p.summary:
        parts.append(f"<p>{inline_md(p.summary)}</p>")
    link = plan_url(owner, p, branch)
    where = f"PR #{p.pr_number}" if p.proposed else f"docs/plans/{p.filename}"
    parts.append(f'<p><a href="{link}">{html.escape(where)}</a></p>')
    if p.lab:
        parts.append("<p>Implements lab " + ", ".join(p.lab) + "</p>")
    if p.superseded_by:
        parts.append(f"<p>Superseded by {html.escape(p.superseded_by)}</p>")
    return {"name": p.title, "description_html": "".join(parts)}


class Syncer:
    def __init__(self, plane, cfg: Config, gh=None, log=print):
        self.plane = plane
        self.cfg = cfg
        self.gh = gh
        self.log = log
        self.report = Report()
        self.state = load_state(cfg.state_file)
        self._projects = None
        self._owner_id = None

    # -- projects, states, labels -------------------------------------------------

    def owner_id(self) -> str | None:
        if self._owner_id is None and self.cfg.owner_email:
            for m in self.plane.workspace_members():
                if (m.get("email") or "").lower() == self.cfg.owner_email.lower():
                    self._owner_id = m["id"]
        return self._owner_id

    def ensure_project(self, name: str, identifier: str, description: str, *,
                       create: bool = True) -> str | None:
        """The project's id, or None when the bot can't work in it.

        Plane lists public projects to every workspace member, but reading their
        states or items needs project membership (403 otherwise), and a private
        project the bot isn't in isn't listed at all. With create=False (repo
        projects, which people create) a project the bot can't see or isn't a
        member of is skipped and reported: creating one would duplicate a
        private project, or fail on its taken identifier.
        """
        name = project_name(name)
        if self._projects is None:
            self._projects = {p["identifier"]: p for p in self.plane.projects()}
        if identifier in self._projects:
            p = self._projects[identifier]
            if p.get("is_member") is False:
                self.report.skipped_projects.append(
                    f"{identifier}: listed but plane-sync is not a member (add the bot as a Member)")
                return None
            return p["id"]
        if not create:
            self.report.skipped_projects.append(
                f"{identifier}: not visible to plane-sync (missing, or private without the bot as a Member)")
            return None
        p = self.plane.create_project(name, identifier, description)
        self._projects[identifier] = p
        self.report.created_projects.append(identifier)
        uid = self.owner_id()
        if uid and not self.plane.dry_run:
            members = {m["id"] for m in self.plane.project_members(p["id"])}
            if uid not in members:
                self.plane.add_member(p["id"], uid, OWNER_ROLE)
        return p["id"]

    def state_index(self, pid: str, *, want_proposed: bool = False) -> dict:
        """name -> {id, group}; also id -> group under key '_group'."""
        states = [] if str(pid).startswith("dry-") else self.plane.states(pid)
        idx = {s["name"]: {"id": s["id"], "group": s["group"]} for s in states}
        if not idx:  # dry-run on a project that doesn't exist yet: Plane's defaults
            for name, group in (("Backlog", "backlog"), ("Todo", "unstarted"),
                                ("In Progress", "started"), ("Done", "completed"),
                                ("Cancelled", "cancelled")):
                idx[name] = {"id": f"dry-{name}", "group": group}
        if want_proposed and PROPOSED not in idx:
            s = self.plane.create_state(pid, PROPOSED, "backlog", PROPOSED_COLOR)
            idx[PROPOSED] = {"id": s["id"], "group": "backlog"}
        idx["_group"] = {v["id"]: v["group"] for k, v in idx.items() if k != "_group"}
        return idx

    def label_index(self, pid: str) -> dict:
        if str(pid).startswith("dry-"):
            return {}
        return {l["name"]: l["id"] for l in self.plane.labels(pid)}

    def label_id(self, pid: str, labels: dict, name: str, color: str) -> str:
        if name not in labels:
            labels[name] = self.plane.ensure_label(pid, name, color)
        return labels[name]

    def items_by_external(self, pid: str, source_prefix: str, hand_made: list | None = None) -> dict:
        """(source, external id) -> item. Items with no external source were
        made in Plane; they go to `hand_made` if given."""
        out = {}
        if str(pid).startswith("dry-"):
            return out
        for it in self.plane.items(pid):
            src = it.get("external_source") or ""
            if not src:
                self.report.plane_only.append(it.get("name", it["id"]))
                if hand_made is not None:
                    hand_made.append(it)
                continue
            if src.startswith(source_prefix):
                key = (src, it.get("external_id"))
                if key in out:
                    self.report.duplicates.append(f"{src}:{it.get('external_id')}")
                out[key] = it
        return out

    # -- briefs -------------------------------------------------------------------

    def sync_briefs(self, *, initial: bool) -> dict:
        """Create/update brief items; on reconcile, apply the status rules.

        Returns number -> (project id, item id), for plan relations.
        """
        root = self.cfg.lab_root
        cats = {c.prefix: c for c in labmod.categories(root)}
        all_briefs = labmod.briefs(root)
        index = {}
        by_cat: dict[str, list[labmod.Brief]] = {}
        for b in all_briefs:
            by_cat.setdefault(b.category_prefix, []).append(b)
        for prefix, cat in sorted(cats.items()):
            pid = self.ensure_project(cat.name, cat.identifier,
                                      f"Lab briefs in {cat.dirname} (synced by plane-sync)")
            if pid is None:
                continue
            states = self.state_index(pid)
            labels = self.label_index(pid)
            existing = self.items_by_external(pid, LAB_SOURCE)
            seen = set()
            for b in by_cat.get(prefix, []):
                seen.add(b.number)
                lids = []
                if b.difficulty in DIFFICULTY_COLORS:
                    lids.append(self.label_id(pid, labels, b.difficulty, DIFFICULTY_COLORS[b.difficulty]))
                for d in b.depends_on:
                    lids.append(self.label_id(pid, labels, f"blocked-by:{d}", DEP_COLOR))
                body = brief_body(self.cfg, b, lids)
                it = existing.get((LAB_SOURCE, b.number))
                if it is None:
                    body.update(external_id=b.number, external_source=LAB_SOURCE,
                                state=states[state_for_lab(b.status)]["id"])
                    new = self.plane.create_item(pid, body)
                    self.report.created_items["brief"] += 1
                    self.state["briefs"][b.number] = b.status
                    index[b.number] = (pid, new["id"])
                    continue
                index[b.number] = (pid, it["id"])
                changed = differs(it, body)
                if changed:
                    self.plane.update_item(pid, it["id"], changed)
                    self.report.updated_items["brief"] += 1
                if initial:
                    self.state["briefs"].setdefault(b.number, b.status)
                    continue
                group = states["_group"].get(it.get("state"))
                if group is None:
                    continue
                plane_status = lab_status_for_group(group)
                action, value = decide_brief(b.status, plane_status,
                                             self.state["briefs"].get(b.number))
                if action == "write_repo":
                    self.report.brief_writes[b.path.as_posix()] = value
                    self.state["briefs"][b.number] = value
                elif action == "record":
                    self.state["briefs"][b.number] = value
                elif action == "conflict":
                    msg = (f"{b.number}: repo says {b.status!r}, Plane says {plane_status!r}, "
                           f"both changed since the last sync ({self.state['briefs'].get(b.number)!r})")
                    self.report.conflicts.append(msg)
                    key = f"{b.number}:{b.status}:{plane_status}"
                    if key not in self.state["conflicts_reported"]:
                        self.plane.comment(pid, it["id"], f"<p>plane-sync conflict: {html.escape(msg)}. "
                                           "Nothing was written; set the status you want on one side.</p>")
                        self.state["conflicts_reported"].append(key)
            for (src, ext), it in existing.items():
                if ext not in seen:
                    self.report.orphaned.append(f"{cat.identifier}:{ext}")
        return index

    # -- plans --------------------------------------------------------------------

    def sync_plans(self, brief_index: dict) -> None:
        owner = self.cfg.lab_owner
        for rp in self.cfg.repo_projects:
            pid = self.ensure_project(rp.name, rp.identifier,
                                      f"Plans and work for gjcourt/{rp.repo} (synced by plane-sync)",
                                      create=False)
            if pid is None or rp.plans_path is None:
                continue
            source = f"plan:{rp.repo}"
            hand_made: list[dict] = []
            existing = self.items_by_external(pid, source, hand_made)
            master = plans_on_branch(rp.repo, rp.plans_path)
            names = {p.filename for p in master}
            proposed = proposed_plans(rp.repo, self.gh, owner, names) if self.gh else []
            states = self.state_index(pid)
            seen = set()
            for p in master + proposed:
                seen.add(p.filename)
                if p.proposed:
                    self.report.proposed.append(f"{rp.repo}:{p.filename} (PR #{p.pr_number})")
                key = f"{rp.repo}:{p.filename}"
                it = existing.get((source, p.filename))
                if it is None:
                    ticket = self.tracking_ticket(rp.identifier, p, hand_made)
                    if ticket:
                        # A person or agent already made the ticket for this plan.
                        # Leave it alone: its state belongs to whoever works it.
                        self.report.tracked_by_ticket.append(f"{key} -> {ticket}")
                        continue
                try:
                    want = state_for_plan(p.status, proposed=p.proposed)
                except ValueError:
                    self.report.plans_without_status.append(key)
                    self.log(f"skip {p.filename}: unknown status {p.status!r}")
                    continue
                if want not in states:
                    # Proposed is made on first use, not in every plan project.
                    states = self.state_index(pid, want_proposed=True)
                want_id = states[want]["id"]
                body = plan_body(owner, p, rp.branch)
                if it is None:
                    body.update(external_id=p.filename, external_source=source, state=want_id)
                    new = self.plane.create_item(pid, body)
                    iid = new["id"]
                    self.report.created_items["plan"] += 1
                else:
                    iid = it["id"]
                    changed = differs(it, body)
                    current = it.get("state")
                    last_set = self.state["plans"].get(key)
                    if current != want_id:
                        changed["state"] = want_id
                        if last_set and current != last_set:
                            # Someone moved a repo-owned plan in Plane: revert and say so.
                            self.report.reverted_plans.append(key)
                            self.plane.comment(pid, iid, "<p>plane-sync: this plan's status is owned by "
                                               "the repo (its frontmatter, or its open PR). The Plane "
                                               "change was reverted.</p>")
                    if changed:
                        self.plane.update_item(pid, iid, changed)
                        self.report.updated_items["plan"] += 1
                self.state["plans"][key] = want_id
                if p.proposed and p.pr_url and not str(iid).startswith("dry-"):
                    if all(l.get("url") != p.pr_url for l in self.plane.links(pid, iid)):
                        self.plane.add_link(pid, iid, p.pr_url, f"PR #{p.pr_number}")
                for ref in p.lab:
                    target = brief_index.get(ref)
                    if not target or str(iid).startswith("dry-"):
                        continue
                    rel = self.plane.relations(pid, iid)
                    if all(r.get("issue_id") != target[1] for r in rel.get("relates_to", [])):
                        self.plane.relate(pid, iid, [target[1]])
                        self.report.relations += 1
            # Plans that left master and every open PR: the PR closed unmerged,
            # or the file was deleted. Mirror that as Cancelled.
            cancelled = states["Cancelled"]["id"]
            for (src, ext), it in existing.items():
                if ext in seen or it.get("state") == cancelled:
                    continue
                self.plane.update_item(pid, it["id"], {"state": cancelled})
                self.plane.comment(pid, it["id"], f"<p>plane-sync: this plan is no longer on {rp.branch} or "
                                   "in an open PR (closed without merge, or removed). Marked Cancelled.</p>")
                self.report.updated_items["plan"] += 1
                self.state["plans"][f"{rp.repo}:{ext}"] = cancelled

    @staticmethod
    def tracking_ticket(identifier: str, p: Plan, hand_made: list[dict]) -> str | None:
        """The hand-made ticket in this project that already tracks plan `p`:
        one the plan names ("Ticket: FAX-3", or its PR's "docs(FAX-3): ..."),
        else one whose name is the plan's title."""
        by_seq = {str(it.get("sequence_id")): it for it in hand_made}
        for t in p.tickets:
            ident, _, seq = t.rpartition("-")
            if ident == identifier and seq in by_seq:
                return t
        want = norm_title(p.title)
        for it in hand_made:
            if want and norm_title(it.get("name", "")) == want:
                return f"{identifier}-{it.get('sequence_id')}"
        return None

    # -- lab write-back -------------------------------------------------------------

    def apply_brief_writes(self, root: Path) -> list[Path]:
        """Edit the lab checkout at `root`. Returns the files changed."""
        if not self.report.brief_writes:
            return []
        changed = set()
        for rel, status in sorted(self.report.brief_writes.items()):
            path = root / rel
            text = path.read_text(encoding="utf-8")
            new = labmod.set_frontmatter_status(text, status)
            if new != text:
                path.write_text(new, encoding="utf-8")
                changed.add(path)
            number = Path(rel).name[:6]
            pm = path.parent / "projects.md"
            ptext = pm.read_text(encoding="utf-8")
            pnew = labmod.set_projects_row_status(ptext, number, status)
            if pnew != ptext:
                pm.write_text(pnew, encoding="utf-8")
                changed.add(pm)
        readme = root / "README.md"
        rtext = readme.read_text(encoding="utf-8")
        rnew = labmod.recount_readme(rtext, [b.status for b in labmod.briefs(root)])
        if rnew != rtext:
            readme.write_text(rnew, encoding="utf-8")
            changed.add(readme)
        return sorted(changed)

    def run(self, *, initial: bool) -> Report:
        index = self.sync_briefs(initial=initial)
        self.sync_plans(index)
        return self.report

    def save(self) -> None:
        if not self.plane.dry_run:
            save_state(self.cfg.state_file, self.state)
