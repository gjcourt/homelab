"""Dispatcher config: the reviewed rules (a ConfigMap in homelab) plus env.

The ConfigMap lives in apps/*/agent-desk/, which is outside bench-cloud's
paths in homelab (scripts/bench-gate/rules.yaml), so nothing here can be
widened by an agent: the project → repo map, the deny list, the playbooks and
the repo notes all come from this file, never from ticket text.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml

_DURATION = re.compile(r"^(\d+)([smh])$")


def seconds(value) -> int:
    """'90m' / '2h' / '300s' / 7200 → seconds."""
    if isinstance(value, int):
        return value
    m = _DURATION.match(str(value).strip())
    if not m:
        raise ValueError(f"bad duration {value!r} (want e.g. 2h, 90m, 300s)")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


@dataclass
class Playbook:
    name: str
    ready: str = "ready"  # "ready" (George's label) | "auto" (not used in phase 1)
    max_concurrent: int = 6
    deadline: int = 7200
    model: str = ""
    allowed_repos: list[str] = field(default_factory=list)  # empty = every mapped repo
    prompt: str = ""


@dataclass
class Config:
    plane_url: str
    workspace: str
    owner_email: str
    bot_email: str
    projects: dict[str, str]  # Plane project identifier → owner/repo
    deny_repos: set[str]
    playbooks: dict[str, Playbook]
    dry_run_jobs: bool
    # owner/repo → text appended to every playbook prompt for that repo, e.g.
    # the paths a bench PR may change there (HOMELAB-85).
    repo_notes: dict[str, str] = field(default_factory=dict)
    job_namespace: str = "bench-cloud"
    job_template: str = "/app/job.yaml"
    console_deployment: str = "bench-console"
    run_record: str = "hestia:agent-inbox/runs/{task_id}/"
    poll_seconds: int = 600
    github_poll_seconds: int = 300
    port: int = 8080
    plane_token: str = ""
    jobs_token_secret: str = "bench-cloud-plane-jobs-token"
    webhook_secret: str = ""
    github_token: str = ""
    # George's GitHub login: his "request changes" review wakes the agent.
    # Empty turns review wakes off.
    github_owner: str = ""
    # Phone notifications (agent_desk/notify.py): Home Assistant's base URL and
    # the webhook id. Either empty means no notifications.
    notify_url: str = ""
    notify_webhook: str = ""
    # Plane's browser URL, for the link in a notification ({base}/{ws}/browse/{key}/).
    plane_web_url: str = ""


def _read_file(env: str) -> str:
    """A secret mounted as a file; '' when unset or absent (an optional key)."""
    path = os.environ.get(env, "")
    if not path or not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read().strip()


def load(path: str | None = None, env=os.environ) -> Config:
    path = path or env.get("AGENT_DESK_CONFIG", "/config/config.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    playbooks = {}
    for name, pb in (raw.get("playbooks") or {}).items():
        pb = pb or {}
        if pb.get("ready", "ready") not in ("ready", "auto"):
            raise ValueError(f"playbook {name}: ready must be 'ready' or 'auto'")
        playbooks[name] = Playbook(
            name=name,
            ready=pb.get("ready", "ready"),
            max_concurrent=int(pb.get("maxConcurrent", 6)),
            deadline=seconds(pb.get("deadline", "2h")),
            model=pb.get("model", "") or "",
            allowed_repos=list(pb.get("allowedRepos") or []),
            prompt=pb.get("prompt", "") or "",
        )
    cfg = Config(
        plane_url=env.get("PLANE_URL", raw.get("planeUrl", "")),
        workspace=env.get("PLANE_WORKSPACE", raw.get("workspace", "")),
        owner_email=raw["ownerEmail"],
        bot_email=raw["botEmail"],
        projects=dict(raw.get("projects") or {}),
        deny_repos=set(raw.get("denyRepos") or []),
        playbooks=playbooks,
        dry_run_jobs=str(env.get("DRY_RUN_JOBS", "false")).lower() == "true",
        repo_notes={str(k): str(v or "") for k, v in (raw.get("repoNotes") or {}).items()},
        poll_seconds=seconds(raw.get("pollInterval", "10m")),
        github_poll_seconds=seconds(raw.get("githubPollInterval", "5m")),
        port=int(env.get("PORT", "8080")),
        plane_token=_read_file("PLANE_API_KEY_FILE"),
        webhook_secret=_read_file("WEBHOOK_SECRET_FILE"),
        github_token=_read_file("GITHUB_TOKEN_FILE"),
        notify_url=env.get("NOTIFY_URL", ""),
        notify_webhook=_read_file("NOTIFY_WEBHOOK_FILE"),
        plane_web_url=raw.get("planeWebUrl", ""),
        github_owner=raw.get("githubOwner", "") or "",
    )
    for key in ("job_namespace", "job_template", "console_deployment", "run_record",
                "jobs_token_secret"):
        camel = re.sub(r"_(\w)", lambda m: m.group(1).upper(), key)
        if camel in raw:
            setattr(cfg, key, raw[camel])
    if not cfg.plane_url or not cfg.workspace:
        raise ValueError("PLANE_URL and PLANE_WORKSPACE are required")
    return cfg
