# agent-sessions

The session service for bench-cloud Jobs (lab 03-042 phase 2). Plan:
[`docs/plans/2026-11-08-agent-sessions-plan.md`](../../docs/plans/2026-11-08-agent-sessions-plan.md),
"Where the events go: the session service".

**This is step 2's skeleton.** It checks each Job's token, validates and
re-redacts its events, and writes one JSON line per event to its log. It
stores nothing and relays nothing. Step 3A gives it a store and plane-mobile's
Sessions pane reads from it.

## API

| Call | What it does |
| --- | --- |
| `POST /sessions/<work item id>/events` | A Job's batch: `{"job": "<Job name>", "events": [...]}`, 1 to 100 events, at most 256 KiB. `202 {"accepted": n}` |
| `GET /sessions/<work item id>/messages?job=<Job name>` | George's messages for `poll()`. Always `{"messages": []}` until step 3A; the hook still polls Plane |
| `GET /healthz` | `200 {"ok": true}` |

**Auth, per Job and stateless.** Both session calls need
`Authorization: Bearer <token>`, where the token is
`HMAC-SHA256(session-key, "<work item id>|<Job name>")` as lowercase hex. The
service recomputes it, so a token works for one ticket and one Job only. A
wrong token is `401` and is logged as refused, with no event content.

**Event shapes**, as the plan's "Job side: session events" table, with a
`type` field added to tell them apart. Unknown fields are dropped; a wrong
type is `400`.

```json
{"type": "progress", "tool": "Bash", "summary": "git status", "at": "2026-11-22T10:00:00Z"}
{"type": "plan", "items": [{"content": "fix the test", "status": "in_progress"}], "at": "…"}
{"type": "say", "text": "Opened PR #3.", "at": "…"}
{"type": "ask", "text": "Which branch should this target?", "at": "…"}
```

- `progress`: `tool` is the Claude Code tool name and `summary` its fixed-form
  description (at most 120 characters from the Job): the path for file tools,
  the first line of a Bash command, the pattern or query for search, the
  action for Plane tools. Never the tool's output or a file's contents.
- `plan`: the whole task list as the agent last wrote it; each send carries
  the latest one only.
- `say` / `ask`: the agent's message, as posted on the ticket.

**Log records.** Each event is logged as
`{"logged", "session", "job", ...event}`. If the service's own redaction
changes a field, it first logs
`{"warning": "redacted on arrival", "session", "job", "type", "fields"}`,
naming the fields, never the value. That means the Job's redaction missed
something.

## The Job side

`bench-ticket hook` (images/bench-cloud) sends the events, and
`bench-session say|ask` the messages, only when the Job has both:

- `sessions_url` in `TASK_TICKET`, e.g. `http://agent-sessions.agent-desk-stage.svc:8080`;
- `BENCH_SESSION_TOKEN` in its env, from a Secret.

Without both, the Job is exactly phase 1: no events, no extra prompt text.

The hook sends at most once per 10 s per Job, holding the latest plan and up
to 20 progress events between sends. `say` and `ask` go at once, with
anything held; the Stop hook sends what's left.

## Redaction

`agent_sessions/redact.py` holds the patterns: token shapes (GitHub, Anthropic,
Plane, Slack, AWS, JWTs, private keys, 32+ hex characters, 32+ mixed
characters) and assignments (`NAME=value` for secret-looking names, `--token
value`, `Bearer …`, URL passwords). The Job also replaces the value of every
env var with a secret-looking name. The two copies of the list must stay the
same, and a bench-cloud test checks they do.

Known over-redaction: 40-character git SHAs count as long hex.

## Run, test

```bash
cd images/agent-sessions
python -m pytest -q tests                     # stdlib + pytest
SESSION_KEY_FILE=/path/to/key python -m agent_sessions   # serve on :8080
```

Env: `SESSION_KEY_FILE` (required; every call is refused without a key),
`PORT` (8080), `EVENT_LOG` (an extra file to append the lines to).

## Step 2's exit check

The plan's exit: "a hand-launched SANDBOX Job's events reach the service's log,
with no secret-shaped strings in them (scripted check). The same ticket run
without the session token behaves exactly as phase 1."

This needs the staging manifests first (not in this PR, see below). Then:

1. Mint the Job's token inside the service's pod, so the key never leaves it,
   and apply it as the Job's Secret:

   ```bash
   kubectl -n agent-desk-stage exec deploy/agent-sessions -- \
     python -m agent_sessions secret --work-item <work item id> --job <Job name> \
     | kubectl apply -f -
   ```

   This creates `<Job name>-session` in `bench-cloud` with `BENCH_SESSION_TOKEN`.
2. Launch the Job from agent-desk's Job spec as for phase-1 step 5b, with
   `"sessions_url": "http://agent-sessions.agent-desk-stage.svc:8080"` in
   `TASK_TICKET` and
   `BENCH_SESSION_TOKEN` from `secretKeyRef: {name: <Job name>-session, key: BENCH_SESSION_TOKEN}`.
3. When it ends:

   ```bash
   kubectl -n agent-desk-stage logs deploy/agent-sessions --since=24h \
     | python3 -m agent_sessions.check --work-item <id> --job <Job name>
   ```

   It passes with at least one progress event, every event in shape, nothing
   secret-shaped, and no "redacted on arrival" warning. `--secret-env NAME`
   also checks that a named env var's value (export it in the checking shell)
   appears nowhere; values are compared, never printed.
4. The phase-1 half: the same ticket, a second Job with neither field. The
   check with `--expect-none --job <second Job>` passes only if it sent
   nothing. The run itself should look as phase 1 does: the same comments,
   states and run record, with no `bench-session` instructions given to it.

## Not in step 2

- **Manifests**: a Deployment, Service, CiliumNetworkPolicy and a
  `session-key` `.yaml.example` for `agent-desk-stage`, and an egress rule
  from bench-cloud Jobs to it (bench-cloud's policy is production). Follow-up.
- **agent-desk minting the token** per Job (its own Secret, owned by the Job).
- **Refusing tokens of Jobs that aren't active** (plan, Security). The token
  is a pure HMAC, so the service can't tell without asking the cluster or
  agent-desk; that's decided with the manifests.
