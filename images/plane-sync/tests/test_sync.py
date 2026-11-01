"""Syncer against an in-memory fake Plane and fake GitHub."""

import itertools
from pathlib import Path

from plane_sync import lab
from plane_sync.sync import Config, RepoProject, Syncer

GROUPS = [("Backlog", "backlog"), ("Todo", "unstarted"), ("In Progress", "started"),
          ("Done", "completed"), ("Cancelled", "cancelled")]


class FakePlane:
    dry_run = False

    def __init__(self):
        self.ids = (f"id{n}" for n in itertools.count())
        self.proj, self.st, self.lab, self.its, self.rel = {}, {}, {}, {}, {}
        self.comments, self.links_, self.members = [], {}, {}
        self.requests = 0

    def _id(self):
        return next(self.ids)

    def projects(self):
        return list(self.proj.values())

    def create_project(self, name, identifier, description, is_member=True):
        p = {"id": self._id(), "name": name, "identifier": identifier, "is_member": is_member}
        self.proj[identifier] = p
        self.st[p["id"]] = [{"id": self._id(), "name": n, "group": g} for n, g in GROUPS]
        self.lab[p["id"]], self.its[p["id"]], self.members[p["id"]] = {}, {}, ["creator"]
        return p

    def workspace_members(self):
        return [{"id": "george", "email": "g@example.com"}]

    def project_members(self, pid):
        return [{"id": m} for m in self.members[pid]]

    def add_member(self, pid, uid, role):
        self.members[pid].append(uid)

    def _member(self, pid):
        if not next(p for p in self.proj.values() if p["id"] == pid)["is_member"]:
            raise AssertionError(f"403: not a member of {pid}")

    def states(self, pid):
        self._member(pid)
        return self.st[pid]

    def create_state(self, pid, name, group, color):
        s = {"id": self._id(), "name": name, "group": group}
        self.st[pid].append(s)
        return s

    def labels(self, pid):
        return [{"name": n, "id": i} for n, i in self.lab[pid].items()]

    def ensure_label(self, pid, name, color):
        return self.lab[pid].setdefault(name, self._id())

    def items(self, pid):
        self._member(pid)
        return list(self.its[pid].values())

    def create_item(self, pid, body):
        for it in self.its[pid].values():
            if (it.get("external_source"), it.get("external_id")) == (body.get("external_source"), body.get("external_id")):
                return {"id": it["id"], "_existing": True}
        it = dict(body, id=self._id(), sequence_id=len(self.its[pid]) + 1)
        self.its[pid][it["id"]] = it
        return it

    def update_item(self, pid, iid, body):
        self.its[pid][iid].update(body)
        return self.its[pid][iid]

    def relations(self, pid, iid):
        return {"relates_to": [{"issue_id": x} for x in self.rel.get(iid, [])]}

    def relate(self, pid, iid, others, kind="relates_to"):
        self.rel.setdefault(iid, []).extend(others)

    def links(self, pid, iid):
        return [{"url": u} for u in self.links_.get(iid, [])]

    def add_link(self, pid, iid, url, title):
        self.links_.setdefault(iid, []).append(url)

    def comment(self, pid, iid, html):
        self.comments.append((iid, html))

    # helpers for tests
    def item(self, ident, ext):
        pid = self.proj[ident]["id"]
        return pid, next(i for i in self.its[pid].values() if i.get("external_id") == ext)

    def state_id(self, ident, name):
        pid = self.proj[ident]["id"]
        return next(s["id"] for s in self.st[pid] if s["name"] == name)


class FakeGH:
    def __init__(self, pulls=None):
        self.pulls = pulls or []

    def open_pulls(self, owner, repo):
        return [p for p in self.pulls if p["repo"] == repo]

    def pull_files(self, owner, repo, number):
        return next(p for p in self.pulls if p["number"] == number)["files"]

    def file_at(self, owner, repo, path, ref):
        for p in self.pulls:
            for f in p["files"]:
                if f["filename"] == path:
                    return f["text"]
        raise KeyError(path)


PLAN = """---
status: {status}
last_modified: 2026-10-21
summary: "{summary}"
{extra}---

# {title}

Body.
"""


def homelab(tmp: Path, plans: dict) -> Path:
    d = tmp / "homelab/docs/plans"
    d.mkdir(parents=True)
    (d / "README.md").write_text("# index\n")
    for name, (status, title, extra) in plans.items():
        (d / name).write_text(PLAN.format(status=status, summary=f"About {title}", title=title, extra=extra))
    return tmp / "homelab"


def make(lab_root, tmp_path, gh=None, plans=None, plane=None):
    hl = homelab(tmp_path, plans or {"2026-01-01-ip.md": ("planned", "IP plan", "lab: 03-002\n")})
    cfg = Config(lab_root=lab_root, owner_email="g@example.com", state_file=tmp_path / "state.json",
                 repo_projects=[RepoProject("homelab", "HOMELAB", "Homelab", hl),
                                RepoProject("domestique", "DOMESTIQUE", "Domestique", None)])
    if plane is not None:  # repo projects are made by people, not by plane-sync
        for rp in cfg.repo_projects:
            plane.create_project(rp.name, rp.identifier, "")
    return cfg


def fresh(lab_root, tmp_path, **kw):
    plane = FakePlane()
    return plane, make(lab_root, tmp_path, plane=plane, **kw)


def test_import_creates_everything_and_is_idempotent(lab_root, tmp_path):
    plane, cfg = fresh(lab_root, tmp_path)
    s = Syncer(plane, cfg, gh=FakeGH(), log=lambda m: None)
    r = s.run(initial=True)
    s.save()
    assert sorted(r.created_projects) == ["AUTO", "COFFEE"]
    assert r.created_items == {"brief": 3, "plan": 1}
    pid, it = plane.item("AUTO", "03-002")
    assert it["state"] == plane.state_id("AUTO", "Backlog")
    assert it["name"] == "03-002 · IP Cleanup"
    assert "george" in plane.members[pid]
    assert r.skipped_projects == []
    _, done = plane.item("AUTO", "03-001")
    assert done["state"] == plane.state_id("AUTO", "Done")
    # plan relates to its brief (cross-project)
    _, plan = plane.item("HOMELAB", "2026-01-01-ip.md")
    assert plane.rel[plan["id"]] == [it["id"]]
    assert plan["state"] == plane.state_id("HOMELAB", "Todo")
    # second run: nothing new
    s2 = Syncer(plane, cfg, gh=FakeGH(), log=lambda m: None)
    r2 = s2.run(initial=True)
    assert r2.created_items == {} and r2.created_projects == [] and r2.relations == 0
    assert r2.updated_items == {}


def test_reconcile_plane_status_writes_back(lab_root, tmp_path):
    plane, cfg = fresh(lab_root, tmp_path)
    s = Syncer(plane, cfg, gh=FakeGH(), log=lambda m: None)
    s.run(initial=True)
    s.save()
    pid, it = plane.item("AUTO", "03-002")
    plane.update_item(pid, it["id"], {"state": plane.state_id("AUTO", "Cancelled")})
    s2 = Syncer(plane, cfg, gh=FakeGH(), log=lambda m: None)
    r = s2.run(initial=False)
    assert r.brief_writes == {"03-homelab-automation/03-002-ip-cleanup.md": "Cancelled"}
    files = s2.apply_brief_writes(lab_root)
    assert {f.name for f in files} == {"03-002-ip-cleanup.md", "projects.md", "README.md"}
    assert lab.parse_brief(lab_root, Path("03-homelab-automation/03-002-ip-cleanup.md")).status == "Cancelled"
    assert "| Cancelled   |" in (lab_root / "03-homelab-automation/projects.md").read_text()
    assert "3 briefs (1 done, 1 in progress, 1 cancelled)" in (lab_root / "README.md").read_text()


def test_reconcile_conflict_writes_nothing(lab_root, tmp_path):
    plane, cfg = fresh(lab_root, tmp_path)
    s = Syncer(plane, cfg, gh=FakeGH(), log=lambda m: None)
    s.run(initial=True)
    s.save()
    pid, it = plane.item("AUTO", "03-002")
    plane.update_item(pid, it["id"], {"state": plane.state_id("AUTO", "In Progress")})
    p = lab_root / "03-homelab-automation/03-002-ip-cleanup.md"
    p.write_text(lab.set_frontmatter_status(p.read_text(), "Done"))
    s2 = Syncer(plane, cfg, gh=FakeGH(), log=lambda m: None)
    r = s2.run(initial=False)
    assert r.brief_writes == {}
    assert len(r.conflicts) == 1 and "03-002" in r.conflicts[0]
    assert len(plane.comments) == 1
    # reported once only
    s2.save()
    s3 = Syncer(plane, cfg, gh=FakeGH(), log=lambda m: None)
    s3.run(initial=False)
    assert len(plane.comments) == 1


def test_open_pr_plan_is_proposed_then_follows_frontmatter(lab_root, tmp_path):
    plane = FakePlane()
    pr = {"repo": "homelab", "number": 1574, "html_url": "https://github.com/gjcourt/homelab/pull/1574",
          "head": {"sha": "abc"}, "files": [{
              "filename": "docs/plans/2026-10-12-music-assistant.md", "status": "added",
              "text": PLAN.format(status="planned", summary="MA", title="Music Assistant", extra="")}]}
    cfg = make(lab_root, tmp_path, plane=plane)
    s = Syncer(plane, cfg, gh=FakeGH([pr]), log=lambda m: None)
    r = s.run(initial=True)
    s.save()
    pid, it = plane.item("HOMELAB", "2026-10-12-music-assistant.md")
    assert it["state"] == plane.state_id("HOMELAB", "Proposed")
    assert plane.links_[it["id"]] == [pr["html_url"]]
    assert r.proposed == ["homelab:2026-10-12-music-assistant.md (PR #1574)"]
    # PR merges: the file lands on master with its frontmatter status
    d = cfg.repo_projects[0].plans_path / "docs/plans"
    (d / "2026-10-12-music-assistant.md").write_text(pr["files"][0]["text"].replace("planned", "in-progress"))
    s2 = Syncer(plane, cfg, gh=FakeGH([]), log=lambda m: None)
    s2.run(initial=False)
    assert plane.its[pid][it["id"]]["state"] == plane.state_id("HOMELAB", "In Progress")


def test_pr_closed_unmerged_cancels(lab_root, tmp_path):
    plane = FakePlane()
    pr = {"repo": "homelab", "number": 9, "html_url": "u", "head": {"sha": "s"}, "files": [{
        "filename": "docs/plans/2026-02-02-x.md", "status": "added",
        "text": PLAN.format(status="planned", summary="x", title="X", extra="")}]}
    cfg = make(lab_root, tmp_path, plane=plane)
    Syncer(plane, cfg, gh=FakeGH([pr]), log=lambda m: None).run(initial=True)
    Syncer(plane, cfg, gh=FakeGH([]), log=lambda m: None).run(initial=False)
    pid, it = plane.item("HOMELAB", "2026-02-02-x.md")
    assert it["state"] == plane.state_id("HOMELAB", "Cancelled")
    assert any("no longer on master" in c[1] for c in plane.comments)


def test_plane_side_plan_edit_is_reverted(lab_root, tmp_path):
    plane, cfg = fresh(lab_root, tmp_path)
    s = Syncer(plane, cfg, gh=FakeGH(), log=lambda m: None)
    s.run(initial=True)
    s.save()
    pid, it = plane.item("HOMELAB", "2026-01-01-ip.md")
    plane.update_item(pid, it["id"], {"state": plane.state_id("HOMELAB", "Done")})
    s2 = Syncer(plane, cfg, gh=FakeGH(), log=lambda m: None)
    r = s2.run(initial=False)
    assert r.reverted_plans == ["homelab:2026-01-01-ip.md"]
    assert plane.its[pid][it["id"]]["state"] == plane.state_id("HOMELAB", "Todo")
