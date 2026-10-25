---
status: in-progress
last_modified: 2026-10-24
lab: 03-042
summary: "The agent desk loop (lab 03-042 phase 1): a stateless in-cluster dispatcher turns ready Plane tickets into bench-cloud Jobs (up to 24 pods, 6 per playbook without asking), links each PR back to its ticket, and carries questions and mid-run comments through the ticket; sandbox repo first; docs-fix, dep-hold and main-ci playbooks"
---

# Agent desk: Plane tickets worked by bench-cloud agents

## Goal

Lab brief 03-042: "write a plan, commit it to GitHub, turn it into tickets, and let agents work the
tickets into pull requests". The pieces under it are done:

- Plane runs in production (`plane.burntbytes.com`, #1624) with a rehearsed restore
  ([Plane runbook §5](../operations/apps/plane.md#5-restore-drill-plan-step-6--2026-10-19)).
- bench-cloud runs task Jobs (`scripts/bench-cloud/job.yaml`, [bench-cloud plan](2026-09-25-bench-cloud-agent.md)).
- plane-sync mirrors lab briefs and repo plans into Plane (#1631, #1640; first round trip
  gjcourt/lab#111 on 2026-10-22).
- A bench-cloud pod has already read and updated a staging ticket over REST and over MCP stdio
  ([staging plan, exit criteria](2026-10-18-plane-staging.md#exit-criteria-staging)).

This plan covers **phase 1 only**: the loop that connects them. Matrix sessions (phase 2) are out of
scope; see [Phase 2 pointer](#phase-2-pointer).

```text
George labels a ticket "ready"
        │  Plane webhook (signed)  +  slow API poll as backstop
        ▼
dispatcher ── checks: playbook, ready by George, repo not denied, no live Job, caps
        │  creates Job in bench-cloud (ticket as the brief)
        ▼
bench-cloud Job ── Plane MCP: reads ticket, comments, attaches PR link
        │  babysit + critique, then ticket → "In review"
        ▼
George merges ── dispatcher sees the merge on GitHub ── ticket → "Done"
```

## Decisions (George, 2026-10-18, from the brief)

- **Tracker:** self-hosted Plane.
- **Ready gate:** per playbook. Each playbook declares whether its tickets start on their own or
  wait for George's `ready` label.
- **Homelab repo out of scope for agent work** until bench-watch exists (the deferred merge-audit
  fix in the [bench-cloud plan](2026-09-25-bench-cloud-agent.md#risks)).
- **The ticket is the chat** in phase 1: questions and mid-run messages go through ticket comments.

## Decisions (George, 2026-10-22, on this plan)

| # | Question | Decision |
| --- | --- | --- |
| 1 | Repos first | **A sandbox first.** A throwaway repo (proposed `gjcourt/agent-sandbox`) with its own Plane project carries the first end-to-end runs. Real repos are added after the milestone passes |
| 2 | Auto-start | **Neither playbook auto-starts.** `docs-fix`, `dep-hold` (and `main-ci`, below) all wait for `ready`. Revisit later |
| 3 | Third playbook | **Failing CI on main** (`main-ci`): a red default branch opens a ticket; the agent investigates and proposes a fix PR |
| 4 | Renovate fixer | **It opens `dep-hold` tickets** instead of launching Jobs directly, so all agent work goes through Plane |
| 5 | Concurrency | George: "we should be able to run up to 24 concurrent pods, w/ no more than 6 pods on one workflow w/out asking permission". **24 agent pods in total; 6 Jobs per playbook**, more only with George's approval |
| 6 | Notifications | **Plane notifications only** in phase 1. George: "let's bias towards plane first, and then when we get the chat server up and running, we can think through alternate notification mechanisms" |

## Design

### Where agents work

| Plane project | Agent tickets? | Why |
| --- | --- | --- |
| **SANDBOX** (`gjcourt/agent-sandbox`, decision 1) | **Yes, first** | Throwaway repo: a wrong PR costs nothing |
| Other repo projects except HOMELAB (DOMESTIQUE today; more as repos are added) | **After the milestone** (decision 1) | One project per repo, so the project decides the repo |
| HOMELAB | **No** | Deny list, until bench-watch |
| Lab categories (AUDIO, AUTO, COFFEE, CROSS, FIN, PIANO, WOOD) | **No** | Briefs are long-form ideas whose status plane-sync owns, not units of agent work |

- **The repo comes from the project, never from the ticket text.** The dispatcher maps project
  identifier → `gjcourt/<repo>` from its own config. A ticket can't name a different repo.
- **Enforced twice:** the dispatcher's allowlist of projects, and Plane project membership. The
  `agent-desk` bot (`gjcourt+agent-desk@gmail.com`, a workspace Member since 2026-10-21) is added
  only to the projects agents work in. Step 2 verifies that a Member's token can't read or write a
  project it isn't a member of.

**Coexisting with plane-sync.** Agent tickets are ordinary Plane items with no `external_source`.
plane-sync counts those as `plane_only_items` and never edits them
(`images/plane-sync/plane_sync/sync.py`, `items_by_external`). In DOMESTIQUE the sync has no plans
configured (`REPO_PROJECTS`: `domestique:DOMESTIQUE:Domestique`), so it only ensures the project
exists. When a repo project also mirrors plans, plan items carry `external_source: plan:<repo>`; the
dispatcher ignores any item that has an `external_source`, so the two never touch the same item.
The SANDBOX project is created by hand in Plane, not through plane-sync: the sandbox has no plans to
mirror.

### Dispatcher

A small service, `agent-desk`, in its own namespace.

| Choice | Decision | Why |
| --- | --- | --- |
| Language | **Python 3.12**, stdlib + PyYAML + the official `kubernetes` client | Reuses plane-sync's Plane client: 1 req/s pacing, `Retry-After`, 409 handling, description normalisation, all already tested against Plane 1.4.2 |
| Source | **`images/agent-desk/`** in this repo, built by `build-agent-desk.yml` | The house pattern (plane-sync, bench-cloud). This reverses the staging plan's "outside this repo": while homelab is on the deny list, no agent can edit the dispatcher that gates agents |
| Namespace | **`agent-desk`** (prod), **`agent-desk-stage`** (staging) | bench-cloud has no ingress at all; webhooks need some. A separate namespace keeps that true |
| State | **None of its own.** Plane (state, labels, links, comments) and Kubernetes (Jobs labelled with the ticket id and playbook) are the record | No PVC means no exposure to the iSCSI read-only failure mode ([AGENTS.md](../../AGENTS.md#recovering-read-only-iscsi-volumes-recurring)). A restart re-derives everything with one full poll |
| Creating Jobs | A ServiceAccount `agent-desk` bound by a **RoleBinding in `bench-cloud`** to the existing `bench-task-launcher` Role (Jobs: create, delete, get, list, watch; that namespace only) | The existing admission policy already pins every Job there to `serviceAccountName: bench-agent` and `priorityClassName: bench-cloud-task`, so the dispatcher can't start a privileged task or one the quota doesn't count |
| Plane identity | The **`agent-desk`** bot, with **two API tokens**: one for the dispatcher, one for Jobs | Bot actions are visibly not George's; each token is revocable on its own. Plane's limit is per key (`API_KEY_RATE_LIMIT`, default 60/min), so two keys also halve contention |
| GitHub identity | A **read-only** fine-grained PAT: Pull requests read and Actions read, on the in-scope repos | It sees whether a linked PR merged or closed, and whether a default branch's CI is red (`main-ci`). Plane's GitHub integration isn't in CE |

**Inputs.**

- **Webhook** at `http://agent-desk.agent-desk.svc.cluster.local:<port>/plane`, registered as a Plane
  workspace webhook for work items and comments.
  - Verify `X-Plane-Signature` (HMAC) against a SOPS secret; reject anything unsigned or wrong.
  - Production's `WEBHOOK_ALLOWED_HOSTS` gets exactly that one name, in `apps/production/plane/release-patch.yaml`,
    which already reserves the spot. Never a CIDR.
- **Backstop poll** every 10 minutes: list items in the allowlisted projects and re-evaluate.
  Plane retries a delivery up to 5 times and then drops it, so this is what catches an outage.
- **GitHub poll** every 5 minutes:
  - the PR state of tickets in "In review";
  - the latest default-branch workflow runs of in-scope repos (`main-ci` trigger).

**The startable rule.** A ticket starts only if **all** hold:

1. It's in an allowlisted project, and has no `external_source`.
2. It carries exactly one `playbook:<name>` label naming a playbook the dispatcher knows.
3. **A `ready` label applied by George's Plane account.** The dispatcher checks the actor in the
   item's activity. An agent can add labels through MCP, and the gate must not be one it can open
   itself. Every playbook is ready-gated (decision 2). The `auto` rule stays in the format for later,
   and when used, starts only tickets **created** by George or an allowlisted automation identity,
   never by `agent-desk`.
4. The project's repo is not on the deny list (`gjcourt/homelab`).
5. No Job labelled with this ticket id is active, and the ticket isn't already In Progress, In review
   or Blocked.
6. The **playbook's cap** isn't reached: fewer than its `maxConcurrent` (6) Jobs labelled with that
   playbook are active, unless George approved this ticket past the cap (below).

If rule 2 or 4 fails, the dispatcher leaves one comment saying which. A missing playbook or a denied
repo is something George would want to know. Other failures stay silent until they change.

**Concurrency (decision 5).**

| Limit | Value | Enforced by |
| --- | --- | --- |
| All agent pods | **24** | bench-cloud's `ResourceQuota` (raised from 8, step 3a). Covers dispatcher Jobs and George's own `bench-cloud run` tasks alike; a pod over it waits in the Job queue, as today |
| One playbook ("workflow") | **6 Jobs**, without asking | The dispatcher, counting active Jobs labelled `agent-desk/playbook=<name>` |
| Above 6 for one playbook | Only with George's approval | The dispatcher asks; George approves per ticket or raises the cap |

- **A ticket over its playbook's cap waits; it doesn't start.**
  - The dispatcher moves it to a custom state **Awaiting approval** (group `unstarted`).
  - It comments once: "`<playbook>` is at 6/6 running. Add `over-cap` to start this one now, or it
    starts when a slot frees."
- **Two ways forward:**
  - When a slot frees, the oldest waiting ticket for that playbook starts. No approval is needed:
    it's within 6.
  - George adds the label **`over-cap`**. The dispatcher checks it was applied by George, as with
    `ready`, and starts the ticket above 6, still inside the quota of 24.
- **A standing raise** is a change to the playbook's `maxConcurrent` in the dispatcher's ConfigMap,
  which lives in this repo. It's a PR George approves, and agents can't edit homelab.
- Three playbooks at 6 each is **18 dispatcher Jobs** at most without approval, leaving at least 6 of
  the 24 for George's own tasks.

**Launching.** The Job is `scripts/bench-cloud/job.yaml` plus:

- labels `agent-desk/ticket=<work-item uuid>` and `agent-desk/playbook=<name>`;
- `TASK_REPO` from the project map;
- `TASK_PROMPT` built from the playbook's template plus the ticket's title, description and
  comments, the ticket text fenced and marked as untrusted input;
- `TASK_TICKET` (project id and work-item id), the Jobs' Plane token, and the playbook's deadline.

On create, the ticket moves to **In Progress** with a comment naming the Job.

**When a Job ends**, the dispatcher reads the ticket:

| Ticket after the Job | Dispatcher does |
| --- | --- |
| In review, with a PR link | Nothing; the GitHub poll takes over |
| In review, no PR, with a findings comment (a playbook's no-PR outcome) | Nothing; George reads it and closes the ticket |
| Blocked, with a question comment | Nothing; waits for George |
| Anything else (crash, deadline, no PR and no findings) | Moves it to **Blocked** with a comment: exit code, and where the record is (`hestia:agent-inbox/runs/<task-id>/`) |

### Ticket ⇄ PR

- **The agent** opens the PR from a new branch, names the ticket in the PR body, attaches the PR
  URL as a work-item link (MCP link tool), runs babysit and critique, and only then sets the ticket
  to **In review**.
- **The dispatcher**, polling GitHub:
  - merged → **Done**;
  - closed without merging → back to **Todo**, with a comment.
- New states are created per in-scope project: **In review** (group `started`), **Blocked** (group
  `started`) and **Awaiting approval** (group `unstarted`).

### Questions and the ticket as chat

- **Question loop.** An agent that needs George comments its question and sets the ticket to
  **Blocked**, then exits. A new comment **by George** on a Blocked ticket re-dispatches it. The new
  Job gets the whole comment thread, so it resumes with the answer. The dispatcher ignores comments
  by `agent-desk`.
  Step 5b widens this: any comment from George wakes the agent, the woken Job resumes the earlier
  session, and an agent can wait in-run for an answer (see "Waking the agent").
- **Mid-run comments.** A Claude Code `PostToolUse` hook in ticket Jobs checks the ticket for new
  comments by George and passes them to the agent as added context. That puts a comment in front of
  the agent mid-task, not only at the next dispatch.
  - It reads through a small **conversation-channel interface** (`poll() -> new messages`,
    `post(text)`), with Plane comments as its only implementation, so phase 2 can add Matrix
    without rewriting it.
  - Rate: at most one Plane call per 30 seconds per Job, whatever the tool-call rate. At 24 Jobs
    that is up to 48 requests/min on the Jobs' key, close to the default 60/min, so step 3a also
    raises `API_KEY_RATE_LIMIT` (a chart setting; see Risks).
  - Step 4 confirms against the Claude Code version pinned in the image that a hook can inject
    context this way.
- **What the Job must change.** Today `bench-task` tells Claude "Nobody can answer questions
  mid-task" and runs with `--permission-prompts none`. In ticket mode the system prompt says instead
  that questions go on the ticket via the Blocked loop. Permission prompts stay off.
- **Notifications:** ~~Plane's own notifications are the only push channel in phase 1 (decision
  6).~~ **Changed 2026-10-24 (George):** Plane's web app shows new comments only on reload (step 5b
  findings), so agent-desk now pushes to George's phone through Home Assistant, as a stopgap until
  the phase 2 chat channel replaces it.
  - **When:** a ticket with `ready` reaches **Blocked** (an agent's question, or a Job that ended
    badly) or **In review** (a PR to look at), and on the reopen question for a closed ticket. Not
    when George has commented since, and not for states a restart finds already set.
  - **How:** agent-desk POSTs `{title, message, url}` to a Home Assistant webhook automation
    ("agent-desk — notify George"), which calls `notify.mobile_app_parthenon` with a link to the
    ticket. agent-desk holds only the webhook id, not a Home Assistant token. The repo is public,
    so the id lives in SOPS: `homeassistant-secrets` (mounted as HA's `secrets.yaml`) and
    `notify-webhook-id` in agent-desk's secret.
  - **Staging** posts to staging Home Assistant, which has no phone: there it's a persistent
    notification in HA's UI only.

### Playbooks

Two parts, split by who may change them:

- **The rules** live in the dispatcher's ConfigMap, reviewed in this repo:
  - name;
  - ready rule (`ready` | `auto`);
  - `maxConcurrent` (default 6);
  - allowed repos;
  - deadline;
  - optional model;
  - verification (what "done" means, checked in the critique step).
- **The how-to** is a skill in `gjcourt/agents`, installed only if it's listed in
  `images/bench-cloud/agents-allowlist`. That's the existing pattern: an agents-repo change can't
  widen what runs.

Hooks and MCP config are not synced from `gjcourt/agents` (the allowlist excludes
settings/hooks/MCP on purpose). For ticket Jobs they're baked into the image's managed settings,
also reviewed here.

**The first three playbooks** are all ready-gated (decision 2), each capped at 6.

| Playbook | Trigger | Does | Allowed repos | Verification |
| --- | --- | --- | --- | --- |
| `docs-fix` | George writes a ticket | Docs-only changes: typos, stale commands, broken links, a README section the ticket describes | In-scope repos | The diff touches only docs paths (`*.md`, `docs/`), and the repo's own lint passes |
| `dep-hold` | George writes a ticket, or the renovate fixer opens one (decision 4) | Investigates why a dependency update is held or stuck; opens a fix PR, or writes up the cause | In-scope repos | A PR whose CI passes, or a findings comment that names the blocking change |
| `design` | George writes a ticket | Task analysis and a low-fidelity prototype per `UI.md` (`gjcourt/agents`), before any production UI code | In-scope repos | The diff touches only `*.md` and `docs/` (a prototype is an HTML file under `docs/`), and it follows the `UI.md` checklist |
| `main-ci` | The dispatcher, when a default branch goes red (below) | Investigates a failing default-branch workflow; proposes a fix PR, or writes up why there isn't one | In-scope repos | A PR on which the previously failing workflow passes, citing the failed run and the root cause; or, if the cause isn't in the repo (flaky test, outage, expired secret), a findings comment saying so with evidence |

`design` was added for PLANEMOBILE-2 (2026-10-25) and isn't one of the three; it runs on Opus,
capped at 2.

**`feature`** (George, 2026-10-25). The first playbook that changes code. Repos: `gjcourt/plane-mobile`
and `gjcourt/domestique` (`allowedRepos`; DOMESTIQUE is mapped in the same change). Opus, a 3 h
deadline, capped at 2. What must pass: the repo's own tests and lint, and CI green on the PR before
In review. What it must never touch: secrets and `.env` files (George's choice). Workflows are out
of reach anyway, since the bench-cloud App has no Workflows permission. Deploy config, lockfiles
and tests aren't fenced by rule; review catches them.

**A run that skips its last step.** PLANEMOBILE-1 (2026-10-25, Haiku) opened and linked its PR, then
ended on its critique without moving the ticket. The reaper used to send any finished Job's In
Progress ticket to Blocked. Now a Job that **succeeded** and left a link to a PR **on the project's
mapped repo** sends the ticket to In review, with a comment naming the run record; everything else
still goes to Blocked.

PLANEMOBILE-4 (Opus) did the same the same day, so the reaper is the backstop, not the fix. Ticket
Jobs now carry a Claude Code **Stop hook** (`bench-ticket stop`): the first time the agent tries to
end with the ticket still In Progress, the hook blocks the stop and tells it to set In review (PR
linked) or Blocked (a question, or why it couldn't finish). A second attempt always ends, so the
hook can't hold a run open, and any Plane error lets the run end too. The run prompt also says the
state change is the last step before the summary.

**`main-ci` trigger.**

- The GitHub poll reads the most recent completed workflow runs on each in-scope repo's default
  branch.
- When a run concludes `failure` and the repo has no open `main-ci` ticket, the dispatcher creates
  one in the repo's project. It carries the `playbook:main-ci` label, the run URL, the failing jobs,
  and the head commit.
- **One open ticket per repo.** Further failures add a comment to it rather than a new ticket.
- **If the branch goes green before George starts it**, the dispatcher comments and moves the ticket
  to Cancelled.
- The ticket is created by `agent-desk` and still needs George's `ready`. An agent-created ticket
  can never start itself.

**Renovate fixer → `dep-hold` tickets (decision 4).** The
[renovate fixer plan](2026-09-28-renovate-fixer-bench-cloud.md) has `renovate-automerge` launch fix
Jobs itself and track them in a `renovate-fixer-state` ConfigMap. Under this plan it instead **opens
a `dep-hold` ticket** naming the Renovate PR, the failing check and the classification. The
dispatcher launches the Job, and Plane becomes the record.

- With decision 2, those tickets **wait for George's `ready`**. That replaces the fixer plan's
  decision 2 ("`renovate-automerge` kicks off the task").
- The fixer plan's merge gates for mechanical fixes are not changed here.

**Auto-start later.** The format keeps `auto` so a playbook can be flipped without a redesign. One
possible bar for flipping one is a run of clean results, e.g. five merged PRs with no George fix-ups.
That's an idea for George to revisit, not a decision.

### Plane access inside a Job

`makeplane/plane-mcp-server` in stdio mode, pinned (v0.3.4 was the version tested on staging), with
`PLANE_BASE_URL`, `PLANE_API_KEY` and `PLANE_WORKSPACE_SLUG`.

- Basic tools only: work items, comments, states, labels and links.
- No PQL; CE rejects it, so filter client-side.
- The paid-edition tools are listed in the managed settings' deny list.
- bench-cloud's network policy already allows the api pods in `plane-stage` and `plane-prod` on 8000.

### Guardrails (from the brief, non-negotiable)

- **Agents never merge.** Branch protection is never bypassed; George merges. bench-cloud's
  `bench-merge-audit` already pages on any merge by the App.
- Every agent PR gets a **babysit and a critique pass** before it reaches "In review".
- The homelab repo is on the dispatcher's **deny list** until bench-watch exists.
- The dispatcher's Plane token is scoped to the workspace (and, by membership, to the in-scope
  projects). It holds **no GitHub write scope**; Jobs keep bench-cloud's existing GitHub App.
- **Ticket text is untrusted input** to the agent, the same as any issue body. The prompt fences it.
  The gates above (George-applied `ready` and `over-cap`, George-authored comments) don't trust
  anything an agent can write.

### Network

| From | To | Port |
| --- | --- | --- |
| `plane-{stage,prod}` worker (webhook delivery) and api (save-time check) | `agent-desk[-stage]` | dispatcher port |
| `agent-desk[-stage]` | Plane api in the matching namespace | 8000 |
| `agent-desk[-stage]` | kube-apiserver | 443/6443 |
| `agent-desk[-stage]` | public internet (GitHub), 0.0.0.0/0 minus private ranges, as bench-cloud | 443 |

Plane's own policies get the matching egress (worker) and ingress rules.

### Capacity for 24 pods (measured 2026-10-22)

Task pods request **500m CPU / 1Gi** each, with limits of **2 CPU / 4Gi**. Ephemeral storage is
1Gi requested, 20Gi limit, with `home` 16Gi and `tmp` 4Gi emptyDirs (`scripts/bench-cloud/job.yaml`).
The init container (10m / 16Mi) doesn't add to that. The `bench-cloud` LimitRange defaults are the
same, with a per-container max of 4 CPU / 8Gi.

From `kubectl get nodes` and `kubectl describe nodes` (allocated requests), and `kubectl top nodes`:

| Node | Allocatable CPU / memory | Requested now | Free by requests | In use now (`top`) |
| --- | --- | --- | --- | --- |
| talos-2mz-rfj | 11.95 / 61.5Gi | 4.08 / 11.9Gi | 7.87 / 49.6Gi | 0.95 / 9.6Gi |
| talos-fpd-h0t | 11.95 / 61.5Gi | 4.09 / 11.7Gi | 7.86 / 49.8Gi | 1.54 / 12.8Gi |
| talos-kot-7x7 | 11.95 / 61.6Gi | 7.13 / 14.1Gi | 4.82 / 47.5Gi | 4.72 / 6.4Gi |
| talos-ykb-uir | 11.95 / 61.5Gi | 4.39 / 11.6Gi | 7.56 / 49.9Gi | 1.42 / 11.1Gi |
| **Cluster** | **47.8 / 246Gi** | **19.7 / 49.3Gi** | **28.1 / 196.8Gi** | **8.6 / 39.9Gi** |

- **Requests fit.** 24 pods request 12 CPU and 24Gi, against 28.1 CPU and 196.8Gi free. Each node
  can take its share: 6 pods per node is 3 CPU and 6Gi, under every node's free amount, including
  kot-7x7's 4.82 CPU.
- **Memory limits fit.** 24 × 4Gi = 96Gi worst case, added to 39.9Gi in use today, is about 136Gi of
  246Gi.
- **CPU limits don't, and needn't.** 24 × 2 CPU = 48 CPU, the whole cluster's allocatable. CPU over a
  request is throttled, not evicted. If many agents compile at once they slow each other and contend
  with real workloads, and CPU shares follow requests. Task pods are priority −100, so they schedule
  last and are evicted first under memory pressure. This is a finding, not a blocker: the quota can
  go to 24, and the cost of a busy hour is slower agents.
- **Ephemeral storage fits.** Allocatable is about 212Gi on three nodes and 836Gi on kot-7x7, with
  3–6Gi requested on each. 6 pods × 20Gi = 120Gi worst case per node.
- **Network caveat, same day:** kot-7x7's link shows ~2–5% packet loss and switch receive errors
  that followed the node to a new cable and port (talos-kot-7x7, 2026-10-22). Agents scheduled there
  will see flaky network until that's fixed. It doesn't change the capacity numbers.

## Step 2 findings (staging, 2026-10-22)

Run against staging Plane (CE 1.4.2). `agent-desk` has no staging account, so the workspace Member
`collab-test` stood in for it. The SANDBOX project, the three states and the five labels now exist
on staging, and `collab-test` is a member of SANDBOX and SMOKE only.

**1. A Member's token on a project it isn't a member of: contents refused, but it can see the
project.**

| Call, with the Member's API key, on AUDIO (not a member) | Result |
| --- | --- |
| `GET /projects/` | 200, and **AUDIO is listed**, with every other project |
| `GET /projects/<AUDIO>/` | 200 |
| `GET /projects/<AUDIO>/work-items/` and `/labels/` | 403 |
| `POST /projects/<AUDIO>/work-items/` | 403 |
| `GET /projects/<SANDBOX>/work-items/` (a member) | 200 |

- The listing is visible because every project here is **public** (`network: 2`). Project names
  and metadata leak to every workspace Member; the work items don't.
- **A Member can join any public project by itself, through a browser session.** The source says
  so: `UserProjectInvitationsViewset.create` (`app/views/project/invite.py`) refuses only
  `network == SECRET` for non-admins. That endpoint takes session auth only; the v1 API's member
  POST needs project admin (`api/views/member.py`). So the API key the dispatcher holds can't
  join, but anyone who logs in as `agent-desk` with its password can. Not tested live: the
  self-join call was blocked by the session's safety check, so this is from the source.
- **What follows:** the dispatcher's project allowlist stays the real gate, with membership as the
  second layer. To make membership a boundary as well, set HOMELAB (and any project agents must
  never see) to **private** (`network: 0`). George's call; it changes nothing for George, who
  is a workspace admin.

**2. The activity API shows who added a label. The `ready` gate can be built as designed.**

`GET /projects/<id>/work-items/<id>/activities/` returns one row per label change. Sequence on
SANDBOX-1, read back with the Member's token:

| Who | Change | `field` | `old_value` → `new_value` | `old_identifier` / `new_identifier` | `actor` |
| --- | --- | --- | --- | --- | --- |
| Member | adds `playbook:docs-fix` | `labels` | `""` → `playbook:docs-fix` | – / label id | Member's id |
| George | adds `ready` | `labels` | `""` → `ready` | – / `ready`'s id | George's id |
| Member | removes `ready` | `labels` | `ready` → `""` | `ready`'s id / – | Member's id |
| Member | re-adds `ready` | `labels` | `""` → `ready` | – / `ready`'s id | Member's id |

- **The rule:** take the latest activity whose `new_identifier` or `old_identifier` is `ready`'s
  label id. It counts only if it's an add (`new_identifier` set) by George's user id. In the run
  above, the last `ready` was added by the Member, so the ticket isn't startable. Match on the
  label **id**, not the name, so renaming a label can't spoof or break the gate.
- Activity rows appeared within about a second of the PATCH.

**3. Webhook payloads carry the actor and the label diff.**

A throwaway receiver on the allowlisted name (`webhook-receiver.plane-stage`, deleted after) caught
all four events. Each had the headers `X-Plane-Delivery`, `X-Plane-Event` and `X-Plane-Signature`.

| Event | `event`, `action` | What's in it |
| --- | --- | --- |
| Label change | `issue`, `updated` | `activity.field = "labels"`; `activity.old_value` and `new_value` are the **full lists of label ids** before and after; `activity.actor` is the user (id, email, display name); `data` is the whole work item, with `data.labels` as objects with names, and `data.project` |
| Comment | `issue_comment`, `created` | `activity.actor`; `data.comment_html`, `data.issue`, `data.project`, `data.created_by`, `data.actor` |

- **Trap:** on `issue` events, `data.created_by` is the item's **creator**, not who made the change.
  The actor is `activity.actor`. (On comments the two agree.)
- The webhook's actor is enough to act fast, but the dispatcher still re-reads the activity API
  before starting a Job: a webhook is a hint, the activity record is the authority. The poll path
  uses only the activity API anyway.

**Exit met:** all three answered. One follow-up for George, from finding 1: whether to make HOMELAB
private.

## Step 3a result (2026-10-23)

#1651 raised the quota to 24. A throwaway Job with `parallelism: 24` (pause containers, the task
PriorityClass) ran all 24 pods at once across three nodes; a 25th was refused with `exceeded quota:
bench-cloud-tasks`. **Exit met.** `API_KEY_RATE_LIMIT` waits for step 5's measured rate, as planned.

## Step 4 findings (staging, 2026-10-23 to 2026-10-24)

The dispatcher (#1652, image `ghcr.io/gjcourt/agent-desk`) runs in `agent-desk-stage` in dry-run
(#1653), with `collab-test` standing in for `agent-desk` and `docs-fix` capped at 1. Runbook:
[`agent-desk.md`](../operations/apps/agent-desk.md).

**Exit tests**

| Test | Result |
| --- | --- |
| Signed delivery accepted | ✅ logged `delivery <id>: issue → queued item …` |
| Bad or missing signature rejected | ✅ 401, `rejected delivery …: bad signature` |
| George's `ready` → dry-run Job admitted | ✅ SANDBOX-1 and SANDBOX-2, with the "Started… Dry run" comment |
| `ready` from the bot → nothing | ✅ `SANDBOX-4: skip — ready not applied by George` |
| HOMELAB refused | ✅ by membership: the bot isn't a HOMELAB member, so the dispatcher never sees its tickets (finding 1). The deny list itself is covered by `test_homelab_is_refused_with_one_comment` |
| Second `ready` on a running ticket → no second Job | ✅ `skip — already In Progress` |
| Cap 1 → Awaiting approval, one comment; George's `over-cap` starts it, the bot's doesn't | ✅ after the fix in finding 3 |
| Webhooks off → the poll finds the ticket within 10 minutes | ✅ SANDBOX-5, `wait — docs-fix is at 2/1 running`, no delivery before it |

**Exit met.** Testing turned up four problems, all fixed:

1. **A mapped project the bot can't read stopped every poll** (#1654). HOMELAB is public, so it's
   listed to the bot (step 2, finding 1), but its states 403. The dispatcher now skips a project
   whose `is_member` is false, logging it once. Production would have hit this too.
2. **Skips and deliveries weren't logged** (#1661), so "the gate held" and "the webhook never
   arrived" looked the same. Accepted deliveries are logged, and so is the decision for a ticket a
   delivery names, skips included. The 10-minute poll still logs only actions.
3. **Plane sends the webhook before it records who changed a label** (#1663). Measured on
   SANDBOX-3: the `over-cap` delivery at 03:21:19.495, the dispatcher's check at 03:21:20.559, the
   activity row at 03:21:21.947. The gate failed closed and George's label waited 8½ minutes for the
   poll. When a delivered ticket carries `ready` or `over-cap` that the activity log doesn't yet
   credit to George, the dispatcher now rechecks after 3, 10 and 30 s. Retested on SANDBOX-5: decided
   11 s and started 15 s after the delivery.
4. **Secret files unreadable** (#1653). The secret volume is 0440 and the pod runs as 65534 with no
   `fsGroup`; added `fsGroup: 65534`.

**Decision (George, 2026-10-24): `over-cap` approves one start.** The dispatcher removes it as the
ticket starts (#1664). Left on, a ticket that later resumed from Blocked would go over the cap again
without asking.

**Noted, not fixed:** an event check takes ~10–15 s, because it re-reads every item in every mapped
project plus the Jobs list to count what's running. Fine at this size; worth narrowing before many
projects are mapped.

**State left on staging:** the dispatcher keeps running in dry-run. SANDBOX-1 to -5 are Cancelled,
since a dry-run start has no Job to finish them.

## Step 5 findings (staging, 2026-10-24)

#1668 added ticket mode: `TASK_TICKET` in `bench-task`, the hash-pinned `plane-mcp-server` in
`/opt/plane-mcp`, the `bench-ticket` comment hook, and managed settings that deny every Plane MCP
tool but `workitem`, `workitem_comment`, `workitem_link`, `state` and `workitem_activity`. Both runs
were hand-launched with `jobs.build_job` and agent-desk's own prompt, against the collab-test key in
a temporary `bench-cloud-plane-jobs-token` Secret, deleted afterwards.

| Run | Ticket | Result |
| --- | --- | --- |
| `ad-sandbox-6-20261024-91b3e4`, image `2026-10-24-4a00349` | SANDBOX-6, "Add a Contributing section to the README" | ✅ PR [agent-sandbox#2](https://github.com/gjcourt/agent-sandbox/pull/2) (README.md only, lint and tests green), linked on the ticket, a summary comment, then In review. 95 s, 24 turns, $0.39 |
| `ad-sandbox-7-20261024-ce5aec`, image `2026-10-24-d1f043b` | SANDBOX-7, an unclear "CI lint standards across the repos" | ✅ asked its question on the ticket and set Blocked, no PR. George's comment at 21:54:09 reached the agent through the hook at 21:54:42 (`hook_additional_context` on `PostToolUse:mcp__plane__workitem_comment` in the transcript), and it answered on the ticket. 75 s |

**Exit met.** Three things turned up:

1. **The agent HTML-escaped its own comment** on SANDBOX-6 (`&lt;p&gt;…`), so Plane showed the
   tags. The MCP server passes `comment_html` through unchanged; the escaping was the model's. A
   prompt line fixed it (`d1f043b`); SANDBOX-7's comments rendered.
2. **A comment can fall between the prompt and the hook.** `bench-ticket init` marks every comment
   already on the ticket as seen, but the prompt was built earlier, by the dispatcher. A comment
   George writes in that gap reaches neither. SANDBOX-7's comment landed after `init`, so it got
   through. Fixed in step 5b.
3. **Runs are short and end the conversation.** Both finished in under two minutes, so "comment
   while it runs" is a narrow window, and after the run nothing listens except the Blocked loop.
   George's expectation (2026-10-24) is Devin's: a comment wakes the agent whenever, and it carries
   on with what it knew. That's step 5b.

**Measured Plane rate.** The hook calls Plane at most once per 30 s per Job (2/min), and a run's own
MCP calls were 4–5 in total. At 24 Jobs that is about 50/min on the Jobs' key, before 5b's waiting
Jobs (below), which poll at 4/min each. **Production `API_KEY_RATE_LIMIT`: 180/min**, enough for 24
Jobs all waiting at once, with room.

## Waking the agent (step 5b)

Decisions (George, 2026-10-24): any comment from George wakes the agent, including on a closed
ticket, after confirming; the agent carries on in the same session, with the saved transcripts as
the fallback; it asks when unsure and can wait for the answer; `ready` stays the gate.

- **What wakes it.** George's newest comment is newer than `agent-desk`'s newest comment, on a
  ticket that still has `ready`. The check is on the comments, not on events, so a missed webhook,
  a comment in finding 2's gap, or a comment while the dispatcher was down all still wake it at the
  next poll. Comments by `agent-desk` never wake anything, so the agent can't loop on itself.

  | Ticket state | On a new comment from George |
  | --- | --- |
  | In Progress (a Job is running) | Nothing from the dispatcher; the hook relays it mid-run |
  | Blocked, In review | A new Job, resuming the last session |
  | Done, Cancelled | The dispatcher asks first (below); no Job and no tokens until George confirms |
  | Todo, Awaiting approval | Unchanged: the ordinary start and cap rules |

- **A "request changes" review wakes it too** (George, 2026-10-25). On the 5-minute GitHub check,
  an In review ticket whose linked PR is open, and whose latest deciding review by `githubOwner`
  (approve, request changes, dismiss; plain comments don't count) requests changes, gets a resumed
  Job. The review's summary and inline comments go to the Job directly as its prompt, not copied
  onto the ticket: a copy would be the bot's comment, which never wakes anything. The `Started`
  comment names the review id, so one review wakes one run; a later review wakes another. The same
  gates apply: `ready` by George, one Job per ticket, the playbook cap. At the cap the ticket stays
  In review with one comment saying so, and starts when a slot frees. An approval merges instead
  (plane-mobile's `approve-to-merge`). One ticket's or repo's error is logged and the check goes on
  to the rest.
- **Closed tickets ask first.** The dispatcher, which runs no model, comments: "SANDBOX-6 is Done.
  Reply `reopen` to reopen it and wake the agent on your comment." Only a later comment from George
  that is exactly `reopen` moves the ticket to In Progress and starts a resumed Job, with his
  earlier comment as the request. Anything else leaves it closed, and the dispatcher doesn't ask
  twice for the same comment.
- **Same PR, not a new one.** A woken Job checks out the ticket's PR branch (from its link) and
  pushes there. It sets In review again when done. If the PR was merged in the meantime, it
  branches fresh and opens a follow-up PR.
- **Same session.** Every run already copies `~/.claude/projects` to
  `hestia:agent-inbox/runs/<task-id>/transcripts/`. A woken Job finds the ticket's newest run by
  `meta.json`'s `ticket` key, copies its session file into its own `~/.claude/projects/`, and runs
  `claude -p --resume <session-id> --fork-session` with the new comments as the prompt. The cwd is
  the same in every pod (`/home/node/work/repo`), so the session's paths still hold. The old
  transcript is never modified.
  - **Fallback, if `--resume` refuses the file:** `bench-task` builds a condensed record from the
    old transcript (the agent's own messages, its tool calls and their results trimmed, the PR
    branch) and puts it in the prompt. Less detail, no dependence on Claude Code's session format.
  - **Saved as it goes.** The hook also copies the session file to the run's record after each tool
    call, so a Job killed at its deadline or by a crash still leaves a session to resume.
- **Asking, and waiting for the answer.**
  - The ticket prompt gains: "If the ticket can be read more than one way, ask before you start,
    not after a PR."
  - New `bench-ticket wait`, allowed as a Bash command: it posts nothing itself, and blocks until
    George comments or 15 minutes pass, polling every 15 s. The agent posts its question, sets
    Blocked, then waits; George's answer comes back as the command's output and the agent sets In
    Progress and carries on. On timeout it ends the run, and George's later answer wakes it as
    above.
  - A waiting Job uses no tokens, but it holds a pod and a slot in its playbook's cap. The 15-minute
    limit bounds that.
- **The finding 2 fix.** agent-desk puts the ids of the comments in the prompt into `TASK_TICKET`
  (`seen_comments`), and `bench-ticket init` marks only those as seen. A woken Job's ids are the
  ones up to the comment that woke it.

## Step 5b findings (staging, 2026-10-24)

Code: `feat/agent-desk-wake`. The dispatcher half ran as agent-desk's real `Dispatcher` against
staging Plane (as collab-test) with Kubernetes stubbed, so every decision, Plane write and Job spec
was the real one; the Jobs it produced were then created by hand. The staging deployment was
scaled to 0 (and `apps-staging` suspended) during the tests, so its older dry-run code couldn't
race the harness; both were restored afterwards. The temporary Jobs key Secret was deleted.

| Test | Run | Result |
| --- | --- | --- |
| Comment on an In review ticket wakes the agent on the same PR | SANDBOX-6, `ad-sandbox-6-20261024-6bfb5e` | ✅ resumed the earlier session on its model (Sonnet), retitled PR #2 per the repo's rules, replied, In review. Checkpoints were on hestia mid-run. $0.28 net |
| A question answered within 15 minutes continues in the same Job | SANDBOX-8, `ad-sandbox-8-20261024-0d545a` | ✅ asked, set Blocked, ran `bench-ticket wait`; George's answer came back as its output; opened PR #3 and set In review. 3 min, $0.69 (Haiku) |
| A comment written just before the Job starts reaches it | SANDBOX-8, same run | ✅ "fizz buzz" was written after the prompt was built and before the Job existed; the hook relayed it on the first tool call |
| A comment on a Done ticket asks first; `reopen` starts a Job | SANDBOX-6, `ad-sandbox-6-20261024-33e24a` | ✅ one confirmation comment and no Job; a second pass asked nothing; `reopen` started a resumed Job with the comment that drew the question |
| A Blocked ticket's answer resumes the session | SANDBOX-6, `ad-sandbox-6-20261024-80e1fb` | ✅ George's license answer woke it; MIT `LICENSE.md` and a README line pushed to PR #2; In review |

**Exit met.** Found and fixed on the way:

1. **The model swap crashed a resumed Job** under `set -u` (unsetting array entries mid-loop).
2. **A Job that dies before Claude runs became the ticket's "last run"**, with no session. The
   lookup now follows `resumed_from` back a few hops, and records it as soon as the session is
   found.
3. **Labels set in Plane's create dialog have no activity row** (SANDBOX-8): only the "created"
   row. `ready` on a ticket George created with it looked unattributed, and the staging dispatcher
   refused it too. A label with no rows now counts for the ticket's creator; any later row still
   decides, and a bot-created ticket still fails closed.
4. **A resumed session asked its question and ended the run** instead of waiting: its prompt said
   only "or Blocked if you need him". The hook now tells any agent that sets Blocked to run
   `bench-ticket wait`, and the resume prompt says so.
5. **The agent escaped its comment HTML again** (resumed Sonnet session). The pre-hook repaired it
   before posting, so the ticket was clean: the lint doing its job.
6. **`uv` wasn't in the image**, so the agent ran the repo's lint in a pip venv on another Python.
   Added, pinned by checksum.

Also measured:

- **Cost of a wake-up.** A resumed run on the session's model cost $0.28; the earlier cross-model
  resume cost $0.36 for less work. Staying on the session's model (George, 2026-10-24) is right.
- **The PR link title** is enforced: `PR #3 · docs: SANDBOX-8 improve README and about page`.
- **Plane doesn't show new comments without a refresh.** Source of v1.4.2: comments load with the
  work item; the full-page view re-fetches only on window focus, the peek view never; `plane-live`
  is collaborative editing only; nothing in CE enables polling. George watched SANDBOX-8 and saw
  nothing until he reloaded. Proposed: a push through the Home Assistant app when an agent asks a
  question, reaches In review, or a Job fails (reverses decision 6; George's call, not built).

## Step 6 findings (production, 2026-10-25)

**Exit met.** SANDBOX-1 ("add a one-line description to the README"), labelled
`playbook:docs-fix` and `ready` by George:

- the webhook delivered at once, and agent-desk moved the ticket to In Progress and started
  `ad-sandbox-1-20261025-46743f`; later deliveries for it were skipped as already In Progress;
- the Job, on Haiku, opened agent-sandbox#4 in 70 s (18 turns), changing only `README.md`, linked
  it as `PR #4 · docs: SANDBOX-1 add one-line repo summary to README` and set In review. CI passed;
- George's phone got "SANDBOX-1 is ready for review";
- George merged, and the next GitHub poll moved the ticket to Done.

**What setup found:**

1. **Plane resolves a webhook's host when it's saved.** So the webhook can't exist before
   agent-desk's Service. The order for a new environment: the Plane change, agent-desk with a
   placeholder `webhook-secret`, the webhook, then the real secret and a restart (runbook).
2. **Plane switches a webhook off silently.** Staging's was off for a day after the 5b tests
   scaled the dispatcher to 0. agent-desk now warns when the poll finds a change no delivery
   brought (#1680).
3. **The bot can list every public project** (Plane's `network: 2`), though it's a member of
   SANDBOX only. The dispatcher acts only on its allowlist; HOMELAB is private and invisible to it.



George, 2026-10-24: simple tasks run on Haiku by default; measure how well each model handles each
kind of task, and route to the cheaper model where it does as well.

- **Now (step 5b).** A playbook's `model` sets `TASK_MODEL` for its Jobs. `docs-fix` runs on
  `claude-haiku-5-5`; `main-ci` and `dep-hold` follow the tiers below. Comment
  repair in `bench-ticket lint` is Haiku too: plain fixes in code first, and Haiku only for what's
  left, its rewrite used only if the words match the original.
- **Tiers (George, 2026-10-25).** Until the scorecard says otherwise, the model follows the kind of
  work: docs on **Haiku** (`docs-fix`); unit tests and YAML on **Sonnet** (`main-ci`, `dep-hold`,
  set when each playbook ships); feature, dev and design work on **Opus** (`design`, `feature`).
  Opus playbooks get a low `maxConcurrent` (2), since Jobs share George's subscription limits.
- **Measure.** Every run already records cost, turns, duration and per-model usage in `result.json`.
  agent-desk adds the outcome, which it alone sees: PR merged, closed, or still open; how many times
  George woke the agent before the merge (a request for changes); CI green on the first push; ended
  Blocked by a crash. It writes one row per run to `agent-inbox/scores.csv` (task id, playbook,
  model, difficulty, cost, outcome). The scorecard is success rate and cost per success, per
  playbook × model × difficulty.
- **A bench, on SANDBOX only.** A set of sandbox tickets with known good outcomes, each run once per
  model (Haiku, Sonnet, Opus) and scored the same way, plus an Opus grader comparing each diff with
  the reference. This fills the scorecard faster than live traffic, at a cost bounded by tickets ×
  3. Never on real repos.
- **Route by difficulty.** At start, agent-desk asks Haiku (no tools, ticket text only, fenced like
  the prompt) to rate the ticket `simple`, `standard` or `hard`, with one line of reason, recorded
  on the Job as a label. The model comes from a per-playbook table, difficulty → model, set from the
  scorecard and reviewed in this repo like any rule.
  - **Escalation.** A woken retry after a crash, or after CI fails twice, steps up one model.
  - **Override.** George's `model:<name>` label on the ticket wins.
  - **Thin data keeps the stronger model.** A cheaper model replaces a stronger one for a cell only
    once it has at least 10 scored runs there and matches its success rate.

## Steps

Staging first. There is one bench-cloud, and it's production, so staging never launches a real Job:
the staging dispatcher runs in **dry-run**, creating Jobs with `dryRun=All`. That still exercises
RBAC and the admission policy.

1. **This plan PR.** Review.
2. **Plane prep and checks on staging.**
   - Create the In review, Blocked and Awaiting approval states; the `ready`, `over-cap` and
     `playbook:*` labels; and a SANDBOX project. Add `agent-desk` to that project only.
   - Verify, recording what's found here:
     - a Member's token is refused on a project it isn't a member of;
     - the activity API shows who added a label;
     - what the webhook payload carries for label and comment events.
   - **Exit:** all three answered, with evidence.
3. **The sandbox (decision 1). George creates it, or OKs its creation.**
   - Repo `gjcourt/agent-sandbox` (private; created 2026-10-22), with a small CI workflow so `main-ci` and the
     PR checks have something to run.
   - The `default-branch-guard` ruleset, applied by the daily `github-rulesets` CronJob (check it
     covers the new repo). The bench-cloud GitHub App is installed on all repositories, so it's
     covered.
   - A SANDBOX project in production Plane, with `agent-desk` as its only bot member.
   - **Exit:** the repo exists with the ruleset, and the bot sees SANDBOX and nothing else agents
     shouldn't touch.

   **3a. Concurrency (decision 5).**
   - Raise `apps/base/bench-cloud/resourcequota.yaml` from 8 to **24** pods. This supersedes the
     bench-cloud plan's "ceiling of 10" (its decision 4), by George's 2026-10-22 decision.
   - Raise production Plane's `API_KEY_RATE_LIMIT`; the value is set from step 5's measured hook
     rate.
   - Capacity is above.
   - **Exit:** the quota reads 24, and 24 trivial task pods schedule at once.
4. **Dispatcher on staging, dry-run.** Image, `agent-desk-stage` namespace, RBAC, network policies,
   webhook secret, the staging `WEBHOOK_ALLOWED_HOSTS` switched from the smoke-test receiver to the
   dispatcher. **Exit:**
   - a signed delivery is accepted and a bad signature rejected;
   - `ready` added by George produces a dry-run Job that passes admission;
   - `ready` added by `agent-desk` does not;
   - a ticket in HOMELAB (or a project mapped to `gjcourt/homelab`) is refused;
   - a second `ready` on a running ticket doesn't create a second Job;
   - with the playbook cap set to 1 for the test, a second ticket goes to Awaiting approval; George's
     `over-cap` starts it, and `agent-desk` adding `over-cap` does not;
   - with webhooks disabled, the poll still finds the ticket within 10 minutes.
5. **Ticket mode in bench-cloud.**
   - `bench-task` learns `TASK_TICKET`; the image gets the pinned Plane MCP server, the comment hook
     and the managed settings.
   - The Jobs' Plane token goes into a SOPS secret (operator).
   - **Exit:** a hand-launched ticket Job against a staging ticket reads it, comments, attaches a
     link and sets In review, and a comment George writes mid-run reaches the agent. **Met
     2026-10-24** (findings above).

   **5b. Waking the agent** (design above).
   - First, alone: does `--resume` work in a new pod from a copied session file? Tested by hand on
     staging with SANDBOX-6 ("rename the heading" on agent-sandbox#2). The answer decides whether
     the resume or the fallback is built first.
   - Then: the wake rule and the closed-ticket confirmation in agent-desk; finding and restoring the
     session, the per-tool-call save and `bench-ticket wait` in bench-cloud; `seen_comments`.
   - **Exit (staging, hand-launched Jobs for the bench-cloud half, dry-run for the dispatcher):**
     - a comment on an In review ticket wakes a Job that pushes to the same PR and remembers the
       earlier run;
     - an agent question answered within 15 minutes continues in the same Job;
     - a comment on a Done ticket draws one confirmation comment and no Job; `reopen` starts one;
     - a comment written just before the Job starts reaches it.
   - **Met 2026-10-24** (findings above).
6. **Production dispatcher with `docs-fix`, on SANDBOX: first end-to-end milestone.** **Exit:**
   George labels one SANDBOX ticket `ready` → a bench-cloud Job opens a PR in
   `gjcourt/agent-sandbox` → the PR is linked on the ticket → the ticket reaches **In review**.
   George merges → the ticket reaches **Done**. **Met 2026-10-25** (findings below).
7. **Question loop live (SANDBOX).** **Exit:**
   - an agent's question parks the ticket in Blocked;
   - George's reply resumes it in a new Job that has the answer and the earlier session;
   - a comment on an In review ticket wakes the agent on the same PR;
   - a crashed Job's ticket lands in Blocked with the record path.
8. **`main-ci` playbook (SANDBOX).** **Exit:**
   - breaking the sandbox's default branch opens exactly one ticket, and a second failure comments
     on it;
   - after George's `ready`, the agent's PR turns the workflow green;
   - a red-then-green branch with no `ready` ends as Cancelled.
   **8b. Model routing** (design above). **Exit:** `scores.csv` has a row per run with its outcome;
   the SANDBOX bench has scored each playbook on Haiku, Sonnet and Opus; the difficulty rating is on
   every Job; and the routing table, set from those scores, is in the config.
9. **`dep-hold` playbook, and the renovate fixer migration (decision 4).**
   - `renovate-automerge` opens `dep-hold` tickets instead of Jobs.
   - The fixer plan is updated to match: its decision 2 is superseded and `renovate-fixer-state`
     goes away, since Plane plus Job labels are the record.
   - **Exit:** one held Renovate update becomes a ticket, and after George's `ready` the agent opens
     a fix PR or writes up the cause.
10. **Real repos (decision 1).** Add the first real repo project to the dispatcher's allowlist and
    `agent-desk`'s project memberships. **Exit:** one `docs-fix` ticket on it goes ready → PR → Done.
    **First repo: `gjcourt/plane-mobile`** (Plane project PLANEMOBILE, George, 2026-10-25), a new
    phone-first Plane client. Its first two tickets are docs-only, so `docs-fix` covers them; the
    `feature` playbook comes with Domestique (DOMESTIQUE, 56 tickets drafted in Backlog). Before a
    repo is mapped: the bot is a Member of its project, and `github-rulesets` has applied the
    default-branch guard (run the CronJob by hand for a repo created that day).
11. **Planner, later.** A merged plan PR → draft tickets in Triage, written as a bench-cloud skill
    (per the brief).
12. **Pilot:** one real plan → tickets → merged PRs through the loop.

## Exit criteria (the brief's phase 1, restated for this plan)

- [ ] A startable ticket launches a bench-cloud Job, and the Job's PR is linked back to the ticket.
- [ ] An agent question on a ticket, answered by George, resumes the same work.
- [ ] A comment George writes on a ticket reaches its running Job mid-task, and the reply appears on
      the ticket.
- [ ] At least three playbooks exist, each declaring its own ready rule. *(The brief adds "at least
      one runs on its own". George chose ready-gating for all three on 2026-10-22, so this one waits
      for a later decision to flip a playbook to `auto`.)*
- [ ] The dispatcher provably refuses homelab-repo tickets and duplicate pickups, and holds a
      playbook at 6 without George's `over-cap`.
- [ ] A merged plan PR produces draft tickets in Plane without manual copying.
- [ ] One full project goes plan → tickets → merged PRs through the loop, with George merging every
      PR.

## Risks

- **Prompt injection through tickets.** Ticket text reaches an agent with push rights. Mitigated by
  fencing and by gates an agent can't satisfy itself. Not eliminated: an agent can still be misled
  within its own task, which is why babysit, critique and George's merge stay in the loop.
- **Subscription burn.** Every dispatched ticket is a Job on George's Claude subscription, and 24 at
  once uses the plan's limits roughly 24 times as fast as one (the bench-cloud plan's risk, scaled
  up). The per-playbook cap of 6 and George's `ready` on every ticket bound it. Per-task usage is
  already recorded in each run's `result.json`.
- **CPU contention at full concurrency** (see Capacity): slower agents and some pressure on real
  workloads when many compile at once.
- **Missed events.** Webhooks retry 5 times and stop. The 10-minute poll is the backstop; step 4
  tested it with webhooks off.
- **Webhooks arrive before Plane's activity record** (step 4, finding 3). The dispatcher rechecks
  for 45 s, then the poll catches it. If Plane's delay ever exceeds that, George's label still acts,
  just up to 10 minutes late; it never acts on the wrong actor.
- **Waiting and resumed Jobs cost more.** A Job in `bench-ticket wait` holds a pod and a cap slot
  for up to 15 minutes, and a resumed session reloads its earlier context, so each wake-up costs
  more tokens than a fresh run. Both are bounded: the 15-minute limit, and `ready` on every ticket.
- **Plane rate limit.** At 24 Jobs the comment hook alone approaches the default 60/min on the Jobs'
  key. Raise `API_KEY_RATE_LIMIT` (step 3a) rather than adding keys.
- **The actor check depends on Plane exposing it.** Step 2 found that it does (findings, item 2).
  The gate keys on the label id and George's user id, so an upgrade that changes the activity
  format would close the gate rather than open it: no matching row, no start.
- **Self-join on public projects** (findings, item 1). A browser session as `agent-desk` can join
  any public project. The allowlist still stops the dispatcher acting there; making HOMELAB
  private closes it in Plane too.

## Open questions

- ~~Sandbox repo name and visibility.~~ **`gjcourt/agent-sandbox`, private** (George, 2026-10-22).
  Created the same day, with a `lint-and-test` CI workflow.
- ~~Make HOMELAB private in Plane?~~ **Private for now**, to be made public again later (George,
  2026-10-25). Set in production Plane the same day. The bot is a **Member** of SANDBOX, not an
  Admin: the states and labels already exist, and Admin would only add the right to change them
  and to invite members.
- ~~Production `API_KEY_RATE_LIMIT` value.~~ **180/min**, from step 5's measured rate.

## Phase 2 pointer

Out of scope here, per the brief: Matrix sessions. One item carries over from this plan. **Alternate
notification mechanisms** (anything beyond Plane's own) are revisited once the phase-2 chat server
is running (decision 6).
