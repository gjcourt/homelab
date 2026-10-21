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
