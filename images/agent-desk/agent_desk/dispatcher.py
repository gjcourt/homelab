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

from . import jobs as jobmod
from . import prompt as promptmod
from . import rules
from .rules import AWAITING, BLOCKED, DONE, IN_PROGRESS, IN_REVIEW, TODO, Ticket

PR_URL = re.compile(r"^https://github\.com/([^/]+/[^/]+)/pull/(\d+)")


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
    def __init__(self, cfg, plane, kube, github=None, log=print):
        self.cfg = cfg
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
        # Tickets this process started and believes In Progress, by playbook.
        # Re-derived from Plane on every poll; only bridges the gap in between.
        self.in_progress: dict[str, str] = {}
        self.last_tick = time.time()

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

    def evaluate(self, pr: Project, item: dict, job_list: list[dict]) -> rules.Decision:
        t = self.ticket(pr, item)
        cheap = rules.decide(t, repo=pr.repo, deny_repos=self.cfg.deny_repos,
                             known_playbooks=set(self.cfg.playbooks), ready_by_owner=True,
                             over_cap_by_owner=False, has_active_job=False,
                             running_for_playbook=0, max_concurrent=1,
                             blocked_answered=True)
        if cheap.action == "skip":  # fails without even asking who added what
            return cheap
        acts = self.plane.activities(pr.id, t.id)
        ready_id, over_id = pr.labels.get("ready"), pr.labels.get("over-cap")
        ready_ok = bool(ready_id) and rules.label_applied_by(acts, ready_id, self.owner_id)
        over_ok = ("over-cap" in t.label_names and bool(over_id)
                   and rules.label_applied_by(acts, over_id, self.owner_id))
        answered = False
        if t.state_name == BLOCKED:
            since = rules.last_entered(acts, pr.states.get(BLOCKED, ""))
            answered = rules.owner_replied_since(self.plane.comments(pr.id, t.id),
                                                 self.owner_id, since)
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
            blocked_answered=answered)

    def handle(self, pr: Project, item: dict, job_list: list[dict]) -> rules.Decision:
        t = self.ticket(pr, item)
        d = self.evaluate(pr, item, job_list)
        if d.action == "skip":
            return d
        self.log(f"{t.key}: {d.action} — {d.reason}")
        if d.action == "comment":
            self.comment_once(pr, t, f"agent-desk can't start this ticket: {d.reason}.")
        elif d.action == "wait":
            if t.state_name != AWAITING and pr.states.get(AWAITING):
                self.plane.update_item(pr.id, t.id, {"state": pr.states[AWAITING]})
            self.comment_once(pr, t, f"{d.reason}. Add `over-cap` to start this one now, "
                                     "or it starts when a slot frees.")
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

    def start(self, pr: Project, t: Ticket, d: rules.Decision, job_list: list[dict]) -> None:
        pb = self.cfg.playbooks[d.playbook]
        item = self.plane.item(pr.id, t.id)
        comments = [("George" if c.get("actor", c.get("created_by")) == self.owner_id
                     else "agent-desk" if c.get("actor", c.get("created_by")) == self.bot_id
                     else "someone else", c.get("comment_html", ""))
                    for c in self.plane.comments(pr.id, t.id)]
        text = promptmod.build(pb.prompt, key=t.key, title=t.name,
                               description_html=item.get("description_html", ""),
                               comments=comments, resume=d.resume)
        if self.template is None:
            self.template = jobmod.load_template(self.cfg.job_template)
        tid = jobmod.task_id(t.key)
        ticket = {"key": t.key, "project_id": pr.id, "work_item_id": t.id,
                  "plane_url": self.cfg.plane_url, "workspace": self.cfg.workspace}
        job = jobmod.build_job(self.template, tid=tid, image=self.kube.console_image(),
                               repo=pr.repo, prompt=text, ticket=ticket, playbook=d.playbook,
                               deadline=pb.deadline, model=pb.model,
                               jobs_token_secret=self.cfg.jobs_token_secret)
        try:
            self.kube.create(job, dry_run=self.cfg.dry_run_jobs)
        except Exception as e:  # noqa: BLE001 — admission denial, quota, API error
            self.log(f"{t.key}: Job create failed: {e}")
            self.comment_once(pr, t, f"agent-desk couldn't create the Job: {str(e)[:300]}")
            return
        self.in_progress[t.id] = d.playbook
        self.plane.update_item(pr.id, t.id, {"state": pr.states[IN_PROGRESS]})
        note = (" Dry run: the API server admitted the Job, but it wasn't created."
                if self.cfg.dry_run_jobs else "")
        why = " Started above the playbook cap, on George's `over-cap`." if d.over_cap else ""
        self.plane.comment(pr.id, t.id, _p(
            f"Started bench-cloud Job `{tid}` (playbook `{d.playbook}`, repo `{pr.repo}`)."
            f"{why}{note}"))
        self.log(f"{t.key}: started {tid}{' (dry-run)' if self.cfg.dry_run_jobs else ''}")

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
                    self.handle(self.projects[pid], it, job_list)
                except Exception as e:  # noqa: BLE001 — one bad ticket mustn't stop the pass
                    self.log(f"error on item {it.get('id')}: {e}")
        self.last_tick = time.time()

    def on_event(self, project_id: str, item_id: str) -> None:
        self.refresh()
        pr = self.projects.get(project_id)
        if not pr:
            return
        by_project = self.items()
        job_list = self.snapshot(by_project)
        item = next((i for i in by_project.get(project_id, []) if i["id"] == item_id), None)
        if item:
            self.handle(pr, item, job_list)

    def reap(self, by_project: dict[str, list[dict]], job_list: list[dict]) -> None:
        """A finished Job whose ticket is still In Progress crashed, timed out or
        ended without saying anything: park the ticket in Blocked."""
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
                self.plane.update_item(pr.id, t.id, {"state": pr.states[BLOCKED]})
                self.plane.comment(pr.id, t.id, _p(
                    f"Job `{j['name']}` ended ({how}) without moving the ticket to In review "
                    f"or Blocked. The run record is at `{record}`."))
                self.in_progress.pop(t.id, None)
                self.log(f"{t.key}: reaped {j['name']} ({how})")

    def github_poll(self) -> None:
        """In review → Done when the linked PR merges; → Todo when it's closed."""
        if not self.github:
            return
        self.refresh()
        for pid, items in self.items().items():
            pr = self.projects[pid]
            for it in items:
                t = self.ticket(pr, it)
                if t.state_name != IN_REVIEW:
                    continue
                for link in self.plane.links(pid, t.id):
                    m = PR_URL.match(link.get("url", ""))
                    if not m or m.group(1).lower() != pr.repo.lower():
                        continue
                    state = self.github.pr_state(m.group(1), int(m.group(2)))
                    if state == "merged":
                        self.plane.update_item(pid, t.id, {"state": pr.states[DONE]})
                        self.log(f"{t.key}: PR merged → Done")
                    elif state == "closed":
                        self.plane.update_item(pid, t.id, {"state": pr.states[TODO]})
                        self.plane.comment(pid, t.id, _p(
                            f"{link['url']} was closed without merging; back to Todo."))
                        self.log(f"{t.key}: PR closed → Todo")
                    break


def event_target(payload: dict) -> tuple[str, str] | None:
    """(project id, work item id) from a Plane webhook body, or None."""
    data = payload.get("data") or {}
    if payload.get("event") == "issue":
        return data.get("project"), data.get("id")
    if payload.get("event") == "issue_comment":
        return data.get("project"), data.get("issue")
    return None
