"""bench-cloud task Jobs: build one from scripts/bench-cloud/job.yaml, list them.

The template is the same file `bench-cloud run` uses (copied into the image at
build time), so identity, limits and network can't drift between George's
tasks and dispatched ones. The bench-cloud-task-jobs admission policy checks
the result either way.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import time

import yaml

TICKET_LABEL = "agent-desk/ticket"
PLAYBOOK_LABEL = "agent-desk/playbook"
TASK_LABEL = "bench-cloud/task"


def task_id(ticket_key: str) -> str:
    """ad-<ticket>-<date>-<hex>: like bench-cloud run's bt-… ids, and a valid
    Job name (lowercase, ≤63 chars)."""
    key = re.sub(r"[^a-z0-9]+", "-", ticket_key.lower()).strip("-")[:30]
    return f"ad-{key}-{time.strftime('%Y%m%d', time.gmtime())}-{secrets.token_hex(3)}"


def build_job(template: dict, *, tid: str, image: str, repo: str, prompt: str,
              ticket: dict, playbook: str, deadline: int, model: str = "",
              jobs_token_secret: str = "") -> dict:
    job = copy.deepcopy(template)
    meta = job["metadata"]
    meta["name"] = tid
    labels = {"app": "bench-task", TASK_LABEL: tid, TICKET_LABEL: ticket["work_item_id"],
              PLAYBOOK_LABEL: playbook}
    meta.setdefault("labels", {}).update(labels)
    meta.setdefault("annotations", {}).update({
        "bench-cloud/repo": repo, "agent-desk/ticket-key": ticket["key"]})
    job["spec"]["activeDeadlineSeconds"] = deadline
    pod = job["spec"]["template"]
    pod.setdefault("metadata", {}).setdefault("labels", {}).update(labels)
    spec = pod["spec"]
    for c in spec.get("initContainers", []) + spec["containers"]:
        c["image"] = image
    env = spec["containers"][0].setdefault("env", [])
    env += [
        {"name": "TASK_ID", "value": tid},
        {"name": "TASK_REPO", "value": repo},
        {"name": "TASK_PROMPT", "value": prompt},
        {"name": "TASK_TICKET", "value": json.dumps(ticket, sort_keys=True)},
    ]
    if model:
        env.append({"name": "TASK_MODEL", "value": model})
    if jobs_token_secret:
        # The Jobs' own Plane key (plan step 5). Optional until that secret
        # exists, so a ticket Job still starts, just without Plane access.
        env.append({"name": "PLANE_API_KEY", "valueFrom": {"secretKeyRef": {
            "name": jobs_token_secret, "key": "PLANE_API_KEY", "optional": True}}})
    return job


def load_template(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


class Kube:
    """The three calls the dispatcher makes, behind one small class so tests
    can swap in a fake."""

    def __init__(self, namespace: str, console_deployment: str):
        from kubernetes import client, config  # imported here: tests don't need it
        config.load_incluster_config()
        self.batch = client.BatchV1Api()
        self.apps = client.AppsV1Api()
        self.ns = namespace
        self.console = console_deployment

    def jobs(self) -> list[dict]:
        """Every dispatcher Job in the namespace, as plain dicts."""
        res = self.batch.list_namespaced_job(self.ns, label_selector=TICKET_LABEL)
        out = []
        for j in res.items:
            st = j.status
            out.append({
                "name": j.metadata.name,
                "created": j.metadata.creation_timestamp.isoformat()
                if j.metadata.creation_timestamp else "",
                "ticket": (j.metadata.labels or {}).get(TICKET_LABEL),
                "playbook": (j.metadata.labels or {}).get(PLAYBOOK_LABEL),
                "active": not any(c.type in ("Complete", "Failed") and c.status == "True"
                                  for c in (st.conditions or [])),
                "failed": bool(st.failed),
                "succeeded": bool(st.succeeded),
                "reason": next((c.reason for c in (st.conditions or [])
                                if c.type == "Failed" and c.status == "True"), ""),
            })
        return out

    def console_image(self) -> str:
        d = self.apps.read_namespaced_deployment(self.console, self.ns)
        return next(c.image for c in d.spec.template.spec.containers if c.name == "console")

    def create(self, job: dict, dry_run: bool) -> None:
        self.batch.create_namespaced_job(self.ns, job, dry_run="All" if dry_run else None)
