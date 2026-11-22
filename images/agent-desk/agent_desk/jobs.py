"""bench-cloud task Jobs: build one from scripts/bench-cloud/job.yaml, list them.

The template is the same file `bench-cloud run` uses (copied into the image at
build time), so identity, limits and network can't drift between George's
tasks and dispatched ones. The bench-cloud-task-jobs admission policy checks
the result either way.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
import time

import yaml

TICKET_LABEL = "agent-desk/ticket"
PLAYBOOK_LABEL = "agent-desk/playbook"
TASK_LABEL = "bench-cloud/task"
# Agent sessions (plan docs/plans/2026-11-08-agent-sessions-plan.md, step 2;
# George, 2026-11-22, D1-D3 on homelab#2003). A pilot Job's pod carries
# PILOT_LABEL, the one thing that lets it reach agent-sessions (D3); its token
# Secret carries TOKEN_LABEL, which the admission policy keys on (D1).
PILOT_LABEL = "agent-sessions/pilot"
TOKEN_LABEL = "agent-sessions/token"
TOKEN_KEY = "BENCH_SESSION_TOKEN"
# D2: "each per-Job token also expires on its own at its playbook deadline plus
# 15 minutes, besides being deleted when the Job ends".
TOKEN_GRACE = 15 * 60
# As agent-sessions' server.py: Plane work-item ids are UUIDs; a Job name is a
# Kubernetes name.
WORK_ITEM = re.compile(r"^[0-9a-fA-F-]{8,64}$")
JOB_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def token(key: bytes, work_item: str, job: str, expires: int) -> str:
    """The per-Job agent-sessions token: v1.<expires>.HMAC(key, work_item|job|expires).

    Mirrors images/agent-sessions/agent_sessions/server.py token() (another
    image, another build context); tests check the two agree.
    """
    # The fields are joined with "|", so each must be one that can't hold one
    # (agent-sessions checks the same patterns): no two tokens collide.
    if not WORK_ITEM.match(work_item) or not JOB_NAME.match(job):
        raise ValueError("token: bad work item id or Job name")
    sig = hmac.new(key, f"{work_item}|{job}|{int(expires)}".encode(), hashlib.sha256).hexdigest()
    return f"v1.{int(expires)}.{sig}"


def token_expires(deadline_seconds: int, now: float | None = None) -> int:
    return int((time.time() if now is None else now) + deadline_seconds + TOKEN_GRACE)


def token_secret_name(tid: str) -> str:
    return f"{tid}-session"


def token_secret(*, tid: str, namespace: str, work_item: str, key: bytes, deadline: int,
                 job_uid: str, now: float | None = None) -> dict:
    """The Job's token Secret, owned by the Job so it goes when the Job does
    (D1); the reaper also deletes it when the Job ends. Same shape as
    `python -m agent_sessions secret`."""
    expires = token_expires(deadline, now)
    meta = {"name": token_secret_name(tid), "namespace": namespace,
            "labels": {TOKEN_LABEL: "true", "agent-sessions/job": tid},
            "annotations": {"agent-sessions/expires": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                    time.gmtime(expires))}}
    if job_uid:
        meta["ownerReferences"] = [{"apiVersion": "batch/v1", "kind": "Job", "name": tid,
                                    "uid": job_uid}]
    return {"apiVersion": "v1", "kind": "Secret", "metadata": meta, "type": "Opaque",
            "stringData": {TOKEN_KEY: token(key, work_item, tid, expires)}}


def task_id(ticket_key: str) -> str:
    """ad-<ticket>-<date>-<hex>: like bench-cloud run's bt-… ids, and a valid
    Job name (lowercase, ≤63 chars)."""
    key = re.sub(r"[^a-z0-9]+", "-", ticket_key.lower()).strip("-")[:30]
    return f"ad-{key}-{time.strftime('%Y%m%d', time.gmtime())}-{secrets.token_hex(3)}"


def build_job(template: dict, *, tid: str, image: str, repo: str, prompt: str,
              ticket: dict, playbook: str, deadline: int, model: str = "",
              jobs_token_secret: str = "", sessions_url: str = "") -> dict:
    """sessions_url: set only for a session pilot Job. It then also gets the
    pilot pod label and its token from <tid>-session; any other Job gets none
    of the three."""
    job = copy.deepcopy(template)
    if sessions_url:
        ticket = {**ticket, "sessions_url": sessions_url}
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
    if sessions_url:
        pod["metadata"]["labels"][PILOT_LABEL] = "true"
        meta["labels"][PILOT_LABEL] = "true"
    spec = pod["spec"]
    for c in spec.get("initContainers", []) + spec["containers"]:
        c["image"] = image
    env = spec["containers"][0].setdefault("env", [])
    env += [
        {"name": "TASK_ID", "value": tid},
        {"name": "TASK_REPO", "value": repo},
        {"name": "TASK_PROMPT", "value": prompt},
        {"name": "TASK_TICKET", "value": json.dumps(ticket, sort_keys=True)},
        # Recorded in the run's meta.json for the bench fleet dashboard's
        # exporter (HOMELAB-94), which has no Job to read the label from once
        # the Job is gone.
        {"name": "TASK_PLAYBOOK", "value": playbook},
    ]
    if model:
        env.append({"name": "TASK_MODEL", "value": model})
    if jobs_token_secret:
        # The Jobs' own Plane key (plan step 5). Optional until that secret
        # exists, so a ticket Job still starts, just without Plane access.
        env.append({"name": "PLANE_API_KEY", "valueFrom": {"secretKeyRef": {
            "name": jobs_token_secret, "key": "PLANE_API_KEY", "optional": True}}})
    if sessions_url:
        # Not optional: the pod waits for its Secret, which agent-desk creates
        # right after the Job, rather than starting as a phase-1 Job.
        env.append({"name": TOKEN_KEY, "valueFrom": {"secretKeyRef": {
            "name": token_secret_name(tid), "key": TOKEN_KEY, "optional": False}}})
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
        self.core = client.CoreV1Api()
        self._api_exception = client.exceptions.ApiException
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
                "pilot": (j.metadata.labels or {}).get(PILOT_LABEL) == "true",
            })
        return out

    def console_image(self) -> str:
        d = self.apps.read_namespaced_deployment(self.console, self.ns)
        return next(c.image for c in d.spec.template.spec.containers if c.name == "console")

    def create(self, job: dict, dry_run: bool) -> str:
        """Create the Job; its uid (the API server assigns one on a dry run too)."""
        res = self.batch.create_namespaced_job(self.ns, job, dry_run="All" if dry_run else None)
        return getattr(getattr(res, "metadata", None), "uid", "") or ""

    def create_secret(self, secret: dict, dry_run: bool) -> None:
        self.core.create_namespaced_secret(self.ns, secret, dry_run="All" if dry_run else None)

    def delete_secret(self, name: str) -> None:
        """Delete a token Secret; already gone is fine."""
        try:
            self.core.delete_namespaced_secret(name, self.ns)
        except self._api_exception as e:
            if e.status != 404:
                raise

    def delete_job(self, name: str) -> None:
        self.batch.delete_namespaced_job(name, self.ns, propagation_policy="Background")
