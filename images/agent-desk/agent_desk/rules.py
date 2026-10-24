"""The startable rule, as pure functions over what Plane and Kubernetes report.

Nothing here does I/O, so every gate is unit-tested against payloads shaped
like the ones measured on staging (plan, "Step 2 findings").
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

PLAYBOOK_PREFIX = "playbook:"

# Ticket states the dispatcher reasons about, by name (created per project in
# plan step 2/3). Anything else in a `started` group is left alone.
TODO, IN_PROGRESS, IN_REVIEW, BLOCKED = "Todo", "In Progress", "In review", "Blocked"
AWAITING, DONE, CANCELLED = "Awaiting approval", "Done", "Cancelled"
WAITING_STATES = {TODO, AWAITING}  # plus anything in the backlog/unstarted groups


def label_applied_by(activities: list[dict], label_id: str, user_id: str) -> bool:
    """True iff the latest change to `label_id` was an add by `user_id`.

    Plane logs one activity per label change: `field == "labels"`, with the
    label's id in `new_identifier` (added) or `old_identifier` (removed), and
    the user in `actor`. Matching on the id, not the name, means renaming a
    label can neither spoof nor break the gate. An unexpected format finds no
    row and fails closed.

    Labels set while creating the ticket get no row of their own: Plane logs
    only the "created" row (staging, SANDBOX-8, 2026-10-24). So a label with
    no rows at all was set at creation, and counts as added by whoever
    created the ticket. Any later change has a row, which then decides.
    """
    latest, created_by = None, None
    for a in activities:  # oldest first
        if a.get("verb") == "created" and not a.get("field"):
            created_by = a.get("actor")
        if a.get("field") != "labels":
            continue
        if label_id in (a.get("new_identifier"), a.get("old_identifier")):
            latest = a
    if latest is None:
        return bool(created_by) and created_by == user_id
    return bool(latest.get("new_identifier") == label_id
                and latest.get("actor") == user_id)


def last_entered(activities: list[dict], state_id: str) -> str:
    """created_at of the latest move into `state_id`, or ''."""
    when = ""
    for a in activities:
        if a.get("field") == "state" and a.get("new_identifier") == state_id:
            when = a.get("created_at") or when
    return when


def owner_replied_since(comments: list[dict], owner_id: str, since: str) -> bool:
    """Has the owner commented after `since` (ISO timestamps compare as text)?"""
    return any(c.get("actor", c.get("created_by")) == owner_id
               and (c.get("created_at") or "") > since for c in comments)


def _by_time(comments: list[dict]) -> list[dict]:
    return sorted(comments, key=lambda c: c.get("created_at") or "")


def _actor(c: dict) -> str:
    return c.get("actor", c.get("created_by")) or ""


def wake_comments(comments: list[dict], owner_id: str, bot_id: str) -> list[dict]:
    """George's comments newer than agent-desk's newest one, oldest first.

    The wake rule (plan, "Waking the agent"): a comment George hasn't had an
    answer to. It reads the comments, not events, so a missed webhook or a
    comment written while nothing was listening still counts at the next poll.
    The bot's own comments never wake anything.
    """
    cs = _by_time(comments)
    last_bot = max((i for i, c in enumerate(cs) if _actor(c) == bot_id), default=-1)
    return [c for c in cs[last_bot + 1:] if _actor(c) == owner_id]


# In the dispatcher's question on a closed ticket, and how it's recognised.
REOPEN_WORD = "reopen"
REOPEN_MARK = "to reopen it and wake the agent"


def _text(c: dict) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", c.get("comment_html") or "")).split())


def reopen_state(comments: list[dict], owner_id: str, bot_id: str) -> tuple[str, list[dict]]:
    """On a closed ticket: ("", []) nothing new; ("ask", wake) George commented,
    ask him first; ("go", request) he replied `reopen` to the ask, and request
    is the comments that drew the ask."""
    cs = _by_time(comments)
    wake = wake_comments(cs, owner_id, bot_id)
    if not wake:
        return "", []
    def is_reopen(c):
        return _text(c).lower().strip(" .!`'\"") == REOPEN_WORD

    bots = [i for i, c in enumerate(cs) if _actor(c) == bot_id]
    if bots and REOPEN_MARK in _text(cs[bots[-1]]) and any(is_reopen(c) for c in wake):
        prev = bots[-2] if len(bots) > 1 else -1
        request = [c for c in cs[prev + 1:bots[-1]] if _actor(c) == owner_id]
        return "go", request + [c for c in wake if not is_reopen(c)]
    return "ask", wake


@dataclass
class Ticket:
    """What the rule needs to know about one work item."""
    id: str
    project_id: str
    project_identifier: str
    sequence_id: int
    name: str
    state_name: str
    state_group: str
    label_names: list[str]
    external_source: str | None = None
    created_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.project_identifier}-{self.sequence_id}"

    @property
    def playbooks(self) -> list[str]:
        return [n[len(PLAYBOOK_PREFIX):] for n in self.label_names
                if n.startswith(PLAYBOOK_PREFIX)]


@dataclass
class Decision:
    action: str  # start | wait | comment | confirm | skip
    reason: str = ""
    playbook: str = ""
    over_cap: bool = False
    resume: bool = False
    extra: dict = field(default_factory=dict)


def decide(t: Ticket, *, repo: str | None, deny_repos: set[str], known_playbooks: set[str],
           ready_by_owner: bool, over_cap_by_owner: bool, has_active_job: bool,
           running_for_playbook: int, max_concurrent: int,
           woken: bool = False, reopen: str = "") -> Decision:
    """The plan's startable rule, in its order.

    `comment` decisions are the two George wants to hear about (an unusable
    playbook label, a denied repo). Everything else that fails is silent.
    """
    # 1. allowlisted project, and not a plane-sync item
    if repo is None:
        return Decision("skip", "project not allowlisted")
    if t.external_source:
        return Decision("skip", "synced item (external_source)")
    resume = False
    if t.state_group in ("completed", "cancelled"):
        # A comment on a closed ticket asks before it reopens (George, 2026-10-24):
        # the question costs no tokens; only his `reopen` starts a Job.
        if reopen == "ask" and "ready" in t.label_names:
            return Decision("confirm", f"George commented on a {t.state_name} ticket")
        if reopen != "go":
            return Decision("skip", "closed")
        resume = True
    elif t.state_name in (BLOCKED, IN_REVIEW):
        if not woken:
            return Decision("skip", "blocked, waiting for George" if t.state_name == BLOCKED
                            else f"already {t.state_name}")
        resume = True
    elif t.state_name == IN_PROGRESS or (
            t.state_group == "started" and t.state_name not in WAITING_STATES):
        return Decision("skip", f"already {t.state_name}")
    if "ready" not in t.label_names:
        return Decision("skip", "no ready label")
    # 3. ready applied by George (checked before rule 2 so an unready ticket
    # with a typo'd playbook label doesn't draw a comment)
    if not ready_by_owner:
        return Decision("skip", "ready not applied by George")
    # 2. exactly one known playbook
    pbs = t.playbooks
    if len(pbs) != 1:
        return Decision("comment", f"needs exactly one `playbook:` label, has {len(pbs)}")
    pb = pbs[0]
    if pb not in known_playbooks:
        return Decision("comment", f"`playbook:{pb}` isn't a playbook the dispatcher knows",
                        playbook=pb)
    # 4. deny list
    if repo in deny_repos:
        return Decision("comment", f"`{repo}` is on the agent deny list", playbook=pb)
    # 5. one Job per ticket
    if has_active_job:
        return Decision("skip", "a Job for this ticket is already active", playbook=pb)
    # 6. playbook cap, unless George approved this one past it
    if running_for_playbook >= max_concurrent:
        if over_cap_by_owner:
            return Decision("start", "over cap, approved by George", playbook=pb,
                            over_cap=True, resume=resume)
        return Decision("wait", f"`{pb}` is at {running_for_playbook}/{max_concurrent} running",
                        playbook=pb, resume=resume)
    return Decision("start", "startable", playbook=pb, resume=resume)
