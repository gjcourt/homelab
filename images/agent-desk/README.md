# agent-desk

The dispatcher for the agent desk loop (lab 03-042). It turns Plane tickets
that George has marked `ready` into bench-cloud task Jobs. Plan:
[`docs/plans/2026-10-22-agent-desk-loop.md`](../../docs/plans/2026-10-22-agent-desk-loop.md).

It keeps no state of its own. Plane (states, labels, comments, links) and the
Jobs' labels (`agent-desk/ticket`, `agent-desk/playbook`) are the record, so
a restart re-derives everything from one poll.

## What it does

| Input | Handling |
| --- | --- |
| Plane webhook, `POST /plane` | Checks `X-Plane-Signature` (HMAC-SHA256 of the body), then queues the ticket. It's a hint: the worker re-reads Plane before acting |
| Poll, every `pollInterval` (10m) | Every item in every allowlisted project, oldest first. The backstop: Plane retries only on connection errors and turns the webhook off after its retries |
| GitHub poll, every `githubPollInterval` (5m) | Tickets In review with a PR link: merged → Done, closed → Todo. Only when a token is configured |

One worker thread makes every decision, so two events for one ticket can't
both start a Job.

**The startable rule** (`agent_desk/rules.py`, in the plan's order):

1. The project is in `projects`, and the item has no `external_source`.
2. Exactly one `playbook:<name>` label, naming a configured playbook.
3. `ready`, **added by George**: the latest activity on the `ready` label's id is an add whose actor is George's user id.
4. The project's repo isn't in `denyRepos`.
5. No active Job for the ticket, and it isn't In Progress or In review. A Blocked ticket starts again only after a newer comment from George.
6. The playbook has fewer than `maxConcurrent` running, unless George added `over-cap`. Otherwise the ticket goes to Awaiting approval with one comment.

Rules 2 and 4 get one comment on the ticket. Every other miss stays silent.

**After a Job ends:** a ticket still In Progress moves to Blocked, with a comment naming the run record.

## Config

`config.yaml`, mounted from a ConfigMap reviewed in this repo. The env
supplies `PLANE_URL`, `PLANE_WORKSPACE`, `DRY_RUN_JOBS`, and the `*_FILE`
secrets (`PLANE_API_KEY_FILE`, `WEBHOOK_SECRET_FILE`, `GITHUB_TOKEN_FILE`).
With `DRY_RUN_JOBS=true`, Jobs are created with `dryRun=All`: the API server
runs RBAC, admission and quota checks but stores nothing. Plane writes are
still real.

`repoNotes` (optional) maps `owner/repo` to text that is appended to every
playbook prompt for that repo, ahead of the ticket. homelab uses it to tell
the agent which paths a bench PR may change there (HOMELAB-85,
`scripts/bench-gate/rules.yaml`), so a playbook such as `docs-fix` doesn't try
a file the gate would refuse.

## Develop

```bash
cd images/agent-desk
uv run --python 3.12 --with pyyaml --with pytest python -m pytest -q tests
```
