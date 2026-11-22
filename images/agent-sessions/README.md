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
| `POST /sessions/<work item id>/events` | A Job's batch: `{"job": "<Job name>", "events": [...]}`, 1 to 100 events, at most 256 KiB. `202 {"accepted": n}`. A missing `Content-Length` is `411`, a bad one `400`, too large `413`, all before the body is read |
| `GET /sessions/<work item id>/messages?job=<Job name>` | George's messages for `poll()`. Always `{"messages": []}` until step 3A; the hook still polls Plane |
| `GET /healthz` | `200 {"ok": true}` |

**Auth, per Job and stateless.** Both session calls need
`Authorization: Bearer <token>`, where the token is
`HMAC-SHA256(session-key, "<work item id>|<Job name>")` as lowercase hex. The
service recomputes it, so a token works for one ticket and one Job only. A
wrong token (including a non-ASCII one) is `401` and is logged as refused,
with no event content. The service refuses to start with a `session-key`
under 32 bytes (`openssl rand -hex 32` makes one).

**Limits.** A client gets 10 s to send its request, and at most 32 requests
are handled at once; more wait to be accepted.

**Event shapes**, as the plan's "Job side: session events" table, with a
`type` field added to tell them apart. Unknown fields are dropped; a wrong
type is `400`. `at` must be ISO-8601 UTC, `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`.

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
anything held; the Stop hook sends what's left. A send has a 2 s timeout and
runs outside the hook's state lock. A network error, a 5xx, a 408 or a 429
keeps the batch for the next send. Any other 4xx drops it with a line on
stderr, since resending can't help. The Job clamps every field to the limits
above before sending.

## Redaction

`agent_sessions/redact.py` holds three lists of `(name, pattern)`:

- **VENDOR**: token shapes a vendor issues (GitHub, GitLab, Anthropic,
  OpenAI-style, Stripe live keys, Plane, Slack incl. `xapp-`, Hugging Face,
  npm, AWS key ids, JWTs, private keys);
- **GENERIC**: 32+ hex characters, 32+ characters mixing cases and digits;
- **ASSIGN**: a secret by context: `NAME=value` and `"name": "value"` for
  secret-looking names, `--token value`, a long value after a secret-looking
  word, `Authorization: <scheme> X`, `Bearer …`, `mysql -p…`, `sshpass -p`,
  `curl -u user:pw`, URL passwords.

**Two levels.** Progress summaries and plan items get all three (the full
level). `say`/`ask` text is also posted to Plane, so it gets VENDOR only (the
message level): commit SHAs, branch names and prose such as "the key: …"
survive there. On the Job, both levels also replace the value of every env var
with a secret-looking name (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASS*`,
`*APIKEY*`, `*AUTH*`, …).

Every quantifier is bounded and every string is capped at 20,000 characters
before a pattern runs (tests: 200,000 characters of `x-` in under 0.2 s). The
Job's copy of the lists must stay the same, and a bench-cloud test checks it.

Known over-redaction at the full level: 40-character git SHAs count as long
hex.

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
   secret-shaped, no "redacted on arrival" warning and no refused request from
   the Job.

   **Also pass `--secret-env`** for the run's real credentials, so the check
   compares their values too, not only their shapes. Export each in the
   checking shell without echoing it, then e.g.
   `--secret-env PLANE_API_KEY --secret-env CLAUDE_CODE_OAUTH_TOKEN`. Values
   are compared, never printed.
4. Delete the token's Secret when the test is done:
   `kubectl -n bench-cloud delete secret <Job name>-session`.
5. The phase-1 half: the same ticket, a second Job with neither field. The
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
