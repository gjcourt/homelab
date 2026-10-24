"""In-memory Plane and Kubernetes, shaped like what staging returned
(plan, "Step 2 findings")."""

import itertools

from agent_desk.plane import PlaneError

OWNER, BOT, OTHER = "u-george", "u-bot", "u-agent"
_clock = itertools.count(1)


def ts():
    return f"2026-10-22T20:{next(_clock):05d}"


class FakePlane:
    def __init__(self):
        self.members = [{"id": OWNER, "email": "gjcourt@gmail.com"},
                        {"id": BOT, "email": "gjcourt+agent-desk@gmail.com"},
                        {"id": OTHER, "email": "other@example.com"}]
        self.projs = {}
        self.writes = []
        self.listed = 0  # full work-item listings

    def add_project(self, pid, identifier, member=True):
        names = [("Backlog", "backlog"), ("Todo", "unstarted"), ("In Progress", "started"),
                 ("Done", "completed"), ("Cancelled", "cancelled"), ("In review", "started"),
                 ("Blocked", "started"), ("Awaiting approval", "unstarted")]
        states = [{"id": f"{pid}-s-{n}", "name": n, "group": g} for n, g in names]
        labels = [{"id": f"{pid}-l-{n}", "name": n} for n in
                  ("ready", "over-cap", "playbook:docs-fix", "playbook:dep-hold", "playbook:bogus")]
        self.projs[pid] = {"identifier": identifier, "states": states, "labels": labels,
                           "items": {}, "acts": {}, "comments": {}, "links": {},
                           "member": member}
        return pid

    def sid(self, pid, name):
        return f"{pid}-s-{name}"

    def lid(self, pid, name):
        return f"{pid}-l-{name}"

    def add_item(self, pid, iid, name="t", state="Todo", seq=1):
        p = self.projs[pid]
        p["items"][iid] = {"id": iid, "name": name, "state": self.sid(pid, state), "labels": [],
                           "sequence_id": seq, "external_source": None, "created_at": ts(),
                           "description_html": "<p>Fix the typo in README.</p>"}
        p["acts"][iid], p["comments"][iid], p["links"][iid] = [], [], []
        return iid

    def label(self, pid, iid, name, actor, add=True):
        it, lid = self.projs[pid]["items"][iid], self.lid(pid, name)
        if add and lid not in it["labels"]:
            it["labels"].append(lid)
        if not add and lid in it["labels"]:
            it["labels"].remove(lid)
        self.projs[pid]["acts"][iid].append({
            "field": "labels", "actor": actor, "created_at": ts(),
            "new_identifier": lid if add else None, "old_identifier": None if add else lid})

    def set_state(self, pid, iid, name, actor=OWNER):
        sid = self.sid(pid, name)
        self.projs[pid]["items"][iid]["state"] = sid
        self.projs[pid]["acts"][iid].append({"field": "state", "actor": actor,
                                             "new_identifier": sid, "created_at": ts()})

    def say(self, pid, iid, actor, text):
        self.projs[pid]["comments"][iid].append(
            {"id": f"c{next(_clock)}", "actor": actor, "comment_html": f"<p>{text}</p>",
             "created_at": ts()})

    def state_of(self, pid, iid):
        sid = self.projs[pid]["items"][iid]["state"]
        return next(s["name"] for s in self.projs[pid]["states"] if s["id"] == sid)

    # -- the Plane client's interface --
    def workspace_members(self):
        return self.members

    def projects(self):
        return [{"id": k, "identifier": v["identifier"], "is_member": v["member"]}
                for k, v in self.projs.items()]

    def states(self, pid):
        if not self.projs[pid]["member"]:
            raise RuntimeError("403: You do not have permission to perform this action.")
        return self.projs[pid]["states"]

    def labels(self, pid):
        return self.projs[pid]["labels"]

    def items(self, pid):
        self.listed += 1
        return [dict(i) for i in self.projs[pid]["items"].values()]

    def item(self, pid, iid):
        if iid not in self.projs[pid]["items"]:
            raise PlaneError(404, {"error": "not found"}, "GET", f"/work-items/{iid}/")
        return dict(self.projs[pid]["items"][iid])

    def activities(self, pid, iid):
        return list(self.projs[pid]["acts"][iid])

    def comments(self, pid, iid):
        return list(self.projs[pid]["comments"][iid])

    def links(self, pid, iid):
        return self.projs[pid]["links"][iid]

    def update_item(self, pid, iid, body):
        self.writes.append(("update", iid, body))
        it = self.projs[pid]["items"][iid]
        for lid in set(it["labels"]) - set(body.get("labels", it["labels"])):
            self.projs[pid]["acts"][iid].append({
                "field": "labels", "actor": BOT, "created_at": ts(),
                "new_identifier": None, "old_identifier": lid})
        if "labels" in body:
            it["labels"] = list(body["labels"])
        if "state" in body:
            self.projs[pid]["items"][iid]["state"] = body["state"]
            self.projs[pid]["acts"][iid].append({"field": "state", "actor": BOT,
                                                 "new_identifier": body["state"],
                                                 "created_at": ts()})

    def comment(self, pid, iid, html):
        self.writes.append(("comment", iid, html))
        self.projs[pid]["comments"][iid].append(
            {"id": f"c{next(_clock)}", "actor": BOT, "comment_html": html, "created_at": ts()})


class FakeKube:
    def __init__(self, deny=None):
        self.created = []
        self.job_list = []
        self.deny = deny

    def jobs(self):
        return list(self.job_list)

    def console_image(self):
        return "ghcr.io/gjcourt/bench-cloud:2026-10-20-abc1234"

    def create(self, job, dry_run):
        if self.deny:
            raise RuntimeError(self.deny)
        self.created.append((job, dry_run))
