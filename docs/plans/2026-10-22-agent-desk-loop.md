---
status: planned
last_modified: 2026-10-22
lab: 03-042
summary: "The agent desk loop (lab 03-042 phase 1): a stateless in-cluster dispatcher turns startable Plane tickets into bench-cloud Jobs, links each PR back to its ticket, and carries questions and mid-run comments through the ticket; docs-fix and dependency-hold playbooks first"
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
scope.

```text
George labels a ticket "ready"  (or its playbook is auto-start)
        │  Plane webhook (signed)  +  slow API poll as backstop
        ▼
dispatcher ── checks: playbook, ready rule, repo not denied, no live Job, quota
        │  creates Job in bench-cloud (ticket as the brief)
        ▼
bench-cloud Job ── Plane MCP: reads ticket, comments, attaches PR link
        │  babysit + critique, then ticket → "In review"
        ▼
George merges ── dispatcher sees the merge on GitHub ── ticket → "Done"
```

## Decisions already made (George, 2026-10-18, from the brief)

- **Tracker:** self-hosted Plane.
- **Ready gate:** per playbook. Each playbook declares whether its tickets start on their own or
  wait for George's `ready` label.
- **Homelab repo out of scope for agent work** until bench-watch exists (the deferred merge-audit
  fix in the [bench-cloud plan](2026-09-25-bench-cloud-agent.md#risks)).
- **The ticket is the chat** in phase 1: questions and mid-run messages go through ticket comments.

## Design

### Where agents work

| Plane project | Agent tickets? | Why |
| --- | --- | --- |
| Repo projects other than HOMELAB (DOMESTIQUE today; more as repos are added) | **Yes** | One project per repo, so the project decides the repo |
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

### Dispatcher

A small service, `agent-desk`, in its own namespace.

| Choice | Decision | Why |
| --- | --- | --- |
| Language | **Python 3.12**, stdlib + PyYAML + the official `kubernetes` client | Reuses plane-sync's Plane client: 1 req/s pacing, `Retry-After`, 409 handling, description normalisation, all already tested against Plane 1.4.2 |
| Source | **`images/agent-desk/`** in this repo, built by `build-agent-desk.yml` | The house pattern (plane-sync, bench-cloud). This reverses the staging plan's "outside this repo": while homelab is on the deny list, no agent can edit the dispatcher that gates agents |
| Namespace | **`agent-desk`** (prod), **`agent-desk-stage`** (staging) | bench-cloud has no ingress at all; webhooks need some. A separate namespace keeps that true |
| State | **None of its own.** Plane (state, labels, links, comments) and Kubernetes (Jobs labelled with the ticket id) are the record | No PVC means no exposure to the iSCSI read-only failure mode ([AGENTS.md](../../AGENTS.md#recovering-read-only-iscsi-volumes-recurring)). A restart re-derives everything with one full poll |
| Creating Jobs | A ServiceAccount `agent-desk` bound by a **RoleBinding in `bench-cloud`** to the existing `bench-task-launcher` Role (Jobs: create, delete, get, list, watch; that namespace only) | The existing admission policy already pins every Job there to `serviceAccountName: bench-agent` and `priorityClassName: bench-cloud-task`, so the dispatcher can't start a privileged task or one the quota doesn't count |
| Plane identity | The **`agent-desk`** bot, with **two API tokens**: one for the dispatcher, one for Jobs | Bot actions are visibly not George's; each token is revocable on its own. Plane's limit is per key (`API_KEY_RATE_LIMIT`, default 60/min), so two keys also halve contention |
| GitHub identity | A **read-only** fine-grained PAT: Pull requests read, on the in-scope repos | It only needs to see whether a linked PR merged or closed. Plane's GitHub integration isn't in CE |

**Inputs.**

- **Webhook** at `http://agent-desk.agent-desk.svc.cluster.local:<port>/plane`, registered as a Plane
  workspace webhook for work items and comments.
  - Verify `X-Plane-Signature` (HMAC) against a SOPS secret; reject anything unsigned or wrong.
  - Production's `WEBHOOK_ALLOWED_HOSTS` gets exactly that one name, in `apps/production/plane/release-patch.yaml`,
    which already reserves the spot. Never a CIDR.
- **Backstop poll** every 10 minutes: list items in the allowlisted projects and re-evaluate.
  Plane retries a delivery up to 5 times and then drops it, so this is what catches an outage.
- **GitHub poll** every 5 minutes for tickets in "In review": the PR's state.

**The startable rule.** A ticket starts only if **all** hold:

1. It's in an allowlisted project, and has no `external_source`.
2. It carries exactly one `playbook:<name>` label naming a playbook the dispatcher knows.
3. Ready rule:
   - `ready` playbooks: a `ready` label **applied by George's Plane account**. The dispatcher checks
     the actor in the item's activity. An agent can add labels through MCP, and the gate must not
     be one it can open itself.
   - `auto` playbooks: the ticket was **created** by George or by an allowlisted automation
     identity. Never by `agent-desk`, so an agent can't create work that starts itself.
4. The project's repo is not on the deny list (`gjcourt/homelab`).
5. No Job labelled with this ticket id is active, and the ticket isn't already In Progress, In review
   or Blocked.
6. The dispatcher's own concurrency cap isn't reached (proposed **3**, below the `bench-cloud-tasks`
   quota of 8 pods in `apps/base/bench-cloud/resourcequota.yaml`). That leaves room for George's own
   `bench-cloud run` tasks and the renovate fixer's up-to-2 per run.

If a rule fails, the dispatcher leaves at most one comment saying which one, and only for rules 2
and 4: a missing playbook or a denied repo is something George would want to know.

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
| Blocked, with a question comment | Nothing; waits for George |
| Anything else (crash, deadline, no PR) | Moves it to **Blocked** with a comment: exit code, and where the record is (`hestia:agent-inbox/runs/<task-id>/`) |

### Ticket ⇄ PR

- **The agent** opens the PR from a new branch, names the ticket in the PR body, attaches the PR
  URL as a work-item link (MCP link tool), runs babysit and critique, and only then sets the ticket
  to **In review**.
- **The dispatcher**, polling GitHub:
  - merged → **Done**;
  - closed without merging → back to **Todo**, with a comment.
- New states are created per in-scope project: **In review** (group `started`) and **Blocked**
  (group `started`).

### Questions and the ticket as chat

- **Question loop.** An agent that needs George comments its question and sets the ticket to
  **Blocked**, then exits. A new comment **by George** on a Blocked ticket re-dispatches it. The new
  Job gets the whole comment thread, so it resumes with the answer. The dispatcher ignores comments
  by `agent-desk`.
- **Mid-run comments.** A Claude Code `PostToolUse` hook in ticket Jobs checks the ticket for new
  comments by George and passes them to the agent as added context. That puts a comment in front of
  the agent mid-task, not only at the next dispatch.
  - It reads through a small **conversation-channel interface** (`poll() -> new messages`,
    `post(text)`), with Plane comments as its only implementation, so phase 2 can add Matrix
    without rewriting it.
  - Rate: at most one Plane call per 30 seconds per Job, whatever the tool-call rate. Eight Jobs
    stay well under 60/min.
  - Step 4 confirms against the Claude Code version pinned in the image that a hook can inject
    context this way.
- **What the Job must change.** Today `bench-task` tells Claude "Nobody can answer questions
  mid-task" and runs with `--permission-prompts none`. In ticket mode the system prompt says instead
  that questions go on the ticket via the Blocked loop. Permission prompts stay off.

### Playbooks

Two parts, split by who may change them:

- **The rules** live in the dispatcher's ConfigMap, reviewed in this repo:
  - name;
  - ready rule (`ready` | `auto`);
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

**First two playbooks.**

| Playbook | Does | Allowed repos | Proposed ready rule |
| --- | --- | --- | --- |
| `docs-fix` | Docs-only changes: typos, stale commands, broken links, a README section the ticket describes. Verification: the diff touches only docs paths (`*.md`, `docs/`), and the repo's own lint passes | In-scope repos | `ready` at first; **auto after five clean runs**, if George agrees |
| `dep-hold` | Investigates why a dependency update is held or stuck, and either opens a fix PR or writes up the cause on the ticket. Verification: a PR, or a ticket comment that names the blocking change | In-scope repos | `ready` |

- The brief lists both as auto-start candidates. **Auto-start is George's call per playbook**, so
  both start ready-gated and the plan only proposes when to flip them.
- **`dep-hold` overlaps the [renovate fixer plan](2026-09-28-renovate-fixer-bench-cloud.md)**
  (`planned`), where `renovate-automerge` launches the fixes itself. This playbook is the
  ticket-driven version for one-off holds George wants looked at. If both ship, the renovate fixer
  could open its work as `dep-hold` tickets instead of bare Jobs. That's an open question below,
  not decided here.
- The brief's exit criteria ask for **at least three** playbooks. The third is an open question.

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
  The gates above (George-applied `ready`, George-created auto tickets, George-authored comments)
  don't trust anything an agent can write.

### Network

| From | To | Port |
| --- | --- | --- |
| `plane-{stage,prod}` worker (webhook delivery) and api (save-time check) | `agent-desk[-stage]` | dispatcher port |
| `agent-desk[-stage]` | Plane api in the matching namespace | 8000 |
| `agent-desk[-stage]` | kube-apiserver | 443/6443 |
| `agent-desk[-stage]` | public internet (GitHub), 0.0.0.0/0 minus private ranges, as bench-cloud | 443 |

Plane's own policies get the matching egress (worker) and ingress rules.

## Steps

Staging first. There is one bench-cloud, and it's production, so staging never launches a real Job:
the staging dispatcher runs in **dry-run**, creating Jobs with `dryRun=All`. That still exercises
RBAC and the admission policy.

1. **This plan PR.** Review; answer the open questions.
2. **Plane prep and checks on staging.**
   - Create the In review and Blocked states, the `ready` and `playbook:*` labels, and a test repo
     project. Add `agent-desk` to that project only.
   - Verify, recording what's found here:
     - a Member's token is refused on a project it isn't a member of;
     - the activity API shows who added a label;
     - what the webhook payload carries for label and comment events.
   - **Exit:** all three answered, with evidence.
3. **Dispatcher on staging, dry-run.** Image, `agent-desk-stage` namespace, RBAC, network policies,
   webhook secret, the staging `WEBHOOK_ALLOWED_HOSTS` switched from the smoke-test receiver to the
   dispatcher. **Exit:**
   - a signed delivery is accepted and a bad signature rejected;
   - `ready` added by George produces a dry-run Job that passes admission;
   - `ready` added by `agent-desk` does not;
   - a ticket in HOMELAB (or a project mapped to `gjcourt/homelab`) is refused;
   - a second `ready` on a running ticket doesn't create a second Job;
   - with webhooks disabled, the poll still finds the ticket within 10 minutes.
4. **Ticket mode in bench-cloud.**
   - `bench-task` learns `TASK_TICKET`; the image gets the pinned Plane MCP server, the comment hook
     and the managed settings.
   - The Jobs' Plane token goes into a SOPS secret (operator).
   - **Exit:** a hand-launched ticket Job against a staging ticket reads it, comments, attaches a
     link and sets In review, and a comment George writes mid-run reaches the agent.
5. **Production dispatcher with `docs-fix`, ready-gated: first end-to-end milestone.** **Exit:**
   George labels one ticket `ready` → a bench-cloud Job opens a PR → the PR is linked on the ticket
   → the ticket reaches **In review**. George merges → the ticket reaches **Done**.
6. **Question loop live.** **Exit:**
   - an agent's question parks the ticket in Blocked;
   - George's reply resumes it in a new Job that has the answer;
   - a crashed Job's ticket lands in Blocked with the record path.
7. **`dep-hold` playbook.** **Exit:** one real held update investigated through a ticket.
8. **Auto-start, per George's decision per playbook.** **Exit:** one auto playbook starts a
   George-created ticket without `ready`; an `agent-desk`-created ticket with the same playbook does
   not start.
9. **Planner, later.** A merged plan PR → draft tickets in Triage, written as a bench-cloud skill
   (per the brief).
10. **Third playbook and the pilot:** one real plan → tickets → merged PRs through the loop.

## Exit criteria (the brief's phase 1, restated for this plan)

- [ ] A startable ticket launches a bench-cloud Job, and the Job's PR is linked back to the ticket.
- [ ] An agent question on a ticket, answered by George, resumes the same work.
- [ ] A comment George writes on a ticket reaches its running Job mid-task, and the reply appears on
      the ticket.
- [ ] At least three playbooks exist, each declaring its own ready rule, and at least one runs on
      its own.
- [ ] The dispatcher provably refuses homelab-repo tickets and duplicate pickups.
- [ ] A merged plan PR produces draft tickets in Plane without manual copying.
- [ ] One full project goes plan → tickets → merged PRs through the loop, with George merging every
      PR.

## Risks

- **Prompt injection through tickets.** Ticket text reaches an agent with push rights. Mitigated by
  fencing and by gates an agent can't satisfy itself. Not eliminated: an agent can still be misled
  within its own task, which is why babysit, critique and George's merge stay in the loop.
- **Subscription burn.** Every dispatched ticket is a Job on George's Claude subscription. The
  dispatcher's cap (proposed 3) bounds it. Per-task usage is already recorded in each run's
  `result.json`.
- **Missed events.** Webhooks retry 5 times and stop. The 10-minute poll is the backstop, and step 3
  tests it with webhooks off.
- **Two Plane keys at one rate limit each.** If Jobs plus the hook outgrow 60/min, raise
  `API_KEY_RATE_LIMIT` (a chart setting) rather than adding keys.
- **The actor check depends on Plane exposing it.** If step 2 finds the activity API doesn't show
  who added a label, the ready gate needs another design before step 3, e.g. George sets a state
  only his account can see. Stop and revisit rather than ship a gate an agent could open.

## Open questions for George

1. **Repos in scope first.** DOMESTIQUE is the only repo project today. Which repos should get Plane
   projects and agent access for the first milestone, and should the first end-to-end run use a
   real repo or a throwaway sandbox repo?
2. **Auto-start.** Keep both first playbooks ready-gated? If `docs-fix` should go auto later, is
   "five clean runs" the right bar?
3. **Third playbook.** Candidates from the brief's automations: failing main CI, a recurring chore.
   Or something George does by hand often.
4. **Renovate fixer.** Should it open `dep-hold` tickets (one record, visible in Plane), or keep
   launching Jobs directly as its plan says?
5. **Dispatcher concurrency cap.** 3 of the 8-pod quota, or another number?
6. **Notifications.** Plane's own notifications are the phase-1 push channel (web and mobile app).
   Is that enough for "an agent asked you a question", or does George want something louder
   (e.g. an email via the existing alert path) before phase 2?
