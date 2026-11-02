from pathlib import Path

import pytest
from conftest import run_invariants

from plane_sync import lab
from plane_sync.mapping import LAB_STATUSES


def test_parse_briefs(lab_root):
    bs = {b.number: b for b in lab.briefs(lab_root)}
    assert set(bs) == {"03-001", "03-002", "06-001"}
    b = bs["03-002"]
    assert b.title == "IP Cleanup" and b.status == "Not Started" and b.difficulty == "Medium"
    assert b.depends_on == ["homelab/cilium"]
    assert b.summary == "First paragraph of IP Cleanup, which becomes the Plane description."
    cats = {c.prefix: c for c in lab.categories(lab_root)}
    assert cats["03"].name == "Homelab & Automation" and cats["03"].identifier == "AUTO"


def test_frontmatter_roundtrip_is_byte_identical(lab_root):
    for p in lab_root.rglob("0*-*.md"):
        if p.name == "projects.md":
            continue
        text = p.read_text()
        status = lab.parse_brief(lab_root, p.relative_to(lab_root)).status
        assert lab.set_frontmatter_status(text, status) == text


def test_frontmatter_edit_touches_only_status_line(lab_root):
    p = lab_root / "03-homelab-automation/03-002-ip-cleanup.md"
    before = p.read_text()
    after = lab.set_frontmatter_status(before, "Cancelled")
    diff = [(a, b) for a, b in zip(before.split("\n"), after.split("\n")) if a != b]
    assert diff == [("status: 'Not Started'", "status: 'Cancelled'")]
    with pytest.raises(ValueError):
        lab.set_frontmatter_status(before, "Blocked")


def test_projects_row_edit_keeps_alignment(lab_root):
    text = (lab_root / "03-homelab-automation/projects.md").read_text()
    for status in LAB_STATUSES:
        new = lab.set_projects_row_status(text, "03-002", status)
        row = next(l for l in new.split("\n") if l.startswith("| 03-002"))
        old = next(l for l in text.split("\n") if l.startswith("| 03-002"))
        assert len(row) == len(old)
        assert row.split("|")[3].strip() == status
        assert [l for l in new.split("\n") if not l.startswith("| 03-002")] == \
               [l for l in text.split("\n") if not l.startswith("| 03-002")]
    assert lab.set_projects_row_status(text, "03-002", "Not Started") == text


def test_readme_recount(lab_root):
    text = (lab_root / "README.md").read_text()
    assert lab.recount_readme(text, ["Done", "In Progress", "Not Started"]) == text
    new = lab.recount_readme(text, ["Done", "Done", "Cancelled"])
    assert "3 briefs (2 done, 0 in progress, 1 cancelled)" in new
    para = new.split("\n\n")[3]
    assert all(len(l) <= 100 for l in para.split("\n"))
    assert new.split("\n\n")[:3] == text.split("\n\n")[:3]
    assert new.endswith("## More\n")


def test_wrap_reproduces_real_readme_paragraph(real_lab):
    text = (real_lab / "README.md").read_text()
    m = lab.README_STATUS_RE.search(text)
    start = text.rfind("\n\n", 0, m.start()) + 2
    end = text.find("\n\n", m.end())
    para = text[start:end]
    assert lab.wrap_paragraph(" ".join(para.split("\n"))) == para


def test_real_lab_roundtrip_and_invariants_after_edit(lab_copy):
    root = lab_copy
    all_briefs = lab.briefs(root)
    assert len(all_briefs) >= 140
    # Byte-identical when nothing changes.
    for b in all_briefs:
        text = (root / b.path).read_text()
        assert lab.set_frontmatter_status(text, b.status) == text
        pm = (root / b.path).parent / "projects.md"
        row = next(l for l in pm.read_text().split("\n") if l.startswith(f"| {b.number}"))
        if row.split("|")[3].strip() != b.status:
            continue  # the index already disagrees with the brief (pre-existing drift)
        assert lab.set_projects_row_status(pm.read_text(), b.number, b.status) == pm.read_text()
    readme = (root / "README.md").read_text()
    statuses = [b.status for b in all_briefs]
    once = lab.recount_readme(readme, statuses)
    assert lab.recount_readme(once, statuses) == once  # idempotent
    # (the live README count can lag the briefs; the recount is what corrects it)
    # Simulated sync edit: move one brief to each status, then the lab invariants must pass.
    target = next(b for b in all_briefs if b.status == "Not Started")
    for status in ("In Progress", "Done", "Cancelled"):
        p = root / target.path
        p.write_text(lab.set_frontmatter_status(p.read_text(), status))
        pm = p.parent / "projects.md"
        pm.write_text(lab.set_projects_row_status(pm.read_text(), target.number, status))
        rd = root / "README.md"
        rd.write_text(lab.recount_readme(rd.read_text(), [b.status for b in lab.briefs(root)]))
        r = run_invariants(root)
        assert r.returncode == 0, r.stdout + r.stderr
