"""The dispatcher loop: Plane tickets in, bench-cloud Jobs out.

Stateless by design (plan, "Dispatcher"): Plane (states, labels, comments,
links) and Kubernetes (Jobs labelled with the ticket and playbook) are the
record. A restart re-derives everything from one full poll.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import jobs as jobmod
from . import prompt as promptmod
from . import rules
from .notify import Notifier
from .plane import PlaneError
from .rules import AWAITING, BLOCKED, DONE, IN_PROGRESS, IN_REVIEW, TODO, Ticket

PR_URL = re.compile(r"^https://github\.com/([^/]+/[^/]+)/pull/(\d+)")
# A ticket that changed this long before the newest delivery, and this long
# ago, was missed by the webhook: Plane delivers within seconds.
WEBHOOK_SLACK = 120


@dataclass
class Project:
    id: str
    identifier: str
    repo: str
    states: dict[str, str] = field(default_factory=dict)  # name → id
    state_info: dict[str, tuple[str, str]] = field(default_factory=dict)  # id → (name, group)
    labels: dict[str, str] = field(default_factory=dict)  # name → id
    label_names: dict[str, str] = field(default_factory=dict)  # id → name


def _p(text: str) -> str:
    """One paragraph of comment HTML; backticks become <code>."""
    out = html.escape(text, quote=False)
    return "<p>" + re.sub(r"`([^`]+)`", r"<code>\1</code>", out) + "</p>"


class Dispatcher:
    def __init__(self, cfg, plane, kube, github=None, log=print, notifier=None):
        self.cfg = cfg
        self.notifier = notifier or Notifier("", "", log=log)
        # Last state seen per ticket, to notice a move into Blocked or In review.
        # Seeded by the first poll without notifying, so a restart is quiet.
        self.seen_state: dict[str, str] = {}
        self._seeded = False
        self.plane = plane
        self.kube = kube
        self.github = github
        self.log = log
        self.template = None
        self.projects: dict[str, Project] = {}  # project id → Project
        self.owner_id = ""
        self.bot_id = ""
        self._refreshed = 0.0
        self._not_member: set[str] = set()
        self.unconfirmed = False
        # Tickets this process started and believes In Progress, by playbook.
        # Re-derived from Plane on every poll; only bridges the gap in between.
        self.in_progress: dict[str, str] = {}
        self.last_tick = time.time()
        # Plane switches a webhook off after failed deliveries and tells no
        # one (SMTP is off), leaving only the poll. Counted from start-up, so
        # changes made while this pod was down don't count as missed.
        self.last_delivery = time.time()
        self.webhook_quiet = False

    # -- context ----------------------------------------------------------------

    def refresh(self, force: bool = False) -> None:
        if not force and time.time() - self._refreshed < 60:
            return
        members = self.plane.workspace_members()
        by_email = {m.get("email"): m.get("id") for m in members}
        self.owner_id = by_email.get(self.cfg.owner_email, "")
        self.bot_id = by_email.get(self.cfg.bot_email, "")
        if not self.owner_id:
            raise RuntimeError(f"owner {self.cfg.owner_email} isn't a workspace member")
        projects = {}
        for p in self.plane.projects():
            repo = self.cfg.projects.get(p["identifier"])
            if not repo:
                continue
            # A public project is listed to every workspace member, but its
            # states and items 403 unless the bot is a project member. Skip it
            # rather than fail the whole poll: no membership, no access.
            if p.get("is_member") is False:
                if p["identifier"] not in self._not_member:
                    self.log(f"{p['identifier']}: skipped, {self.cfg.bot_email} isn't a project member")
                    self._not_member.add(p["identifier"])
                continue
            self._not_member.discard(p["identifier"])
            pr = Project(p["id"], p["identifier"], repo)
            for s in self.plane.states(p["id"]):
                pr.states[s["name"]] = s["id"]
                pr.state_info[s["id"]] = (s["name"], s["group"])
            for lb in self.plane.labels(p["id"]):
                pr.labels[lb["name"]] = lb["id"]
                pr.label_names[lb["id"]] = lb["name"]
            projects[p["id"]] = pr
        self.projects = projects
        self._refreshed = time.time()

    def ticket(self, pr: Project, item: dict) -> Ticket:
        name, group = pr.state_info.get(item.get("state"), ("?", "?"))
        labels = [pr.label_names.get(lid, "?") for lid in item.get("labels") or []]
        return Ticket(id=item["id"], project_id=pr.id, project_identifier=pr.identifier,
                      sequence_id=item.get("sequence_id") or 0, name=item.get("name", ""),
                      state_name=name, state_group=group, label_names=labels,
                      external_source=item.get("external_source"),
                      created_at=item.get("created_at") or "")

    # -- notifications ----------------------------------------------------------

    def ticket_url(self, t: Ticket) -> str:
        base = self.cfg.plane_web_url.rstrip("/")
        return f"{base}/{self.cfg.workspace}/browse/{t.key}/" if base else ""

    def note_state(self, pr: Project, t: Ticket) -> None:
        """Tell George when one of his tickets reaches Blocked (a question, or a
        Job that ended badly) or In review (a PR to look at)."""
        prev = self.seen_state.get(t.id)
        self.seen_state[t.id] = t.state_name
        if prev == t.state_name or t.state_name not in (BLOCKED, IN_REVIEW):
            return
        if not self._seeded or "ready" not in t.label_names:
            return
        cmts = self.plane.comments(pr.id, t.id)
        if rules.wake_comments(cmts, self.owner_id, self.bot_id):
            return  # he has answered since; the next move isn't his
        if t.state_name == BLOCKED:
            mine = sorted((c for c in cmts if c.get("actor", c.get("created_by")) == self.bot_id),
                          key=lambda c: c.get("created_at") or "")
            text = promptmod.html_to_text(mine[-1].get("comment_html", "")) if mine else t.name
            self.notifier.send(f"{t.key} needs you", text, self.ticket_url(t))
        else:
            prs = [link.get("title") or link.get("url", "") for link in self.plane.links(pr.id, t.id)
                   if PR_URL.match(link.get("url", ""))]
            self.notifier.send(f"{t.key} is ready for review", "; ".join(prs) or t.name,
                               self.ticket_url(t))

    def saw_delivery(self) -> None:
        """A signed delivery arrived (called from the HTTP thread)."""
        self.last_delivery = time.time()
        if self.webhook_quiet:
            self.webhook_quiet = False
            self.log("webhook deliveries are arriving again")

    def check_webhook(self, by_project: dict[str, list[dict]], now: float | None = None) -> None:
        """Warn once when the poll finds a change the webhook never delivered."""
        now = time.time() if now is None else now
        newest = max((_epoch(it.get("updated_at")) for items in by_project.values()
                      for it in items), default=0.0)
        if (self.webhook_quiet or newest - self.last_delivery < WEBHOOK_SLACK
                or now - newest < WEBHOOK_SLACK):
            return
        self.webhook_quiet = True
        mins = int((now - newest) // 60)
        self.log(f"webhook looks off: a ticket changed {mins} min ago with no delivery since")
        base = self.cfg.plane_web_url.rstrip("/")
        self.notifier.send(
            "agent-desk: Plane's webhook looks off",
            f"A ticket changed {mins} min ago and no webhook delivery has arrived since. "
            "Check Workspace settings → Webhooks in Plane; until it's back, tickets "
            f"only move on the {self.cfg.poll_seconds // 60}-minute poll.",
            f"{base}/{self.cfg.workspace}/" if base else "")

    # -- counting ---------------------------------------------------------------

    def snapshot(self, items_by_project: dict[str, list[dict]]) -> list[dict]:
        """Jobs now, and which tickets are In Progress per playbook."""
        job_list = self.kube.jobs()
        self.in_progress = {}
        for pid, items in items_by_project.items():
            pr = self.projects[pid]
            for it in items:
                t = self.ticket(pr, it)
                if t.state_name == IN_PROGRESS and len(t.playbooks) == 1:
                    self.in_progress[t.id] = t.playbooks[0]
        return job_list

    def running(self, playbook: str, job_list: list[dict]) -> int:
        """Active Jobs for the playbook ∪ its tickets In Progress. The union
        counts a dry-run start (no Job) and a Job whose ticket was moved."""
        ids = {j["ticket"] for j in job_list if j["active"] and j["playbook"] == playbook}
        ids |= {tid for tid, pb in self.in_progress.items() if pb == playbook}
        return len(ids)

    # -- evaluation -------------------------------------------------------------

    def precheck(self, pr: Project, t: Ticket) -> rules.Decision:
        """The rules that need only the ticket: no activity log, no Job count."""
        return rules.decide(t, repo=pr.repo, deny_repos=self.cfg.deny_repos,
                            known_playbooks=set(self.cfg.playbooks), ready_by_owner=True,
                            over_cap_by_owner=False, has_active_job=False,
                            running_for_playbook=0, max_concurrent=1,
                            woken=True, reopen="go")

    def evaluate(self, pr: Project, item: dict, job_list: list[dict],
                 review: bool = False) -> rules.Decision:
        """review: George asked for changes on the ticket's PR, which wakes an
        In review ticket the way his comment would."""
        t = self.ticket(pr, item)
        self.unconfirmed = False
        cheap = self.precheck(pr, t)
        if cheap.action == "skip":  # fails without even asking who added what
            return cheap
        acts = self.plane.activities(pr.id, t.id)
        ready_id, over_id = pr.labels.get("ready"), pr.labels.get("over-cap")
        ready_ok = bool(ready_id) and rules.label_applied_by(acts, ready_id, self.owner_id)
        over_ok = ("over-cap" in t.label_names and bool(over_id)
                   and rules.label_applied_by(acts, over_id, self.owner_id))
        # Plane sends the webhook before it writes the activity row (staging
        # 2026-10-24: 1.4s later), so a label George just added can look
        # unattributed. on_event asks the worker to look again shortly.
        self.unconfirmed = ("ready" in t.label_names and not ready_ok) or \
            ("over-cap" in t.label_names and not over_ok)
        woken, reopen = False, ""
        if t.state_name in (BLOCKED, IN_REVIEW) or t.state_group in ("completed", "cancelled"):
            cmts = self.plane.comments(pr.id, t.id)
            woken = review or bool(rules.wake_comments(cmts, self.owner_id, self.bot_id))
            if t.state_group in ("completed", "cancelled"):
                reopen, _ = rules.reopen_state(cmts, self.owner_id, self.bot_id)
        pb = t.playbooks[0] if len(t.playbooks) == 1 else ""
        playbook = self.cfg.playbooks.get(pb)
        if playbook and playbook.allowed_repos and pr.repo not in playbook.allowed_repos:
            return rules.Decision("comment", f"`playbook:{pb}` isn't allowed on `{pr.repo}`",
                                  playbook=pb)
        active = any(j["active"] and j["ticket"] == t.id for j in job_list)
        return rules.decide(
            t, repo=pr.repo, deny_repos=self.cfg.deny_repos,
            known_playbooks=set(self.cfg.playbooks), ready_by_owner=ready_ok,
            over_cap_by_owner=over_ok, has_active_job=active,
            running_for_playbook=self.running(pb, job_list) if pb else 0,
            max_concurrent=playbook.max_concurrent if playbook else 0,
            woken=woken, reopen=reopen)

    def handle(self, pr: Project, item: dict, job_list: list[dict],
               log_skips: bool = False) -> rules.Decision:
        """log_skips: an event names one ticket, so say why it didn't move. The
        10-minute poll checks every ticket and logs only what it acts on."""
        t = self.ticket(pr, item)
        d = self.evaluate(pr, item, job_list)
        if d.action == "skip":
            if log_skips:
                self.log(f"{t.key}: skip — {d.reason}")
            return d
        self.log(f"{t.key}: {d.action} — {d.reason}")
        if d.action == "comment":
            self.comment_once(pr, t, f"agent-desk can't start this ticket: {d.reason}.")
        elif d.action == "wait":
            if t.state_name != AWAITING and pr.states.get(AWAITING):
                self.plane.update_item(pr.id, t.id, {"state": pr.states[AWAITING]})
            self.comment_once(pr, t, f"{d.reason}. Add `over-cap` to start this one now, "
                                     "or it starts when a slot frees.")
        elif d.action == "confirm":
            text = (f"{t.key} is {t.state_name}. Reply `{rules.REOPEN_WORD}` "
                    f"{rules.REOPEN_MARK} on your comment.")
            mine = [c for c in self.plane.comments(pr.id, t.id)
                    if c.get("actor", c.get("created_by")) == self.bot_id]
            if not (mine and rules.REOPEN_MARK in promptmod.html_to_text(
                    mine[-1].get("comment_html", ""))):
                self.notifier.send(f"{t.key}: reopen?", f"You commented on a {t.state_name} "
                                   f"ticket. Reply `{rules.REOPEN_WORD}` to wake the agent.",
                                   self.ticket_url(t))
            self.comment_once(pr, t, text)
        elif d.action == "start":
            self.start(pr, t, d, job_list)
        return d

    def comment_once(self, pr: Project, t: Ticket, text: str) -> None:
        """Skip if the bot's latest comment on the ticket already says this."""
        body = _p(text)
        mine = [c for c in self.plane.comments(pr.id, t.id)
                if c.get("actor", c.get("created_by")) == self.bot_id]
        if mine and promptmod.html_to_text(mine[-1].get("comment_html", "")) == \
                promptmod.html_to_text(body):
            return
        self.plane.comment(pr.id, t.id, body)

    # -- starting ---------------------------------------------------------------

    def start(self, pr: Project, t: Ticket, d: rules.Decision, job_list: list[dict],
              review: dict | None = None) -> bool:
        """review: {"id", "url", "text"} when George's GitHub review woke it.
        True when the Job was created (or admitted, on a dry run)."""
        pb = self.cfg.playbooks[d.playbook]
        item = self.plane.item(pr.id, t.id)
        cmts = self.plane.comments(pr.id, t.id)
        comments = [("George" if c.get("actor", c.get("created_by")) == self.owner_id
                     else "agent-desk" if c.get("actor", c.get("created_by")) == self.bot_id
                     else "someone else", c.get("comment_html", ""))
                    for c in cmts]
        if review:
            comments.append(("George, in a GitHub review", _p(review["text"])))
        text = promptmod.build(pb.prompt, key=t.key, title=t.name,
                               description_html=item.get("description_html", ""),
                               comments=comments, resume=d.resume, repo=pr.repo,
                               repo_note=self.cfg.repo_notes.get(pr.repo, ""))
        if self.template is None:
            self.template = jobmod.load_template(self.cfg.job_template)
        tid = jobmod.task_id(t.key)
        # owner_id: the Job's comment hook relays only George's comments.
        ticket = {"key": t.key, "project_id": pr.id, "work_item_id": t.id,
                  "plane_url": self.cfg.plane_url, "workspace": self.cfg.workspace,
                  "owner_id": self.owner_id,
                  # The browser link, for the PR description (house rules).
                  "web_url": self.ticket_url(t),
                  # The comments in the prompt; the Job's hook relays any others.
                  "seen_comments": [c["id"] for c in cmts if c.get("id")]}
        if d.resume:
            # Woken: the Job resumes the ticket's last session with only what's
            # new; TASK_PROMPT (the whole thread) is its fallback.
            ticket["resume"] = True
            if review:
                ticket["resume_prompt"] = promptmod.build_review_resume(
                    key=t.key, text=review["text"])
            else:
                if t.state_group in ("completed", "cancelled"):
                    _, request = rules.reopen_state(cmts, self.owner_id, self.bot_id)
                else:
                    request = rules.wake_comments(cmts, self.owner_id, self.bot_id)
                ticket["resume_prompt"] = promptmod.build_resume(
                    key=t.key, comments=[c.get("comment_html", "") for c in request])
        job = jobmod.build_job(self.template, tid=tid, image=self.kube.console_image(),
                               repo=pr.repo, prompt=text, ticket=ticket, playbook=d.playbook,
                               deadline=pb.deadline, model=pb.model,
                               jobs_token_secret=self.cfg.jobs_token_secret)
        try:
            self.kube.create(job, dry_run=self.cfg.dry_run_jobs)
        except Exception as e:  # noqa: BLE001 — admission denial, quota, API error
            self.log(f"{t.key}: Job create failed: {e}")
            self.comment_once(pr, t, f"agent-desk couldn't create the Job: {str(e)[:300]}")
            return False
        self.in_progress[t.id] = d.playbook
        self.seen_state[t.id] = IN_PROGRESS
        change = {"state": pr.states[IN_PROGRESS]}
        over_id = pr.labels.get("over-cap")
        if d.over_cap and over_id in (item.get("labels") or []):
            # One approval, one start. If this ticket needs a slot again (a
            # Blocked resume), it waits for the cap or for George to re-add it.
            change["labels"] = [lid for lid in item["labels"] if lid != over_id]
        self.plane.update_item(pr.id, t.id, change)
        note = (" Dry run: the API server admitted the Job, but it wasn't created."
                if self.cfg.dry_run_jobs else "")
        why = (" Started above the playbook cap, on George's `over-cap` (now removed: "
               "it approves one start)." if d.over_cap else "")
        if review:
            # The marker github_poll looks for, so one review wakes one run.
            why += f" Woken by {review_marker(review['id'])} on {review['url']}."
        self.plane.comment(pr.id, t.id, _p(
            f"Started bench-cloud Job `{tid}` (playbook `{d.playbook}`, repo `{pr.repo}`)."
            f"{why}{note}"))
        self.log(f"{t.key}: started {tid}{' (dry-run)' if self.cfg.dry_run_jobs else ''}")
        return True

    # -- loops ------------------------------------------------------------------

    def items(self) -> dict[str, list[dict]]:
        out = {}
        for pid in self.projects:
            out[pid] = sorted(self.plane.items(pid), key=lambda i: i.get("created_at") or "")
        return out

    def poll(self) -> None:
        """Full pass: every item in every allowlisted project, oldest first,
        so when a playbook slot frees the longest-waiting ticket gets it."""
        self.refresh(force=True)
        by_project = self.items()
        job_list = self.snapshot(by_project)
        self.reap(by_project, job_list)
        for pid, items in by_project.items():
            for it in items:
                try:
                    self.note_state(self.projects[pid], self.ticket(self.projects[pid], it))
                    self.handle(self.projects[pid], it, job_list)
                except Exception as e:  # noqa: BLE001 — one bad ticket mustn't stop the pass
                    self.log(f"error on item {it.get('id')}: {e}")
        self._seeded = True
        self.check_webhook(by_project)
        self.last_tick = time.time()

    def on_event(self, project_id: str, item_id: str) -> bool:
        """True when the ticket carries `ready` or `over-cap` that Plane's
        activity log doesn't yet credit to George: worth another look soon."""
        self.refresh()
        pr = self.projects.get(project_id)
        if not pr:
            return False
        try:
            item = self.plane.item(project_id, item_id)
        except PlaneError as e:  # deleted since the delivery, most likely
            self.log(f"item {item_id}: can't read it ({e})")
            return False
        self.unconfirmed = False
        t = self.ticket(pr, item)
        self.note_state(pr, t)
        cheap = self.precheck(pr, t)
        if cheap.action == "skip":  # most deliveries: no `ready`, or the bot's own edit
            self.log(f"{t.key}: skip — {cheap.reason}")
            return False
        # The cap counts In Progress tickets across every project, so only a
        # ticket that might start pays for listing them all.
        job_list = self.snapshot(self.items())
        self.handle(pr, item, job_list, log_skips=True)
        return self.unconfirmed

    def reap(self, by_project: dict[str, list[dict]], job_list: list[dict]) -> None:
        """A finished Job whose ticket is still In Progress crashed, timed out or
        ended without saying anything: park the ticket in Blocked. One that
        succeeded and linked a PR on the mapped repo only skipped its last step,
        moving the ticket on, so take it to In review instead."""
        latest: dict[str, dict] = {}
        for j in job_list:
            if j["ticket"] and (j["ticket"] not in latest
                                or j.get("created", "") > latest[j["ticket"]].get("created", "")):
                latest[j["ticket"]] = j
        for pid, items in by_project.items():
            pr = self.projects[pid]
            for it in items:
                j = latest.get(it["id"])
                t = self.ticket(pr, it)
                if not j or j["active"] or t.state_name != IN_PROGRESS:
                    continue
                how = j.get("reason") or ("succeeded" if j["succeeded"] else "failed")
                record = self.cfg.run_record.format(task_id=j["name"])
                if j["succeeded"] and self.linked_pr(pr, t):
                    self.plane.update_item(pr.id, t.id, {"state": pr.states[IN_REVIEW]})
                    self.plane.comment(pr.id, t.id, _p(
                        f"Job `{j['name']}` succeeded and linked a PR but left the ticket "
                        f"In Progress, so the dispatcher moved it to In review. The run "
                        f"record is at `{record}`."))
                    self.in_progress.pop(t.id, None)
                    self.log(f"{t.key}: {j['name']} succeeded with a PR → In review")
                    continue
                self.plane.update_item(pr.id, t.id, {"state": pr.states[BLOCKED]})
                self.plane.comment(pr.id, t.id, _p(
                    f"Job `{j['name']}` ended ({how}) without moving the ticket to In review "
                    f"or Blocked. The run record is at `{record}`."))
                self.in_progress.pop(t.id, None)
                self.log(f"{t.key}: reaped {j['name']} ({how})")

    def linked_pr(self, pr: Project, t: Ticket) -> str:
        """The first link on the ticket to a PR on the project's own repo, or ""."""
        for link in self.plane.links(pr.id, t.id):
            m = PR_URL.match(link.get("url", ""))
            if m and m.group(1).lower() == pr.repo.lower():
                return link["url"]
        return ""

    def github_poll(self) -> None:
        """In review → Done when the linked PR merges; → Todo when it's closed;
        a new "request changes" review from George wakes the agent on it."""
        if not self.github:
            return
        self.refresh()
        job_list = None
        for pid, items in self.items().items():
            pr = self.projects[pid]
            for it in items:
                t = self.ticket(pr, it)
                if t.state_name != IN_REVIEW:
                    continue
                try:
                    url = self.linked_pr(pr, t)
                    if not url:
                        continue
                    m = PR_URL.match(url)
                    repo, number = m.group(1), int(m.group(2))
                    state = self.github.pr_state(repo, number)
                    if state == "merged":
                        self.plane.update_item(pid, t.id, {"state": pr.states[DONE]})
                        self.log(f"{t.key}: PR merged → Done")
                    elif state == "closed":
                        self.plane.update_item(pid, t.id, {"state": pr.states[TODO]})
                        self.plane.comment(pid, t.id, _p(
                            f"{url} was closed without merging; back to Todo."))
                        self.log(f"{t.key}: PR closed → Todo")
                    elif self.cfg.github_owner:
                        rv = self.github.changes_requested(repo, number, self.cfg.github_owner)
                        if rv and not self.review_handled(pr, t, rv["id"]):
                            if job_list is None:  # only a review that might wake pays for this
                                job_list = self.snapshot(self.items())
                            self.wake_for_review(pr, it, t, url, rv, job_list)
                except Exception as e:  # noqa: BLE001 — one bad repo mustn't stop the pass
                    self.log(f"{t.key}: github check failed: {e}")

    def review_handled(self, pr: Project, t: Ticket, review_id) -> bool:
        """Has agent-desk already started a run for this review?"""
        mark = review_marker(review_id)
        return any(mark in promptmod.html_to_text(c.get("comment_html", ""))
                   for c in self.plane.comments(pr.id, t.id)
                   if c.get("actor", c.get("created_by")) == self.bot_id)

    def wake_for_review(self, pr: Project, item: dict, t: Ticket, url: str, rv: dict,
                        job_list: list[dict]) -> None:
        """Start a resumed Job with the review as its prompt. A review that
        can't start yet (the cap, a running Job) is tried again next pass:
        the ticket stays In review and the review stays unhandled."""
        d = self.evaluate(pr, item, job_list, review=True)
        if d.action != "start":
            self.log(f"{t.key}: review {rv['id']} not started — {d.reason}")
            if d.action == "wait":
                self.comment_once(pr, t, f"George requested changes on {url}. {d.reason}, "
                                         "so the agent wakes on the review when a slot frees.")
            return
        self.log(f"{t.key}: review {rv['id']} requests changes — {d.reason}")
        review = {"id": rv["id"], "url": url, "text": promptmod.review_text(url, rv)}
        if self.start(pr, t, d, job_list, review=review):
            job_list.append({"ticket": t.id, "playbook": d.playbook, "active": True,
                             "name": "", "succeeded": False})


def review_marker(review_id) -> str:
    return f"GitHub review {review_id}"


def _epoch(stamp) -> float:
    """Plane's ISO timestamp as epoch seconds; 0 when missing or malformed."""
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def event_target(payload: dict) -> tuple[str, str] | None:
    """(project id, work item id) from a Plane webhook body, or None."""
    data = payload.get("data") or {}
    if payload.get("event") == "issue":
        return data.get("project"), data.get("id")
    if payload.get("event") == "issue_comment":
        return data.get("project"), data.get("issue")
    return None
