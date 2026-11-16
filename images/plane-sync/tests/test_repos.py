"""Plans from every active repo: projects the bot can't work in, plans that
already have a hand-made ticket, plans without a status, repos without plans."""

from pathlib import Path

from plane_sync import cli
from plane_sync.plans import parse_plan
from plane_sync.sync import Config, RepoProject, Syncer, norm_title
from test_sync import PLAN, FakeGH, FakePlane

# How the agent-desk plans in the other repos look: no frontmatter, a Ticket line.
NO_FM = """# {title}

Ticket: {ticket}. Status: proposed.

Body.
"""


def repo(tmp: Path, name: str, plans: dict[str, str]) -> Path:
    root = tmp / name
    d = root / "docs/plans"
    d.mkdir(parents=True)
    for fname, text in plans.items():
        (d / fname).write_text(text)
    return root


def cfg_for(lab_root, tmp_path, *rps) -> Config:
    return Config(lab_root=lab_root, owner_email="g@example.com",
                  state_file=tmp_path / "state.json", repo_projects=list(rps))


def hand_made(plane, ident, name):
    pid = plane.proj[ident]["id"]
    seq = len(plane.its[pid]) + 1
    iid = plane._id()
    plane.its[pid][iid] = {"id": iid, "name": name, "sequence_id": seq, "state": None}
    return f"{ident}-{seq}"


def run(plane, cfg, gh=None):
    s = Syncer(plane, cfg, gh=gh or FakeGH(), log=lambda m: None)
    return s.run(initial=False)


def test_missing_repo_project_is_skipped_not_created(lab_root, tmp_path):
    plane = FakePlane()
    root = repo(tmp_path, "fax", {"2026-10-30-fax.md": PLAN.format(
        status="planned", summary="s", title="Fax", extra="")})
    r = run(plane, cfg_for(lab_root, tmp_path, RepoProject("fax", "FAX", "fax", root, "main")))
    assert "FAX" not in plane.proj
    assert "FAX" not in r.created_projects
    assert r.skipped_projects == [
        "FAX: not visible to plane-sync (missing, or private without the bot as a Member)"]
    assert "plan" not in r.created_items


def test_listed_project_without_membership_is_skipped(lab_root, tmp_path):
    plane = FakePlane()
    plane.create_project("fax", "FAX", "", is_member=False)
    root = repo(tmp_path, "fax", {"2026-10-30-fax.md": PLAN.format(
        status="planned", summary="s", title="Fax", extra="")})
    # FakePlane raises on states/items for a non-member, as Plane answers 403.
    r = run(plane, cfg_for(lab_root, tmp_path, RepoProject("fax", "FAX", "fax", root, "main")))
    assert r.skipped_projects == ["FAX: listed but plane-sync is not a member (add the bot as a Member)"]
    assert plane.its[plane.proj["FAX"]["id"]] == {}


def test_plan_naming_its_ticket_is_not_duplicated(lab_root, tmp_path):
    plane = FakePlane()
    plane.create_project("Plane Mobile", "PLANEMOBILE", "")
    t = hand_made(plane, "PLANEMOBILE", "Write the architecture plan for the backend and PWA")
    root = repo(tmp_path, "plane-mobile", {
        "2026-10-25-architecture-plan.md": NO_FM.format(title="plane-mobile — architecture plan", ticket=t)})
    rp = RepoProject("plane-mobile", "PLANEMOBILE", "Plane Mobile", root, "main")
    r = run(plane, cfg_for(lab_root, tmp_path, rp))
    assert r.tracked_by_ticket == [f"plane-mobile:2026-10-25-architecture-plan.md -> {t}"]
    assert len(plane.its[plane.proj["PLANEMOBILE"]["id"]]) == 1
    assert r.plans_without_status == []  # tracked plans aren't also reported as status-less


def test_open_pr_plan_with_ticket_in_pr_title_is_not_duplicated(lab_root, tmp_path):
    """PLANEMOBILE-26 and plane-mobile PR #30: the PR title names the ticket."""
    plane = FakePlane()
    plane.create_project("Plane Mobile", "PLANEMOBILE", "")
    t = hand_made(plane, "PLANEMOBILE", "Plan: approve a PR from the phone (approve-to-merge)")
    root = repo(tmp_path, "plane-mobile", {})
    pr = {"repo": "plane-mobile", "number": 30, "title": f"docs({t}): plan for approving a PR from the phone",
          "html_url": "https://github.com/gjcourt/plane-mobile/pull/30", "head": {"sha": "abc"},
          "files": [{"filename": "docs/plans/2026-10-31-approve-from-phone-plan.md", "status": "added",
                     "text": "# Approving a PR from the phone\n\nBody.\n"}]}
    rp = RepoProject("plane-mobile", "PLANEMOBILE", "Plane Mobile", root, "main")
    r = run(plane, cfg_for(lab_root, tmp_path, rp), FakeGH([pr]))
    assert r.tracked_by_ticket == [f"plane-mobile:2026-10-31-approve-from-phone-plan.md -> {t}"]
    pid = plane.proj["PLANEMOBILE"]["id"]
    assert len(plane.its[pid]) == 1
    assert "Proposed" not in {s["name"] for s in plane.st[pid]}
    assert plane.links_ == {}


def test_plan_matching_a_ticket_title_is_not_duplicated(lab_root, tmp_path):
    plane = FakePlane()
    plane.create_project("toppingctl", "TOPPINGCTL", "")
    t = hand_made(plane, "TOPPINGCTL", "Plan: networked, multi-room toppingctl")
    root = repo(tmp_path, "toppingctl", {"2026-10-26-web-ui.md": PLAN.format(
        status="planned", summary="s", title="Networked, multi-room toppingctl: plan", extra="")})
    r = run(plane, cfg_for(lab_root, tmp_path, RepoProject("toppingctl", "TOPPINGCTL", "t", root, "main")))
    assert r.tracked_by_ticket == [f"toppingctl:2026-10-26-web-ui.md -> {t}"]
    assert "plan" not in r.created_items


def test_ticket_in_another_project_does_not_count(lab_root, tmp_path):
    plane = FakePlane()
    plane.create_project("Homelab", "HOMELAB", "")
    hand_made(plane, "HOMELAB", "Something else")  # HOMELAB-1, unrelated
    root = repo(tmp_path, "homelab", {"2026-11-01-x.md": PLAN.format(
        status="planned", summary="s", title="X", extra="") + "\nTicket: HASS-1\n"})
    r = run(plane, cfg_for(lab_root, tmp_path, RepoProject("homelab", "HOMELAB", "Homelab", root)))
    assert r.tracked_by_ticket == []
    assert r.created_items["plan"] == 1


def test_plan_without_status_is_reported_not_created(lab_root, tmp_path):
    plane = FakePlane()
    plane.create_project("fax", "FAX", "")
    root = repo(tmp_path, "fax", {"2026-10-30-fax-sending-plan.md": "# Fax: how a fax gets sent\n\nBody.\n"})
    r = run(plane, cfg_for(lab_root, tmp_path, RepoProject("fax", "FAX", "fax", root, "main")))
    assert r.plans_without_status == ["fax:2026-10-30-fax-sending-plan.md"]
    assert "plan" not in r.created_items
    # nothing is cancelled for it on the next run either
    r2 = run(plane, cfg_for(lab_root, tmp_path, RepoProject("fax", "FAX", "fax", root, "main")))
    assert r2.updated_items == {}


def test_repo_without_plans_dir_syncs_nothing(lab_root, tmp_path):
    plane = FakePlane()
    plane.create_project("modemscope", "MODEMSCOPE", "")
    root = tmp_path / "modemscope"
    root.mkdir()
    r = run(plane, cfg_for(lab_root, tmp_path, RepoProject("modemscope", "MODEMSCOPE", "m", root, "main")))
    pid = plane.proj["MODEMSCOPE"]["id"]
    assert plane.its[pid] == {} and r.skipped_projects == []
    assert "Proposed" not in {s["name"] for s in plane.st[pid]}  # no state made when unused


def test_plan_link_uses_the_default_branch(lab_root, tmp_path):
    plane = FakePlane()
    plane.create_project("fax", "FAX", "")
    root = repo(tmp_path, "fax", {"2026-10-30-fax.md": PLAN.format(
        status="planned", summary="s", title="Fax", extra="")})
    run(plane, cfg_for(lab_root, tmp_path, RepoProject("fax", "FAX", "fax", root, "main")))
    _, it = plane.item("FAX", "2026-10-30-fax.md")
    assert "github.com/gjcourt/fax/blob/main/docs/plans/2026-10-30-fax.md" in it["description_html"]


def test_ticket_parsing():
    p = parse_plan("t", "x.md", "# T\n\nTicket: [TOPPINGCTL-2](https://plane/x). Status: done\n")
    assert p.tickets == ["TOPPINGCTL-2"]
    assert parse_plan("t", "x.md", "# T\n\nTicket: DOMESTIQUE-36 (plan ref 4.14)\n").tickets == ["DOMESTIQUE-36"]
    assert parse_plan("t", "x.md", "# T\n\nNo code is added by this plan. Ticket: DOMESTIQUE-66. Related\n"
                      "tickets: DOMESTIQUE-2 (UI gate)\n").tickets == ["DOMESTIQUE-66"]
    assert norm_title("Plan: Approve a PR!") == norm_title("approve a pr: plan") == "approve a pr"


class FakeRepoGH:
    def default_branch(self, owner, repo):
        return {"homelab": "master"}.get(repo, "main")


def test_parse_repo_projects_local_paths_and_branches(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAN_PATH_PLANE_MOBILE", str(tmp_path / "pm"))
    monkeypatch.setenv("PLAN_PATH_HOMELAB", str(tmp_path / "hl"))
    rps = cli.parse_repo_projects("homelab:HOMELAB:Homelab:plans,plane-mobile:PLANEMOBILE:Plane Mobile:plans,"
                                  "x:X:X", tmp_path, None, "gjcourt", FakeRepoGH())
    assert [(r.repo, r.identifier, r.name, r.plans_path, r.branch) for r in rps] == [
        ("homelab", "HOMELAB", "Homelab", tmp_path / "hl", "master"),
        ("plane-mobile", "PLANEMOBILE", "Plane Mobile", tmp_path / "pm", "main"),
        ("x", "X", "X", None, "master")]
