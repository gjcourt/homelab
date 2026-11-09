---
status: planned
last_modified: 2026-11-09
summary: "Rename agent-desk to bench-desk (Phase A, own PR, zero lost webhooks), then make Renovate PRs a second bench-desk work source: renovate-review and renovate-fix run as bench-cloud tasks, results go back as a sha-bound commit status + held-by-review, renovate-automerge stays the only merger with every 2026-09-28 gate; retire the renovate-review CronJob"
---

# bench-desk: rename agent-desk, and run Renovate review and fixes on it

## Goal

One agent platform, one name family, one dispatcher.

1. **Phase A:** rename `agent-desk` (the Plane → bench-cloud Job dispatcher) to
   **`bench-desk`**, as its own PR series, without dropping a Plane webhook or a running Job.
2. **Phase B:** give bench-desk a second work source besides Plane, **open Renovate PRs**,
   dispatched as bench-cloud tasks under two playbooks: `renovate-review` (the risk review that
   applies `held-by-review`) and `renovate-fix` (the stuck-PR fixer from the
   [2026-09-28 fixer plan](2026-09-28-renovate-fixer-bench-cloud.md)).
3. **Retire** `infra/controllers/renovate-review/` once Phase B is live.

## Decisions (George, 2026-11-09)

| # | Topic | Decision |
| --- | --- | --- |
| 1 | Naming | The agent platform is the **bench-\* family**. `bench-cloud` stays the runtime name (namespace, image, CLI, `bench-cloud[bot]` GitHub App; renaming the App would break bench-merge-audit and admission policies keyed on its login). `agent-desk` is renamed **bench-desk**. The guardrails keep their bench-gate / bench-paths / bench-approval / bench-merge-audit names. No new "agent-\*" names |
| 2 | Renovate review | George: "let's get renovate-review fully on this agent-bench / bench-cloud architecture". The nightly `renovate-review` CronJob becomes work dispatched by bench-desk and run as bench-cloud tasks; the CronJob is retired |

Everything else below is a proposal. Where it needs George, it is in
[Open questions](#open-questions-for-george).

## What exists today (verified 2026-11-09)

### agent-desk

- **Code:** `images/agent-desk/`, Python package `agent_desk/` (dispatcher, rules, plane, github,
  jobs, prompt, notify, server), tests in `images/agent-desk/tests/`. Built by
  `.github/workflows/build-agent-desk.yml` (`IMAGE_NAME: gjcourt/agent-desk`, line 19) from the
  repo root so it can copy `scripts/bench-cloud/job.yaml` (`images/agent-desk/Dockerfile`).
- **Stateless, one worker thread.** Plane plus Job labels are the record
  (`images/agent-desk/README.md`); `server.py:79-117` queues webhooks, the 10-minute Plane poll and
  the 5-minute GitHub poll onto a single worker so two events can't both start a Job. The
  Deployment is `replicas: 1`, `Recreate` (`apps/base/agent-desk/deployment.yaml`).
- **Job identity:** labels `agent-desk/ticket`, `agent-desk/playbook`, `bench-cloud/task`, annotation
  `agent-desk/ticket-key` (`jobs.py:19-21,37-41`); task id `ad-<ticket-key>-<yyyymmdd>-<hex>`
  (`jobs.py:24-28`). It lists its Jobs **by the ticket label** (`jobs.py:88`), and counts a playbook's
  load as active Jobs ∪ In Progress tickets (`dispatcher.py:189-194`). Caps are **per playbook
  only**; there is no desk-wide cap.
- **Production caps** (`apps/production/agent-desk/config.yaml`): `docs-fix` 6, `design` 2,
  `review` 1, `feature` 8, i.e. 17 at most, inside bench-cloud's 24-pod quota
  (`apps/base/bench-cloud/resourcequota.yaml`), which George's own `bench-cloud run` tasks share.
  Staging's config still carries the unshipped `dep-hold` and `main-ci` playbooks.
- **Namespaces:** `agent-desk` (production, unsuffixed by the loop plan's Design table) and
  `agent-desk-stage` (staging, `DRY_RUN_JOBS=true`: Jobs are created with `dryRun=All`, because there
  is one bench-cloud and it is production). Both run today (`kubectl get deploy`: 1/1 each).
- **GitHub:** a read-only fine-grained PAT (Pull requests + Actions read) in the SOPS secret
  `secret-agent-desk.yaml` (`github.py:1-6`). It never writes.
- **Plane webhook:** `http://agent-desk.agent-desk.svc.cluster.local/plane`, HMAC-checked. The host is
  the one entry in production Plane's `WEBHOOK_ALLOWED_HOSTS`, on both api and worker
  (`apps/production/plane/release-patch.yaml:44,67`); staging lists
  `agent-desk.agent-desk-stage.svc.cluster.local,plane-mobile.plane-mobile-stage.svc.cluster.local`.
  Plane **resolves the host when the webhook is saved**, so the Service must exist first, and it
  **deactivates a webhook after its retries fail** (`docs/operations/apps/agent-desk.md` §3–4).

### Every place the name appears

`grep -ril agent-desk` outside `images/agent-desk/` and `apps/*/agent-desk/` (functional unless
marked *text*):

| Where | What |
| --- | --- |
| `apps/{production,staging}/kustomization.yaml:5` | the overlay entry |
| `apps/base/bench-cloud/rbac.yaml:52-104` | RoleBindings `agent-desk-task-launcher`, `agent-desk-console-image-read` for SA `agent-desk` in both namespaces |
| `apps/{production,staging}/plane/release-patch.yaml` | `WEBHOOK_ALLOWED_HOSTS` |
| `apps/{production,staging}/plane/networkpolicy.yaml` | api ingress from, and webhook egress to, the agent-desk namespace |
| `apps/{production,staging}/homeassistant/kustomization.yaml` | HA ingress from the agent-desk namespace (notify webhook) |
| `apps/base/homeassistant/files/automations.yaml:349-382` | automation alias/title *text*; `!secret agent_desk_webhook_id` (key lives in the SOPS HA secret) |
| `scripts/bench-gate/rules.yaml:20` + `scripts/bench-gate/tests/test_check.py` | **deny `apps/*/agent-desk/**`**: what keeps agents off the dispatcher's own config |
| `infra/controllers/kube-prometheus-stack/values.yaml:2710` | KSM `metricLabelsAllowlist: jobs=[agent-desk/playbook,bench-cloud/task,agent-desk/ticket]` |
| `infra/configs/alerts/bench-fleet-rules.yaml:34-48` | recording rules: `job_name=~"ad-.*"`, project regex `ad-([a-z0-9]+)-.*`, `label_agent_desk_playbook` |
| `infra/configs/dashboards/bench-fleet-cm.yaml:1018,1150` | panel descriptions (caps copied from the agent-desk config) |
| `images/bench-cloud/{bench-task,bench-ticket,Dockerfile}`, `images/plane-sync/…` | *text* (comments) |
| `apps/production/agent-desk/config.yaml` `repoNotes` | *text the agent reads*: "never: apps/\*/agent-desk/\*\*" |
| `docs/operations/apps/agent-desk.md`, `docs/operations/apps/{bench-cloud,plane}.md`, `docs/STATUS.md:43`, `apps/README.md:9`, the plans | docs |
| `~/src/agents/desk/` (gjcourt/agents) | `/desk` skill: `SKILL.md:6,11,78,101`, `README.md:3,15,17` (kubectl access to the `agent-desk` namespace), `desk.py` docstrings |
| SOPS files: `secret-agent-desk.yaml` (both envs), `secret-plane-jobs-token.yaml`, `secret-homeassistant-secrets.yaml` | operator-only; not touched by agents |

No homelabscope rule mentions agent-desk or renovate-review (`infra/configs/homelabscope/`
covers `renovate`, `renovate-automerge`, `github-rulesets`, `bench-merge-audit` only).

### renovate-review (the CronJob being retired)

- Nightly at 01:00 in `renovate`, on the bench-cloud image (`cronjob.yaml:46`, pinned
  `2026-10-01-60e3604`) but with `command:` replacing the entrypoint, so it is **not** a bench-cloud
  task: no quota, no run record, no dashboard. `REVIEW_MODE: shadow` (`configmap.yaml:16`): it calls
  the model and posts nothing. **It has never posted**; no open PR carries `held-by-review` today
  (`gh search prs --label held-by-review`: 0).
- Three passes through `claude -p` on Opus 5.5 high (`configmap.yaml:12-13`): A release-notes lens,
  B repo-impact lens (concurrent), C a verifier that tries to refute their findings, run only when A
  or B holds. Fail-closed throughout (`normalise`, reviewer error → hold).
- Reviewer tools are `Read,Grep,Glob` only, **no Bash and no web** (`script-configmap.yaml:45`);
  `GITHUB_TOKEN` and API keys are stripped from the child env (`:196`).
- Its token is a fine-grained PAT **without Contents: write** (`kustomization.yaml:6-8`): "can only
  hold, never merge" is a property of the credential.
- **Scope today:** `needs_judgement()` (`:74`) reviews only **majors and PRs with no update
  column**, which `renovate-automerge` already holds on its own. Live, it would only add a comment
  to PRs that were never going to auto-merge. The PRs where a review would change an outcome,
  green patch/minor ones (the 2026-09-28 plan's "viem-style patch that's actually breaking"), are
  skipped as "routine".
- Its `is_renovate_pr()` (`:59`) is the **old, loose** one (title or author-substring fallbacks),
  not the hardened one in renovate-automerge.

### renovate-automerge: what #1504 actually shipped

The fixer plan's PR 1 shipped as #1504. Against that plan, in
`infra/controllers/renovate-automerge/script-configmap.yaml`:

| 2026-09-28 PR 1 item | Live? | Where |
| --- | --- | --- |
| Renovate PR = `renovate/` branch **and** head in the same repo; title/author fallbacks dropped | yes | `is_renovate_pr` `:192-222` |
| "PR author is the token owner" | **not as written**; instead `touched_by_app()` holds any PR the App authored or committed to (`:240-262`), and `edited_by_other()` holds unless every body edit, retitle, unlabel and base change was by the PR's author (`:290-324`). Both fail closed | `:240-324` |
| Strict CI: ≥1 check run, none pending, all success | **partly**: pending checks now fail, but a head with **no check runs and no statuses still passes** ("a repo without CI", `:462-484`) | `ci_passing` |
| Merge pinned to the evaluated `sha` | yes | `merge_pr` `:516-524` |
| `renovate-branch-guard` ruleset | yes: `scripts/github-rulesets.sh:99`; present on homelab (`gh api repos/gjcourt/homelab/rulesets`: `default-branch-guard`, `renovate-branch-guard`) | |
| `held-by-review` honoured | yes, `:52,562` | |

PR 2 (the fixer, the diff-equality gate, `renovate-fixer-state`) was **never built**.

### Volume (measured with `gh search prs`, 2026-10-26 → 2026-11-09)

86 Renovate PRs opened, 87 merged (74 in homelab). **48 of the 86 are digest bumps.** So ~38
non-digest PRs in 14 days, majors included: under 3 a day. Five are open now, all in homelab.

### Other constraints that shape the design

- The **bench-cloud App** has Contents/PRs/Issues RW, Checks read, **Commit statuses: read**
  (`docs/operations/apps/bench-cloud.md:56-60`). It cannot post a commit status.
- In **homelab**, every App-authored PR gets `bench-paths` and `bench-approval` required statuses
  from master's `scripts/bench-gate/` (`.github/workflows/bench-gate.yaml:9,45`). `bench-approval`
  fails until George approves the head SHA, and `ci_passing()` requires the combined status to be
  `success`, so **an App-authored fix PR in homelab can never auto-merge**. A homelab Renovate
  PR that touches `infra/**`, `images/**` or `.github/**` (HelmReleases, Dockerfiles, workflows)
  is also outside `bench-paths`, so the fixer can't even push a fix for it.
- `tempo-interview` is the only private repo of the eleven; the rest are public.
- The bench gate reads its rules **from master**, not from the PR.

## Phase A: rename agent-desk → bench-desk

Mechanical, no behaviour change. Four PRs plus two Plane-UI steps by George, staging a day ahead of
production.

### New names

| Thing | Old | New |
| --- | --- | --- |
| Namespaces | `agent-desk`, `agent-desk-stage` | `bench-desk-prod` (see Q5), `bench-desk-stage` |
| Image, workflow | `ghcr.io/gjcourt/agent-desk`, `build-agent-desk.yml` | `ghcr.io/gjcourt/bench-desk`, `build-bench-desk.yml` |
| Source, package | `images/agent-desk/`, `agent_desk` | `images/bench-desk/`, `bench_desk` |
| Deployment / Service / SA / container | `agent-desk` | `bench-desk` |
| ConfigMap, Secret, SOPS file | `agent-desk-config`, `agent-desk`, `secret-agent-desk.yaml` | `bench-desk-config`, `bench-desk`, `secret-bench-desk.yaml` (+ `.example`) |
| Env | `AGENT_DESK_CONFIG` | `BENCH_DESK_CONFIG` (read both for one release) |
| NetworkPolicies | `agent-desk`, `agent-desk-plane`, `agent-desk-notify` | `bench-desk`, `bench-desk-plane`, `bench-desk-notify` |
| RoleBindings in bench-cloud | `agent-desk-task-launcher`, `agent-desk-console-image-read` | `bench-desk-task-launcher`, `bench-desk-console-image-read` |
| Job labels / annotation | `agent-desk/ticket`, `agent-desk/playbook`, `agent-desk/ticket-key` | `bench-desk/ticket`, `bench-desk/playbook`, `bench-desk/ticket-key` |
| Task id | `ad-<key>-<date>-<hex>` | `bd-<key>-<date>-<hex>` |
| Plane webhook URL | `http://agent-desk.agent-desk.svc.cluster.local/plane` | `http://bench-desk.bench-desk-prod.svc.cluster.local/plane` |
| Comment/prompt text | "agent-desk couldn't create the Job", the `agent-desk` speaker tag (`dispatcher.py:298,340`) | "bench-desk …" |
| Runbook | `docs/operations/apps/agent-desk.md` | `docs/operations/apps/bench-desk.md` |

**Kept on purpose** (see Q6): the Plane bot account `gjcourt+agent-desk@gmail.com` (an identity
that `botEmail` and project memberships key on; only its display name changes), the HA secret key
`agent_desk_webhook_id` (inside a SOPS file), and the dated plan filenames
(`2026-10-22-agent-desk-loop.md` is a record; it gets a one-line "renamed bench-desk" note).
`bench-cloud/task` stays: it is bench-cloud's label.

### Order

**A0. Protect the new path first (tiny PR, merge before anything else).** Add
`apps/*/bench-desk/**` to `scripts/bench-gate/rules.yaml` deny (keep the agent-desk entry), with
tests, and update the homelab `repoNotes` text. The gate runs master's rules, so if this landed in
the same PR as the new directory, an agent PR touching `apps/production/bench-desk/config.yaml`
while the rename was open would pass `bench-paths` (`bench-approval` would still stop it, but the
path gate is the boundary).

**A1. Code + image.** `git mv images/agent-desk images/bench-desk`, package `bench_desk`, the new
workflow, all strings above. The dispatcher lists its Jobs by **both** label keys (two list calls,
merged) and reads both env names, for one release, so Jobs started before the cutover are still
reaped and counted against caps. Build prints the first `ghcr.io/gjcourt/bench-desk:<date-sha>`.
It is a new package: its first tag is necessarily later than agent-desk's current
`2026-11-08-2f602ea`, and strictly-increasing applies from there. George checks the new GHCR
package's visibility/pull access matches agent-desk's (a new package does not inherit it).

**A2. Stand up bench-desk beside agent-desk, at `replicas: 0`** (one PR per env, staging first):

- `apps/base/bench-desk/` + overlays, Deployment patched to `replicas: 0`, the Service live (Plane
  must resolve it when the webhook is saved). Never two dispatchers at once: each has its own
  worker thread, and two would double-start tickets.
- `secret-bench-desk.yaml.example`; **George** encrypts `secret-bench-desk.yaml` with the same
  `plane-api-key`, `github-token`, `notify-webhook-id`, and a placeholder `webhook-secret`.
- RoleBindings in `apps/base/bench-cloud/rbac.yaml` gain the `bench-desk` SAs (old ones kept).
- Plane `WEBHOOK_ALLOWED_HOSTS` lists **both** hosts (exact names, comma-separated, as staging
  already does); Plane and HA network policies allow both namespaces.
- KSM allowlist carries both label sets:
  `jobs=[agent-desk/playbook,agent-desk/ticket,bench-desk/playbook,bench-desk/ticket,bench-cloud/task]`.
- Recording rules match `job_name=~"(ad|bd)-.*"`, project `(?:ad|bd)-([a-z0-9]+)-.*`, and playbook
  from `label_bench_desk_playbook` **or** `label_agent_desk_playbook`, so history stays continuous.

**A3. Webhook (George, Plane UI).** Workspace settings → Webhooks → add a **second** webhook to the
bench-desk URL (work items + comments). Copy its secret into `secret-bench-desk.yaml` (SOPS PR,
George). Both webhooks are now active; deliveries to bench-desk fail with connection refused (no
endpoints), which Plane retries and may eventually deactivate. If it does, George re-enables it
after A4.

**A4. Cutover (one PR per env).** agent-desk `replicas: 0`, bench-desk `replicas: 1`, in the same
commit, so one Flux apply swaps them. There is never a moment with two workers. During the swap
both webhooks may miss deliveries; bench-desk's first action is a full poll ("a restart re-derives
everything", README), so nothing is lost but latency, at most one poll. Running Jobs carry on:
the dual-label read reaps them. Verify: bench-desk log shows the poll, a `ready` on a SANDBOX
ticket starts a `bd-` Job, Plane shows the new webhook's deliveries succeeding, HA notice arrives.

**A5. Remove agent-desk (George, then PR).** George deletes the old webhook in Plane. Then one PR
per env removes `apps/*/agent-desk/` (Flux prunes the namespace and its secret), the old
RoleBindings, the old allowed host and policy rules, `build-agent-desk.yml`, and, once no `ad-` Job
is left (Job TTL is 24 h, `scripts/bench-cloud/job.yaml:17`), the old label keys from the KSM
allowlist and the dual-label read. The `(ad|bd)` recording-rule regex stays for Prometheus
retention. The `agent-desk` image stays in GHCR as the rollback.

**A6. Elsewhere.**

- `/desk` skill (gjcourt/agents, separate PR after A4): `SKILL.md` config path
  `apps/production/bench-desk/config.yaml`, the description and headings, `README.md` kubectl
  access to `bench-desk-prod`, `desk.py` docstrings. The skill name `/desk` stays.
- Docs in this repo: runbook renamed, `bench-cloud.md`, `plane.md`, `STATUS.md`, `apps/README.md`,
  dashboard descriptions, HA automation alias text. Comment-only mentions in `images/bench-cloud/`
  ride along with Phase B's bench-cloud image change rather than forcing a rebuild now.

## Phase B: Renovate PRs as a bench-desk work source

### Shape

```text
            poll every 15m (11 repos)                          bench-cloud Jobs
GitHub ───────────────────────────────▶ bench-desk ──create──▶ renovate-review (bench-review program)
  ▲                                       │    ▲                renovate-fix   (bench-task agent)
  │ status bench-desk/renovate-review     │    └── termination message (verdict) / fix PR
  │ + held-by-review + comment ◀──────────┘
  │ status bench-desk/renovate-fix-of (on the fix PR)
  │
renovate-automerge (every 6h, George's token): the only merger.
  - Renovate PR in review scope: merge only with a success review status on that sha
  - bench-fix/ PR: merge only through the 2026-09-28 gates, then close the Renovate PR
```

One dispatcher, one merger. bench-desk decides **what runs**; renovate-automerge decides **what
merges**. The 2026-09-28 plan's "automerge launches the fix task and tracks it in
`renovate-fixer-state`" is **superseded**: automerge never creates Jobs, gets no cluster RBAC, and
that ConfigMap is never created. (The loop plan's step 9, "automerge opens `dep-hold` tickets", is
superseded too, subject to Q1.)

### Discovery: poll, not a GitHub webhook

- **Poll** each repo in `renovate.repos` every 15 minutes: one `GET /pulls?state=open` per repo, then
  per-candidate checks. 11 repos is 44 list calls an hour against 5,000; volume is under 3
  non-digest PRs a day.
- A webhook would need a **public** endpoint into the one process that creates Jobs (bench-desk has
  no ingress beyond Plane's worker today, by design), a Cloudflare route, an HMAC secret per repo
  or an org-level hook, and a poll behind it anyway, since GitHub drops deliveries too. Renovate
  runs `@daily` and automerge every 6 h, so 15 minutes of latency costs nothing.
- The poll runs on the **same worker thread** (a third timer next to `POLL` and `GITHUB`,
  `server.py:91-101`), so Renovate decisions are serialised with Plane ones and caps are counted in
  one place. Every GitHub call has a timeout, so a slow API delays Plane work by seconds, not more.

### Which PRs, and which playbook

bench-desk applies renovate-automerge's own checks before it spends a task. To stop the two
drifting, the classifier and identity checks (`is_renovate_pr`, `touched_by_app`,
`edited_by_other`, `classify_pr`, `ci_passing`) move out of the inline ConfigMap into
`scripts/renovate/renovatelib.py`: automerge's ConfigMap is generated from it
(`configMapGenerator`), and bench-desk's Dockerfile copies it, as it already copies
`scripts/bench-cloud/job.yaml`. If they ever do drift, it fails closed: automerge holds any PR in
review scope without a review status.

| Renovate PR (passes strict identity, not touched by the App, not edited by others) | Playbook | Starts |
| --- | --- | --- |
| Automerge would merge it (patch/minor, not runtime/held deps), CI green, **not** digest/pin/pinDigest (Q2) | `renovate-review` | once per distinct diff (below) |
| Safe type, **conflicting** (`mergeable: false`) or a **completed failed** check | `renovate-fix` | once per head sha, at most 2 attempts |
| Major, runtime, held dep, unparseable, `held-by-review` already | none | George, as today |

**One review per distinct diff, not per sha.** Renovate rebases and automerge's `update-branch`
(`merge_pr` `:537`) change the head sha without changing the update. bench-desk hashes the PR's
file diff against its base (`git patch-id --stable` over the PR's `.diff`), puts it in the status
description, and when a new sha has the same hash as an earlier reviewed sha of the same PR, it
re-posts that verdict instead of starting a Job. Without this, every automerge rebase would buy a
fresh Opus review.

### Per-PR state: GitHub and Job labels, nothing else

bench-desk stays stateless. The record is:

- **Commit status `bench-desk/renovate-review`** on the Renovate PR's head sha: `pending` (Job
  started, description names the task id and diff hash), then `success` (pass) or `failure`
  (hold), or `error` (Job failed; counts as an attempt). Statuses are sha-bound, so a push voids
  them by construction, which is what the merge gate needs. Attempts are counted from the statuses
  bench-desk's token created on that sha.
- **Commit status `bench-desk/renovate-fix`** on the Renovate PR's head sha, the same way, for fix
  attempts; and **`bench-desk/renovate-fix-of`** on the **fix PR's** head sha, description
  `<owner/repo>#<n>@<renovate sha>`, posted by bench-desk when the fix Job ends with a PR.
- **Job labels** `bench-desk/playbook`, `bench-desk/source=renovate`, `bench-desk/project=<repo
  slug>`, annotations `bench-desk/renovate-pr=<owner/repo>#<n>` and `bench-desk/renovate-sha`.
  Renovate Jobs have **no ticket label**, so `Kube.jobs()` must list by `bench-desk/playbook`
  instead of by the ticket label (`jobs.py:88` today), or cap counts would miss them.

A restart re-derives everything from one poll: open PRs, their statuses, and active Jobs.

**Who can forge a status.** Anyone with Commit statuses: write on the repo: George (and Renovate,
which runs as George), and workflow `GITHUB_TOKEN`s. Not the bench-cloud App (statuses: read).
renovate-automerge accepts a `bench-desk/*` status only if its `creator.login` is the bench-desk
token's owner, which excludes `github-actions[bot]`.

### renovate-review as a bench-cloud task

The review keeps its tested three-pass design. It becomes a **program run inside a bench-cloud
task**, not one free-form agent session:

- `review.py` moves into the bench-cloud image as `bench-review` (single-PR mode only, `--pr`
  semantics), with its prompts, `normalise`, fail-closed rules and mock-mode tests. A, B, C stay
  separate `claude -p` calls with `--tools Read,Grep,Glob`: no Bash, no web, exactly as today.
  One open-ended agent session with Bash would read untrusted release notes with a shell.
- bench-desk builds the Job from `scripts/bench-cloud/job.yaml` like every other task (same SA,
  PriorityClass, quota, admission policy), with three changes for this playbook:
  1. **No App private key in the task container.** An init container (bench-cloud image, which has
     the key mounted) mints an installation token **downscoped** to the one repo with
     `contents: read, pull_requests: read, metadata: read` and writes it to an in-memory
     `emptyDir`. The task container mounts only that token. `images/bench-cloud/github-app-token`
     gains `--repo`/`--permissions` (the access-tokens API takes `repositories` and `permissions`;
     today it mints full rights, `github-app-token:59`). So a prompt-injected reviewer has a 1-hour
     read-only token for a repo it could read anyway.
  2. Command `bench-entrypoint bench-review` instead of `bench-task`; no `TASK_TICKET`, no Plane key.
  3. Model and effort from the playbook (`claude-opus-5-5`, `high`, as `configmap.yaml:12-13`).
- It still writes `meta.json`, `result.json` (costs summed over the three calls) and the record to
  `hestia:agent-inbox/runs/<task>/`, so runs show up on the fleet dashboard like any other task.
- **The verdict goes back through the pod's termination message** (`/dev/termination-log`, ≤4 KB
  JSON: verdict, confidence, root cause, confirmed findings). bench-desk reads it from the
  finished pod. This needs `get,list` on pods in bench-cloud: a new Role `bench-task-pod-read`,
  bound to the bench-desk SAs in `apps/base/bench-cloud/rbac.yaml`. Not the hestia record: every
  task's ssh key can write anywhere under `runs/`, so that file isn't tied to the run that wrote it.
  A missing or unparseable message is a hold, as today.
- **bench-desk, not the task, writes to GitHub**, with its own token: the status, and on hold the
  `held-by-review` label and one comment. The comment uses `comment_body()`'s fixed template; model
  text is length-bounded and has `@` mentions and links defanged, since it is posted under George's
  PAT. Hold-only stays a credential property: this token has no Contents: write.

### renovate-fix as a bench-cloud task

The 2026-09-28 design, re-homed:

- A normal `bench-task` agent session (it must edit files and push), full App token, prompt from
  that plan: the Renovate PR, the failing check and a log excerpt, the tiers, the branch name
  `bench-fix/<task-id>`, "if it isn't mechanical, say so and stop", never push to `renovate/`
  (the ruleset refuses it anyway), never comment on, edit or close the Renovate PR. Model on the
  Sonnet tier (dependency files and YAML), per George's model tiers.
- On Job end, bench-desk finds the PR from `bench-fix/<task-id>`, posts `bench-desk/renovate-fix-of`
  on its head, and comments once on the Renovate PR linking it. No PR: status `error`; after the
  second attempt, a comment for George and a phone notice.
- If the App edits the Renovate PR's body or title anyway, `edited_by_other()` holds it: fail closed.
- **homelab:** a fix PR here is App-authored, so `bench-approval` needs George's approval of its
  head and `bench-paths` refuses any fix outside `apps/**` and `docs/**`. Fixes in homelab are therefore always
  George's to merge. Fine; it is what the gate is for.

### How renovate-automerge consumes the results

renovate-automerge stays the only thing that merges, still with George's token, and still every 6 h.
Two additions, each behind its own env switch so rollout is per step:

1. **`REVIEW_GATE=on`:** a Renovate PR in review scope merges only if the evaluated head sha has
   `bench-desk/renovate-review = success` created by the bench-desk token owner. Missing or pending
   → hold "awaiting review"; failure → hold (the label also holds it, `:562`). Shown in the
   existing email report.
2. **`FIX_MERGE=on`:** for open PRs whose head is `bench-fix/<id>` in the same repo, authored by
   `bench-cloud[bot]`, all of:
   1. **Bookkeeping:** a `bench-desk/renovate-fix-of` status by the bench-desk token owner on the
      fix PR's head, naming the Renovate PR. This links the PR to a dispatch; it is **not**
      provenance, exactly as in the 2026-09-28 plan.
   2. **Classification recomputed now** by automerge from the Renovate PR (strict identity,
      edit/label checks, `classify_pr` safe). bench-desk's view at launch is not trusted.
   3. **Scope as diff equality:** every file the fix touches is one the Renovate PR touches, or a
      lock/sum file; each non-lock file's change is **identical** to Renovate's; no workflow, CI or
      source files; `go.sum` accepted (hash-only, checksum DB); any other lock file only if every
      changed source resolves to the ecosystem's default registry, else George.
   4. **CI strict on the fix PR:** at least one completed check run, all success, combined status
      success (which includes `bench-approval` where it exists). Stricter than today's
      `ci_passing()`, which passes a head with no checks at all; the fix-PR path never does.
   5. **Merge with `sha`** = the evaluated head; a push by any task in between voids it.
   Then close the Renovate PR as superseded, with a comment, **only after** the merge.

### How the 2026-09-28 security gates carry over

| Gate | Where it lives after this plan |
| --- | --- |
| Strict Renovate-PR identity, App-touched hold, edit/unlabel hold | `renovatelib.py`, used by automerge (merge) **and** bench-desk (dispatch) |
| `renovate-branch-guard` | unchanged (`github-rulesets.sh`); the fix agent can't create or push `renovate/**` |
| Diff-equality scope gate | renovate-automerge, `FIX_MERGE` path, computed from the two diffs |
| Strict CI | fix-PR path requires ≥1 check run; review dispatch requires automerge's `ci_passing()` |
| sha-pinned merges | unchanged (`merge_pr`), and the review gate reads the status on that same sha |
| Bookkeeping ≠ provenance | `renovate-fix-of` status replaces the state ConfigMap; same weight, same caveat |
| bench-merge-audit | unchanged. Optional add from the 2026-09-28 plan: flag Renovate PRs closed by the App |
| Never bypass protection | automerge never merges past a failing required status: combined status must be success |

### Caps and the shared subscription

| Playbook | maxConcurrent | Deadline | Model |
| --- | --- | --- | --- |
| `renovate-review` | 1 (its A and B already run as two concurrent processes) | 45m | Opus 5.5, high |
| `renovate-fix` | 2 (the 2026-09-28 plan's "at most 2 per run") | 2h | Sonnet tier |

With today's 17 that is 20 of the 24-pod quota, leaving 4 for George. Proposed: a desk-wide
**`maxTotal: 18`** in config (24 − 6, the loop plan's "at least 6 of the 24 for George's own
tasks"), counted over all active desk Jobs; when it binds, Plane tickets start before Renovate work,
which just waits for the next poll (Q4). Renovate work never takes `over-cap`. Load is small:
under 3 reviews a day at the measured volume.

### Config (sketch)

```yaml
renovate:
  mode: off            # off | dry-run | shadow | live
  pollInterval: 15m
  repos: [gjcourt/homelab, gjcourt/golinks, gjcourt/llmux, gjcourt/Pingo, gjcourt/soundbyte,
          gjcourt/tempo-interview, gjcourt/vitals, gjcourt/drift, gjcourt/python-nginx-signing,
          gjcourt/toppingctl, gjcourt/workstation]   # = GITHUB_REPOS today; one fewer copy once renovate-review goes
  statusCreator: gjcourt
maxTotal: 18
playbooks:
  renovate-review: {source: renovate, program: bench-review, maxConcurrent: 1, deadline: 45m,
                    model: claude-opus-5-5, effort: high, githubPermissions: read}
  renovate-fix:    {source: renovate, maxConcurrent: 2, deadline: 2h, prompt: "…"}
```

A Plane ticket naming a `source: renovate` playbook is refused (rule 2's comment). New secret key
`renovate-token` in `secret-bench-desk.yaml` (George, SOPS): fine-grained PAT on the eleven repos,
Pull requests RW + Issues RW (label, comment), **Commit statuses RW**, Contents **read**,
Metadata read (Q7). Staging gets **no** write token: its Renovate source can only run `dry-run`,
which logs decisions and posts nothing. bench-desk's egress already allows the internet on 443.

### Rollout: shadow → live

| Step | bench-desk `renovate.mode` | automerge | Exit criterion |
| --- | --- | --- | --- |
| B-0 | `dry-run`: log which PRs would get which playbook | unchanged | 2 days of logs match automerge's own classifications |
| B-1 | `shadow`: review Jobs run; verdict to log + run record; nothing posted. Fix Jobs not started | unchanged | ≥1 week; plus a replay set (past Renovate PRs that broke and were reverted, and ~10 clean ones) run once through `bench-review --pr`; false-hold rate and cost per review written into this plan |
| B-2 | `live` for review: status + label + comment | `REVIEW_GATE=off` (label honoured as today) | 1 week, no wrong holds George had to clear more than once |
| B-3 | same | `REVIEW_GATE=on` | first in-scope PR merges after a success status |
| B-4 | `renovate-fix` enabled | `FIX_MERGE=off`: fix PRs wait for George | 3 fix PRs George would have merged as is |
| B-5 | same | `FIX_MERGE=on` | one mechanical fix auto-merged with every gate logged |

Rollback at any step is one config line: `mode: off` (and automerge's switches off); with
`REVIEW_GATE=off`, automerge behaves as today.

### PRs

1. **B1, bench-cloud image:** `bench-review`; `github-app-token --repo/--permissions`; bench-task
   writes `source`/`project` into `meta.json` from `TASK_SOURCE`/`TASK_PROJECT` when there's no
   ticket; comment renames from Phase A. Then the `bench-console` tag bump (desk Jobs run the
   console's image).
2. **B2, bench-desk code:** Renovate source, Job variants (review profile, fix profile), list by
   playbook label, `maxTotal`, GitHub writer, termination-message reader, `renovatelib.py` extracted
   (and automerge's ConfigMap generated from it, no behaviour change). Default `mode: off`.
3. **B3, wiring:** config blocks, `bench-task-pod-read` Role, KSM allowlist +
   `bench-desk/source`, `bench-desk/project`; George adds `renovate-token`. Staging's unshipped
   `dep-hold` playbook removed. Then the mode steps above, one small PR each.
4. **B4, automerge:** `REVIEW_GATE`, `FIX_MERGE` paths, both default off.
5. **B5, retire renovate-review** (below).

## Retiring `infra/controllers/renovate-review/`

When B-2 is live: remove the directory (CronJob, script ConfigMap, env ConfigMap, SOPS secret,
`.example`) and its line in `infra/controllers/kustomization.yaml:18`; fix the stale comment at
automerge `script-configmap.yaml:49` ("Applied by the renovate-review CronJob"). George revokes the
PAT if bench-desk got a new one (Q7). The CronJob posts nothing today, so it could go as early as
B-1 to stop paying for nightly shadow reviews of majors; this plan keeps it until B-2 so the
replacement is proven first.

| Lost | Kept / moved |
| --- | --- |
| Review of **majors and no-column PRs** (unless Q2 keeps them as advisory) | Three-pass A/B/C logic, prompts, tools restriction, fail-closed defaults, mock tests, now in `bench-review` |
| Independence from bench-desk: a bench-desk outage now stalls in-scope merges (they hold; fail closed) | `held-by-review` label semantics; automerge honours it unchanged |
| The `--pr` CLI replay against any PR, open or closed, from a CronJob pod | `bench-review --pr` in a one-off task |
| A separate credential only for review | Same "no Contents: write" property on bench-desk's `renovate-token` |

## Bench fleet dashboard

- Renovate Jobs carry `bench-desk/playbook` (`renovate-review`, `renovate-fix`),
  `bench-desk/source=renovate` and `bench-desk/project=<repo>`. Task ids are
  `bd-rn-<repo slug, hyphens dropped>-<pr>-<date>-<hex>` (e.g. `bd-rn-tempointerview-79-…`, under
  63 chars for the longest repo name).
- The recording rules take `project` and `source` **from labels** when present (allowlisted in KSM),
  falling back to the name regex for old Jobs. Repo names have hyphens, so the name regex alone
  can't carry them (`bench-fleet-rules.yaml` notes the no-hyphen assumption).
- Dashboard: a `$source` variable (plane | renovate); the caps panel gains the two playbooks and the
  `maxTotal` line; a Renovate row: reviews/day, review latency (PR green → status), fix attempts.
- **Verdict split and $/review** need Phase 2's `bench-metrics-exporter` (not built yet): it reads
  `result.json`, and `bench-review` writes `verdict` there, so the exporter adds
  `bench_renovate_verdicts_total{project,verdict}` with no new plumbing.
- Alert: `BenchRenovateReviewFailing`, two or more failed `renovate-review` Jobs in 24 h (from
  `bench:job_outcome:info`), since with `REVIEW_GATE=on` a broken reviewer silently stops merges.

## Risks

- **Merges stall when bench-desk or the reviewer is down** (fail closed under `REVIEW_GATE=on`).
  Visible in automerge's email ("awaiting review") and the alert above; rollback is one env line.
- **Prompt injection via release notes.** The reviewer keeps no shell, no web, a read-only 1-hour
  token, and no GitHub write. Its pod still holds the subscription token and the hestia ssh key,
  as every task does; with only Read/Grep/Glob it has no way to send them anywhere. The fixer has
  Bash and the App token, as in the 2026-09-28 plan; the merge gates are computed from diffs, not
  from anything it says.
- **Model text under George's name.** Comments are templated, bounded and defanged.
- **The single worker** now also serves the Renovate poll. Bounded by timeouts and by the volume;
  if Plane latency regresses, the poll moves to a second thread that only enqueues decisions.
- **Classifier drift** between bench-desk and automerge: one shared module; drift fails closed.
- **Renovate reacting to a closed PR:** closing as superseded suppresses that update; automerge
  closes only after the fix merged (2026-09-28 risk, unchanged).
- **Rename window:** Plane may deactivate the new webhook during A3–A4 (connection refused before
  cutover); the poll backstops it and George re-enables it. Two dispatchers never run together.
- **Subscription contention:** two more playbooks on the same subscription; `maxTotal` and the
  low measured volume bound it.

## Decisions on the open questions (George, 2026-11-09)

Answered one at a time; each answer is the option George picked.

| # | Question | George's answer |
|---|---|---|
| 1 | Does Renovate work bypass Plane and `ready`? | "Both auto-start": renovate-review and renovate-fix start from the GitHub source, no ticket |
| 2 | What does the review cover? | "Patch/minor, not digests": no digest/pin/pinDigest, no majors |
| 3 | Do mechanical fixes merge without George? | "Yes, behind all gates": the 2026-09-28 decision 3 stands, all five gates |
| 4 | Caps | "1 / 2, total 18": renovate-review 1, renovate-fix 2, desk-wide `maxTotal: 18`, Plane before Renovate |
| 5 | Production namespace | "bench-desk-prod" (staging `bench-desk-stage`); fix the AGENTS.md quality-gate line in A1 |
| 6 | Leftover agent-desk identities | **"Rename both"**: Plane bot email and the HA secret key `agent_desk_webhook_id` are renamed too (George does the Plane admin change and the SOPS edit; the plan's A5 must carry these steps) |
| 7 | GitHub token for statuses/labels | "New narrow PAT": PRs/Issues RW, Commit statuses RW, Contents read, eleven repos; revoke renovate-review's PAT at retirement |

The original questions, with their recommendations, are kept below for the record.

## Open questions (answered above) for George

1. **Does Renovate work bypass Plane and `ready`?** The loop plan has your 2026-10-22 decisions 2
   ("neither playbook auto-starts") and 4 (the fixer "opens `dep-hold` tickets ... so all agent work
   goes through Plane"). **Recommendation: yes, for both Renovate playbooks: they start from the
   GitHub source without a ticket.** The review can only hold, and fix PRs merge only through
   automerge's gates or through you. Their record is GitHub statuses, the run record and the fleet
   dashboard. Alternative: `renovate-fix` opens a Plane ticket that waits for your `ready`, and
   only `renovate-review` auto-starts.
2. **What does the review cover?** **Recommendation: patch/minor PRs that automerge would
   otherwise merge, minus digest/pin/pinDigest** (no release notes to read; 48 of the last 86
   Renovate PRs). Majors are not reviewed, since automerge already holds them for you. That is
   under 3 reviews a day. Alternative: also post an advisory review on majors, as today's script
   was built to.
3. **Do mechanical fixes still merge without you?** Your 2026-09-28 decision 3 says yes for
   patch/minor. automerge's code comment says "George's rule is that agent-written code on a
   default branch goes through him" (`script-configmap.yaml:225-228`). **Recommendation: keep
   decision 3, behind all five gates.** In homelab it is moot: `bench-approval` already requires
   your approval of every App PR.
4. **Caps.** **Recommendation: `renovate-review` 1, `renovate-fix` 2, plus a desk-wide `maxTotal:
   18` so 6 of the 24 pods stay yours; Plane tickets start before Renovate work when it binds.**
   Without `maxTotal`, the playbook caps sum to 20.
5. **Production namespace name.** AGENTS.md says production uses a `-prod` suffix (Conventions),
   but its quality gate says "production unsuffixed", and agent-desk is unsuffixed. **Recommendation:
   `bench-desk-prod`**, matching golinks-prod, plane-prod and homeassistant-prod. Every reference
   changes in the rename anyway. The AGENTS.md quality-gate line gets corrected in A1.
6. **Leftover agent-desk names behind SOPS or Plane accounts.** **Recommendation: keep** the Plane
   bot account email `gjcourt+agent-desk@gmail.com` (rename only its display name to bench-desk)
   and the HA secret key `agent_desk_webhook_id`. Changing them is a Plane admin change and a SOPS
   edit, and gains nothing. They are identities, not component names. Alternative: rename both
   in A5.
7. **Which GitHub token writes the review status and labels?** **Recommendation: a new
   fine-grained PAT for bench-desk** (`renovate-token`: PRs/Issues RW, Commit statuses RW, Contents
   read, on the eleven repos). Revoke renovate-review's PAT when the CronJob goes. Alternative:
   move renovate-review's PAT into bench-desk's secret and add Commit statuses: write.
