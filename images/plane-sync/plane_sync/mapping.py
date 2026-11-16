"""Status vocabularies and the mappings between them.

Briefs: Plane owns status. Plans: the repo owns status. See
docs/plans/2026-10-21-plane-lab-project-sync.md.
"""

LAB_STATUSES = ("Not Started", "In Progress", "Done", "Cancelled")

# Plane state *groups* are stable across renamed or custom states, so the
# Plane -> lab direction maps on the group, never on the state name.
GROUP_TO_LAB = {
    "backlog": "Not Started",
    "unstarted": "Not Started",
    "started": "In Progress",
    "completed": "Done",
    "cancelled": "Cancelled",
}

# Lab -> Plane picks a state by name (the defaults every new project gets).
LAB_TO_STATE = {
    "Not Started": "Backlog",
    "In Progress": "In Progress",
    "Done": "Done",
    "Cancelled": "Cancelled",
}

PROPOSED = "Proposed"  # custom backlog-group state for plans still in an open PR

# Plan frontmatter status -> Plane state name.
PLAN_TO_STATE = {
    "planned": "Todo",
    "in-progress": "In Progress",
    "complete": "Done",
    "superseded": "Cancelled",
    "abandoned": "Cancelled",
}


def lab_status_for_group(group: str) -> str:
    try:
        return GROUP_TO_LAB[group]
    except KeyError:
        raise ValueError(f"unknown Plane state group {group!r}") from None


def state_for_lab(status: str) -> str:
    try:
        return LAB_TO_STATE[status]
    except KeyError:
        raise ValueError(f"unknown lab status {status!r}") from None


def state_for_plan(status: str, *, proposed: bool = False, pr_closed_unmerged: bool = False) -> str:
    if pr_closed_unmerged:
        return "Cancelled"
    if proposed:
        return PROPOSED
    try:
        return PLAN_TO_STATE[status]
    except KeyError:
        raise ValueError(f"unknown plan status {status!r}") from None


def decide_brief(repo: str, plane: str, last: str | None) -> tuple[str, str | None]:
    """Decide what to do with one brief's status.

    Returns (action, value):
      ("none", None)        nothing to do
      ("write_repo", s)     write status s into the lab repo (Plane wins)
      ("conflict", None)    both sides changed to different values: write neither
      ("record", s)         both already agree on s; just remember it

    `last` is the status both sides held after the previous sync (None the
    first time a brief is seen after import, which is treated as "agreed on
    whatever the repo says" because import set Plane from the repo).
    """
    if last is None:
        last = repo
    if repo == plane:
        return ("record", repo) if repo != last else ("none", None)
    repo_changed = repo != last
    plane_changed = plane != last
    if repo_changed and plane_changed:
        return ("conflict", None)
    # Plane wins: either Plane changed, or only the repo changed (a hand edit),
    # which the plan resolves in Plane's favour.
    return ("write_repo", plane)
