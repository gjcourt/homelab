import pytest

from plane_sync.mapping import (GROUP_TO_LAB, LAB_STATUSES, LAB_TO_STATE, PROPOSED, decide_brief,
                                lab_status_for_group, state_for_lab, state_for_plan)

DEFAULT_GROUPS = {"Backlog": "backlog", "Todo": "unstarted", "In Progress": "started",
                  "Done": "completed", "Cancelled": "cancelled"}


@pytest.mark.parametrize("status", LAB_STATUSES)
def test_lab_status_roundtrips_through_plane(status):
    state = state_for_lab(status)
    assert lab_status_for_group(DEFAULT_GROUPS[state]) == status


def test_todo_and_backlog_both_mean_not_started():
    assert lab_status_for_group("backlog") == lab_status_for_group("unstarted") == "Not Started"
    assert set(GROUP_TO_LAB.values()) == set(LAB_STATUSES)
    assert set(LAB_TO_STATE) == set(LAB_STATUSES)


def test_plan_states():
    assert state_for_plan("planned") == "Todo"
    assert state_for_plan("in-progress") == "In Progress"
    assert state_for_plan("complete") == "Done"
    assert state_for_plan("superseded") == state_for_plan("abandoned") == "Cancelled"
    assert state_for_plan("", proposed=True) == PROPOSED
    assert state_for_plan("planned", proposed=True) == PROPOSED
    assert state_for_plan("planned", pr_closed_unmerged=True) == "Cancelled"
    with pytest.raises(ValueError):
        state_for_plan("someday")


@pytest.mark.parametrize("repo,plane,last,expected", [
    ("Not Started", "Not Started", "Not Started", ("none", None)),
    ("Not Started", "In Progress", "Not Started", ("write_repo", "In Progress")),  # Plane moved
    ("Done", "Not Started", "Not Started", ("write_repo", "Not Started")),  # hand edit: Plane wins
    ("Done", "In Progress", "Not Started", ("conflict", None)),  # both moved, differently
    ("Done", "Done", "Not Started", ("record", "Done")),  # both moved the same way
    ("In Progress", "Done", None, ("write_repo", "Done")),  # first sight: repo assumed agreed
])
def test_decide_brief(repo, plane, last, expected):
    assert decide_brief(repo, plane, last) == expected


def test_project_names_are_plane_safe():
    from plane_sync.sync import project_name
    assert project_name("Audio & MIDI") == "Audio and MIDI"
    assert project_name("Homelab & Automation") == "Homelab and Automation"
    assert project_name("Cross-Disciplinary") == "Cross Disciplinary"


def test_description_compare_ignores_plane_normalisation():
    from plane_sync.sync import differs
    sent = {"description_html": "<p>It&#x27;s &quot;fine&quot; &amp; good</p>", "labels": ["b", "a"], "name": "n"}
    stored = {"description_html": "<div><p>It's \"fine\" &amp; good</p></div>", "labels": ["a", "b"], "name": "n"}
    assert differs(stored, sent) == {}
    assert differs(dict(stored, name="m"), sent) == {"name": "n"}
    link_sent = {"description_html": '<p><a href="https://x/y">Brief</a></p>'}
    link_stored = {"description_html": '<div><p><a href="https://x/y" rel="noopener noreferrer">Brief</a></p></div>'}
    assert differs(link_stored, link_sent) == {}


def test_description_compare_ignores_plane_editor_resave():
    # Opening an item in Plane's editor re-saves it with class/data-id/target attributes (seen on
    # staging, 2026-10-21); without this every viewed item was re-patched on each sync.
    from plane_sync.sync import differs
    sent = {"description_html": '<p>Uses <code>x</code></p><p><a href="https://x/y">Brief</a> · Hard</p>'}
    stored = {"description_html": (
        '<p class="editor-paragraph-block" data-id="97ba">Uses <code class="c">x</code></p>'
        '<p class="editor-paragraph-block" data-id="b6c9"><a target="_blank" class="text-accent-secondary '
        'underline underline-offset-[3px]" href="https://x/y" rel="noopener noreferrer">Brief</a> · Hard</p>')}
    assert differs(stored, sent) == {}
    assert differs(stored, {"description_html": '<p>Uses <code>x</code></p><p><a href="https://x/z">Brief</a> · Hard</p>'})


def test_inline_markdown_rendered():
    from plane_sync.sync import inline_md
    out = inline_md("The tracker is **[Plane](https://plane.so)**, see `a<b>.md` & **bench-cloud**.")
    assert out == ('The tracker is <strong><a href="https://plane.so">Plane</a></strong>, see '
                   '<code>a&lt;b&gt;.md</code> &amp; <strong>bench-cloud</strong>.')
    assert inline_md("no `**bold**` inside code") == "no <code>**bold**</code> inside code"
