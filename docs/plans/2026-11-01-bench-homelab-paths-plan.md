---
status: planned
last_modified: 2026-11-01
summary: "agent-desk Jobs may open homelab PRs in apps/** and docs/**; a required check holds them until George approves the head"
---

# bench-cloud PRs in homelab, limited by paths

Plane: HOMELAB-85.

## Goal

agent-desk turns `ready` Plane tickets into bench-cloud Jobs. Today `gjcourt/homelab` is on its
`denyRepos` list, so no Job can work in this repo. This plan lets Jobs open homelab PRs for the
Plane projects HOMELAB and HASS, within a fixed set of paths. A merge to master still needs
George's approval of the exact commit that merges.

Plan only. Nothing changes until George approves it.

## Decisions (George, 2026-11-01)

1. **Paths.** Bench PRs may change `apps/**` and `docs/**`. A denylist always wins over that:
   `apps/*/agent-desk/**`, SOPS secrets (`**/secret-*.yaml`), network policies, `clusters/**`,
   `infra/**`, `.github/**`.
2. **Staging.** Bench PRs deploy to staging on green CI, the same as George's own PRs. No label
   gate.
3. **Merge gate.** A required status check fails on a bench-cloud PR until `gjcourt` has
   submitted an APPROVED review on the PR's current head SHA. It passes for every other author
   (George, Renovate). It is re-evaluated when the bot pushes and when George reviews.

## What was checked (2026-11-01)

All read-only. Nothing in GitHub, Plane or the cluster was changed.

| Item | Finding | Source |
| --- | --- | --- |
| App permissions | `actions: read, checks: read, contents: write, issues: write, metadata: read, pull_requests: write, statuses: read`. **No `workflows`, no `administration`** | Live: `performed_via_github_app` (id 5090609, slug `bench-cloud`) on the timeline of gjcourt/toppingctl#34, a PR the App opened. The runbook's setup step 1 lists the same set |
| Bot user | `bench-cloud[bot]`, id 334337744 | `gh api /users/bench-cloud%5Bbot%5D` |
| George | `gjcourt`, id 356410 | `gh api /users/gjcourt` |
| GitHub Actions app | id 15368, the `integration_id` the `lab` rule already uses | `gh api /apps/github-actions`; `scripts/github-rulesets.sh` |
| Workflows run on App PRs | Yes. toppingctl#34 (author `bench-cloud[bot]`) ran its CI | `gh pr checks 34 -R gjcourt/toppingctl` |
| homelab rulesets | `default-branch-guard` (id 24077121) and `renovate-branch-guard`. No required checks in either | `gh api repos/gjcourt/homelab/rulesets`, `.../rules/branches/master` |
| homelab classic branch protection | Also on `master`: required checks `yaml-lint` and `validate-kustomize` (app 15368), `strict: true`, 0 approvals, `dismiss_stale_reviews: true`, `enforce_admins: false`. Not managed by any script | `gh api repos/gjcourt/homelab/branches/master/protection` |
| Workflow token default | `default_workflow_permissions: read` | `gh api repos/gjcourt/homelab/actions/permissions/workflow` |
| `gh pr checks` and statuses | It lists commit statuses as well as check runs (statuses show with an empty `workflow`) | Checked on an open kubernetes/kubernetes PR, whose Prow statuses appear |
| Image builds on PRs | `build-agent-desk` and `build-plane-sync` run on `pull_request` but skip the push job (`if: github.event_name != 'pull_request'`). The other builds run on push to master only | `.github/workflows/build-*.yml` |
| Staging Flux identity | `apps-staging` sets no `serviceAccountName`, and kustomize-controller has no `--default-service-account`. So it applies as `kustomize-controller`, which `cluster-reconciler-flux-system` binds to **cluster-admin** | `clusters/melodic-muse/apps-staging.yaml`, `gotk-components.yaml`, live Deployment args |
| Network policy naming | 45 files named `networkpolicy*.yaml` under `apps/`. Also inline: two `CiliumNetworkPolicy` docs in `apps/staging/immich/cronjob-bootstrap.yaml`, and kustomize patches that target `CiliumNetworkPolicy` inside several `kustomization.yaml` files (e.g. `apps/staging/homeassistant/`) | `grep` over `apps/` |
| SOPS naming | 79 files under `apps/` carry a `sops:` key. All but one are `secret-*.yaml`; the exception is `apps/production/truenas-iscsi-monitor/secret.yaml` | `grep -l '^sops:'` |
| Cluster-scoped kinds in `apps/` today | 39 `Namespace`, 14 `PersistentVolume`, 4 `ClusterRole`, 5 `ClusterRoleBinding`, 1 `PriorityClass`, 1 `ValidatingAdmissionPolicy` and its binding (the last three are bench-cloud's own) | `grep` over `apps/` |
| Who pushes in a Job | agent-desk only creates Jobs. The push happens inside the Job: Claude runs `git push`, and git gets the App token from the system credential helper `github-app-token --git-credential` | `images/agent-desk/agent_desk/jobs.py`, `images/bench-cloud/Dockerfile`, `bench-task` |
| Playbooks with no `allowedRepos` | `docs-fix` and `design` apply to every mapped repo, so they reach homelab as soon as it is mapped | `apps/production/agent-desk/config.yaml`, `config.py` |
| Scratch repo | `ruleset-trial-scratch` no longer exists. `gjcourt/agent-sandbox` exists (private, default branch `main`, both rulesets) and is already the SANDBOX project's repo | `gh repo view`, `gh api .../rulesets` |

GitHub docs used below:

- [Events that trigger workflows][events]: `pull_request` and `pull_request_review` run with
  `GITHUB_REF` = `refs/pull/N/merge`, the PR's test merge commit. `pull_request_target` "runs in
  the context of the default branch of the base repository, rather than in the context of the
  merge commit". `pull_request` workflows "will not run ... if the pull request has a merge
  conflict"; `pull_request_target` runs anyway. `pull_request_review` fires on `submitted`,
  `edited` and `dismissed`.
- [Troubleshooting required status checks][troubleshoot]: job checks count only from `push`,
  `pull_request`, `pull_request_review`, `pull_request_target`, `deployment` and
  `deployment_status` runs. "Required checks must pass on the latest commit SHA". "If a check and a
  commit status have the same name, both must pass when that name is required". A required check
  that never reports leaves the PR "Waiting for status to be reported".
- [Available rules for rulesets][rules]: a required check can name the app that must set it. "If
  the status is set by any other person or integration, merging won't be allowed."
- [Choosing permissions for a GitHub App][app-perms]: an app needs the Workflows permission "to
  access or edit Actions files in the `.github/workflows` directory". GitHub refuses such a push
  without it ("refusing to allow a GitHub App to create or update workflow", [community
  #35410][refuse]).
- [Commit statuses][statuses]: creating one needs `statuses` write; the combined state uses "the
  latest status for all contexts"; limit 1000 statuses per SHA and context.

## Design

### Two statuses, not one

The gate posts two **commit statuses** on the PR's head SHA:

| Context | Bench PR | Any other PR | Counts for staging? |
| --- | --- | --- | --- |
| `bench-paths` | success only if every changed path passes the path rules | success ("not a bench PR") | **Yes.** A bench PR outside its paths stays out of staging |
| `bench-approval` | failure until George's latest review approves the current head SHA | success ("not a bench PR") | **No.** Staging ignores it, so decision 2 holds |

Both become required on homelab `master`.

**Why two.** `staging-deploy` merges a PR into `staging` only when no check is failing or pending,
and `gh pr checks` includes commit statuses (checked above). One combined failing status would
keep every unapproved bench PR out of staging, which breaks decision 2. So `staging-deploy` must
ignore the approval status. But it must not ignore the path status: a bench PR that touches
`clusters/**` or `infra/**` would otherwise reach the cluster through staging before George
reviews it.

This is a **one-line change to `staging-deploy.yaml`**. The brief expected none. The filter that
drops `sync-staging` also drops `bench-approval`:

```bash
FILTERED=$(echo "$CHECKS_JSON" | jq '[.[] | select(.name != "sync-staging" and .name != "bench-approval")]')
```

**Why statuses, not the job's own check run.** The gate runs on two events (a push and a review).
Each run of a job creates a new check run with the same name, and GitHub's docs don't say which
one a required check uses. A commit status is posted to an explicit SHA, and the latest one per
context is the one that counts. The job is named `evaluate`, never `bench-paths` or
`bench-approval`, so no check run shares a required name.

### What counts as a bench PR

A PR is a bench PR if either is true:

- its author is user id 334337744 (`bench-cloud[bot]`), or
- the bot has pushed to its head branch since the PR opened.

The second rule exists because the App can push to any branch except the default branch and
`renovate/**`, including George's PR branches. Who pushed is read from the repository activity
API (`GET /repos/{owner}/{repo}/activity?ref=<head ref>`), which records the actor for each push.
Commit author and committer fields are not used: the bot can write any name into them.
The trial confirms that the activity API reports App pushes with that actor (test T5).

### The approval rule

For a bench PR, `bench-approval` is success only when all of these hold:

1. The PR's head SHA is read fresh from the API at run time, not from the event payload.
2. From the PR's reviews (all pages), keep those by user id 356410 whose state is `APPROVED`,
   `CHANGES_REQUESTED` or `DISMISSED`. Comment-only reviews don't change the result.
3. The latest of those is `APPROVED`, and its `commit_id` equals the head SHA.

Any push gives a new head SHA, so the old approval no longer counts and the status turns red.
The classic protection's `dismiss_stale_reviews` also dismisses it.

### The path rules

The rules file is `scripts/bench-gate/rules.yaml`. One checker,
`scripts/bench-gate/check.py`, applies it. CI and the Job use the same script (see
[Defence in depth](#defence-in-depth-in-the-job)). For each changed file (both the old and new name of a rename):

- **Allowed** only if it matches `apps/**` or `docs/**`.
- **Denied**, whatever else matches, if any of these hold:
  - the path matches `apps/*/agent-desk/**`, `clusters/**`, `infra/**`, `.github/**`;
  - the path matches `**/secret*.yaml`. This is wider than `secret-*.yaml` so that
    `truenas-iscsi-monitor/secret.yaml` is covered;
  - the file, before or after the change, has a top-level `sops:` key;
  - the path matches `**/networkpolicy*.yaml`;
  - the file, before or after, declares a `kind:` ending in `NetworkPolicy`. This covers inline
    policies (immich's `cronjob-bootstrap.yaml`) and kustomize patches whose target names the kind;
  - an added or removed line mentions `networkpolicy` (any case). This covers adding or removing
    `networkpolicy.yaml` from a `resources:` list.
- **Fail closed.** If the PR has more than 3000 files (the API's limit), or a file's diff can't be
  read, or a binary file is changed, `bench-paths` fails.

Paths outside `apps/**` and `docs/**` (`scripts/`, `images/`, `hosts/`, root files such as
`AGENTS.md` and `renovate.json`) are denied because they are not allowed. That keeps
`scripts/github-rulesets.sh`, `scripts/bench-cloud/merge-audit.sh`, the bench-cloud image and its
managed settings out of reach.

### Triggers and which code runs

Workflow `.github/workflows/bench-gate.yaml`, job `evaluate`:

| Trigger | Types | Workflow file used ([events]) | Why |
| --- | --- | --- | --- |
| `pull_request_target` (base `master`) | `opened, synchronize, reopened, edited, ready_for_review` | Default branch's | Push events. Runs even when the PR conflicts. `edited` catches a base change to `master` |
| `pull_request_review` | `submitted, edited, dismissed` | The PR's test merge commit | The only event that fires on a review |
| `workflow_dispatch` | none | The ref George picks (`master`) | Backfill: evaluates every open PR. The App has `actions: read` only, so it can't dispatch |
| `schedule` | hourly | Default branch's | Safety net for a missed event |

`permissions: {statuses: write, pull-requests: read, contents: read}`.
`concurrency: bench-gate-<PR number>` with `cancel-in-progress: false`, so runs for one PR queue
and the newest state is written last.

**What keeps it tamper-proof:**

- **The workflow file.** Under `pull_request_review` the file comes from the PR's merge commit.
  The bot can't change it, because it lacks `workflows` and GitHub refuses its pushes to
  `.github/workflows/`. Under `pull_request_target` the file is master's anyway.
- **The code it runs.** Every run checks out **master** explicitly
  (`ref: ${{ github.event.repository.default_branch }}`, `persist-credentials: false`) and runs
  master's `check.py`. It never runs code from the PR. The PR is read as data only: the file list
  and reviews come from the API, and contents come from `git fetch origin pull/N/head` then
  `git show` / `git diff`. The plain `workflows` permission only protects `.github/workflows/`, so
  nothing the gate runs may live elsewhere in the PR (no local actions, no repo scripts from the
  PR).
- **Who can post the status.** The App has `statuses: read` and `checks: read`, so it can't post a
  status or a check run itself. The ruleset pins both contexts to `integration_id: 15368` (GitHub
  Actions). A same-name status from any other source, including George's PAT, doesn't satisfy it
  (test T3).
- **Other workflows.** A status from any workflow in the repo would carry the Actions identity.
  Only `bench-gate.yaml` may declare `statuses: write`. A lint step fails CI if any other workflow
  does. Workflows that run PR code (`lint`, `flux-test`) keep the repo's read-only default token.
- **Fail closed.** A conflicting PR gets no `pull_request_review` run. A missing status leaves the
  PR "Waiting for status to be reported", which blocks the merge.

**Answer to the brief's question:** `pull_request_target` runs are on GitHub's list of events whose
job checks can satisfy a required check ([troubleshoot]). This design doesn't depend on that,
because it posts statuses to the head SHA explicitly.

### Making it required on homelab only

`scripts/github-rulesets.sh` already has a per-repo hook, `extra_rules()`, used today for `lab`.
Add a `homelab` case:

```json
[{"type": "required_status_checks", "parameters": {
  "strict_required_status_checks_policy": false,
  "do_not_enforce_on_create": false,
  "required_status_checks": [
    {"context": "bench-paths", "integration_id": 15368},
    {"context": "bench-approval", "integration_id": 15368}]}}]
```

No other repo's body changes, and the daily CronJob keeps the rule in place and checks it for
drift. The classic branch protection stays as it is, and both sets of rules apply.
Only George's Admin role bypasses. The App can't, so it can't merge an unapproved homelab PR.
Option 2 still lets it merge a PR after George approves it. That is a timing issue only, and the
merge audit still pages if it happens.

A native rule doesn't fit. A ruleset `pull_request` rule with 1 approval would hold George's own
PRs too, and he can't approve his own. Rulesets can exempt actors but can't target one (the
script's header records this from the 2026-09-26 trial).

**Staging the ruleset change.** The CronJob fetches the script from master and runs `--apply`
daily at 05:17. A manual trial on a scratch repo would be undone by the next run, because `--apply`
PUTs the master body over it. So the trial goes through the script too:

1. **Trial repo first.** One PR adds an `agent-sandbox` case with the same two contexts. Before it
   merges, George runs the branch's script in dry-run mode. Expected: `needs-change=1`, and the one
   change is `WOULD-update agent-sandbox [default-branch-guard]`. Every other repo stays `ok`.
2. **homelab second**, in a later PR, only after the sandbox tests pass. The same dry-run check
   applies: only homelab changes.
3. After each merge, George runs `--apply --repo <repo>` and then `--check`, rather than waiting
   for 05:17. Rollback: revert the PR and run `--apply --repo <repo>`, which PUTs the plain body.

There is no need to suspend the CronJob, because each change is scoped to one repo by name. If
George would rather suspend it anyway, it is `spec.suspend: true` in
`infra/controllers/github-rulesets/cronjob.yaml`, in the same PR, with a follow-up PR to undo it.

### Defence in depth in the Job

agent-desk doesn't push, so the local check goes into the bench-cloud image, where the push
happens:

- `bench-task`, after the clone: if the target repo has `scripts/bench-gate/rules.yaml` on its
  default branch, copy it outside the clone. Then set
  `git -C repo config core.hooksPath /usr/local/share/bench-cloud/git-hooks`. Set it per clone,
  not system-wide, so other repos' own hooks still work.
- A `pre-push` hook runs the same `check.py` (copied into the image at build time, like
  `scripts/bench-cloud/job.yaml`) on `origin/<default>...<local sha>`. It refuses the push and
  prints which paths broke which rule.
- `managed-settings.json` also denies `git push --no-verify` and `git -c core.hooksPath`.
- agent-desk gets a small change: an optional `repoNotes` map in `config.yaml` (repo → text).
  `prompt.build` appends the note to the playbook prompt. For homelab, the note lists the path
  rules, so `docs-fix` (which allows any `*.md`) doesn't try `infra/**/README.md`.

These checks catch mistakes. They are not a security boundary: an agent can edit its own
environment. The server-side gate is the boundary.

### agent-desk config

In `apps/production/agent-desk/config.yaml`:

- `projects`: add `HOMELAB: gjcourt/homelab` and `HASS: gjcourt/homelab`.
- `denyRepos`: remove `gjcourt/homelab` (leave `denyRepos: []`).
- `feature.allowedRepos`: add `gjcourt/homelab`. `docs-fix` and `design` reach it without a change.
  `review` stays modemscope-only.
- `repoNotes`: the homelab note above.
- Header comment. Today it says "agents can't edit this repo, so nothing in it can be widened by an
  agent". New wording: "Agents may open homelab PRs, but this directory (`apps/*/agent-desk/**`)
  is outside their paths (`scripts/bench-gate/rules.yaml`), so nothing here can be widened by an
  agent."
- The `projects` comment ("Real repos join here one at a time ...") gains: homelab also needs
  the `bench-paths` and `bench-approval` required checks.

`apps/staging/agent-desk/config.yaml` stays as it is. It runs dry-run Jobs only, and its HOMELAB
mapping exists to test that `denyRepos` refuses a repo. Its header comment gets the same wording
fix.

**George, in Plane:** add the agent-desk bot (`gjcourt+agent-desk@gmail.com`) as a Member of
HOMELAB and HASS. This has to happen before the config change merges, or the bot can't read the
tickets.

## Rollout

Each step is one PR (or one action of George's). Each can be undone on its own. Later steps wait
for earlier tests to pass.

| # | Change | Test | Undo |
| --- | --- | --- | --- |
| 1 | **Gate, not required.** `scripts/bench-gate/` (rules, `check.py`, unit tests run in CI), `.github/workflows/bench-gate.yaml`, the `statuses: write` lint, and the `staging-deploy` filter line | Unit tests cover each rule, including renames, `sops:`, inline and patched NetworkPolicy, and the approval rule on fixture reviews. After merge, George runs `workflow_dispatch`. Every open PR, Renovate's included, gets `bench-paths` and `bench-approval` success. staging-deploy still merges them | Revert the PR |
| 2 | **agent-sandbox gets the gate** (agent-sandbox PR: the same workflow, with a `rules.yaml` laid out like homelab's), then a **homelab PR adding the `agent-sandbox` `extra_rules` case** | Dry run before merge: `needs-change=1`, agent-sandbox only. After `--apply --repo agent-sandbox`: `--check` ok. Then tests T0 to T6 below, using SANDBOX tickets | Revert both PRs; `--apply --repo agent-sandbox` |
| 3 | **Image and agent-desk changes**: pre-push hook, `bench-task` rules copy, managed-settings denies, `repoNotes`. Bump both image tags | `bench-cloud run --repo agent-sandbox "edit infra/x"`: the push is refused locally with the rule named. A docs-only task pushes normally | Revert; pin the previous tags |
| 4 | **homelab `extra_rules` case** | Dry run before merge: `needs-change=1`, homelab only. After `--apply --repo homelab` and `--check`: an open George PR and a Renovate PR stay mergeable, and the next Renovate automerge goes through | Revert; `--apply --repo homelab` |
| 5 | **George: Plane membership** for HOMELAB and HASS | The bot's token can list HOMELAB and HASS issues. That is the same check as agent-desk plan step 2 | Remove the bot as a Member |
| 6 | **Config change**: map HOMELAB and HASS, empty `denyRepos`, `feature.allowedRepos`, comments | T7 and T8 below | Put `gjcourt/homelab` back in `denyRepos` (one line) |
| 7 | **Docs**: bench-cloud runbook (access model, monitoring), agent-desk plan (the "until bench-watch" rows), `docs/STATUS.md` | Lint | Revert |

If George takes up the staging mitigation in the [notes](#orchestrator-notes-not-georges-decisions),
it goes between steps 4 and 6.

**Tests on agent-sandbox (step 2):**

- **T0.** From a console shell, the App pushes a branch that adds `.github/workflows/x.yaml`.
  GitHub refuses it. This re-checks the missing `workflows` permission end to end.
- **T1.** A bot PR touching `infra/x`: `bench-paths` is failure. The App's merge attempt
  (`gh api -X PUT .../pulls/N/merge` run by George from a console shell with the App token) is
  refused.
- **T2.** A bot PR touching `docs/x`: `bench-paths` is success and `bench-approval` is failure.
  The App's merge is refused. George approves, and `bench-approval` turns success. The bot pushes
  again, and it turns failure on the new SHA. George approves again, then a plain `gh pr merge`
  (no `--admin`) succeeds.
- **T3.** On an unapproved bot PR, George posts a `bench-approval` success status with his own PAT.
  The App's merge is still refused, because of the integration pin.
- **T4.** A George-authored PR: both statuses are success within a minute, and nothing else
  changes.
- **T5.** The bot pushes a commit to a George-authored branch with an open PR. `bench-approval`
  turns failure, and stays failure after George comments on the PR. This also confirms that the
  activity API shows the bot as the pusher.
- **T6.** Bot PRs that add a `sops:` file, edit a `networkpolicy.yaml`, or drop it from
  `resources:`. Each gets `bench-paths` failure.

**Tests on homelab (step 6):**

- **T7.** A HOMELAB `docs-fix` ticket opens a PR. `bench-paths` is success and `bench-approval`
  is failure. The sync-staging log shows "Successfully merged PR #N". George approves and merges.
- **T8.** A HASS ticket that needs a change outside the paths (for example under `infra/`). The
  agent's push is refused locally, or, if it gets through, the PR gets `bench-paths` failure and
  stays out of staging.

## Orchestrator notes (not George's decisions)

These are risks that remain after this plan. They are the orchestrator's reading, for George to
weigh. None of them reopens decisions 1 to 3.

### 1. Staging applies unreviewed bench PRs as cluster-admin

`apps-staging` applies as `kustomize-controller`, which is bound to cluster-admin (checked above).
The staging cluster is the production cluster. Under decision 2, a bench PR that passes
`bench-paths` reaches `staging` within about five minutes, before George reads it. `apps/**`
includes `apps/staging/**` and `apps/base/**`. A new directory there, listed in
`apps/staging/kustomization.yaml`, can declare any kind in any namespace. Examples:

- a `ClusterRoleBinding` that gives `bench-agent` cluster-admin;
- a `Deployment` in `flux-system` that mounts `sops-agekey`. That key decrypts every SOPS file in
  this public repo;
- objects in a production namespace, or cluster-scoped kinds: `Namespace`, CRDs, webhooks,
  `ValidatingAdmissionPolicy`, `PriorityClass`, `PersistentVolume`.

Options:

- **(a) A kind and namespace rule in `bench-paths`, for bench PRs only.** Deny changed YAML that
  declares a cluster-scoped kind (the list above, plus `ClusterRole`, `StorageClass`,
  `CiliumClusterwideNetworkPolicy`, `APIService`) or names a namespace outside the app's own.
  The cheap version checks the files statically. A stronger version renders
  `kustomize build apps/staging` and `apps/production` at base and head and diffs the objects.
  That needs care, because kustomize then reads PR content (no exec plugins, no remote bases).
  Some bench work, like adding a new app with its own `Namespace`, would then need George to do
  that part himself.
- **(b) A restricted ServiceAccount for `apps-staging`.** Give the staging Kustomization a
  `serviceAccountName` bound only to the `*-stage` namespaces. That fixes the cause for every
  author, not just the bot. It is a larger change (`clusters/**`), and today's staging apps may
  need cluster-scoped objects of their own.

The orchestrator's view: do (a) before step 6, and plan (b) separately.

### 2. bench-cloud's own manifests are inside `apps/**`

`apps/base/bench-cloud/` and `apps/production/bench-cloud/` hold the agent's sandbox: its RBAC, the
`bench-cloud-task-jobs` admission policy, its network policy and its quota. George denied
`apps/*/agent-desk/**` because agents shouldn't edit their own rules. The same reasoning covers
`apps/*/bench-cloud/**`. The network policy is already denied; the RBAC, admission policy and
quota are not. The orchestrator's view: add `apps/*/bench-cloud/**` to the denylist.

### 3. bench-watch

The merge audit (`infra/controllers/bench-merge-audit`) and its script run from homelab master,
which the App can now open PRs against. The required checks close the path that made bench-watch
urgent: the App can't merge a homelab PR without George's approval of its head, and
`infra/**` and `scripts/**` are outside its paths. bench-watch is still the only fix for a quietly
edited audit, and the only check that doesn't live where the App can write. It stays deferred.
The agent-desk plan says homelab stays out of scope "until bench-watch exists". This plan
replaces that condition with the gate. Step 7 updates that text once George confirms.

### 4. Smaller items

- **Strict up-to-date.** The classic protection has `strict: true`. Updating a bench PR's branch
  makes a new head SHA, so George approves after the update, not before.
- **Approval after the fact.** Option 2 still lets the App merge a PR once George has approved it.
  The audit still pages if that happens. The content was reviewed, so only the timing is lost.
- **CI runs PR code.** `lint` and `flux-test` run scripts from the PR's checkout, with the
  read-only default token. A bench PR that edits those scripts also fails `bench-paths`
  (`scripts/**`), so it never reaches staging or master.

## Open questions for George

1. **Staging blast radius** (note 1): fix it before homelab is enabled (step 6), with the kind and
   namespace rule, or accept it for now?
2. **Add `apps/*/bench-cloud/**` to the denylist** (note 2)?
3. **bench-watch** (note 3): does HOMELAB-85 replace the 2026-10-18 condition "homelab out of
   scope until bench-watch exists"?

[events]: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
[troubleshoot]: https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks
[rules]: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets
[app-perms]: https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app
[refuse]: https://github.com/orgs/community/discussions/35410
[statuses]: https://docs.github.com/en/rest/commits/statuses
