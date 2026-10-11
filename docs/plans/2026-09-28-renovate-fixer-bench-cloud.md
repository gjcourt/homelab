---
status: planned
last_modified: 2026-10-08
summary: "Bench-cloud fixes stuck Renovate PRs; mechanical fixes auto-merge, risky go to George; renovate-review kept on subscription billing"
---

# Renovate fixer on bench-cloud

## Goal

Renovate PRs that can't merge — conflicts, a failing `go mod tidy`, a stale
lockfile, a small syntax break — sit until George fixes them by hand. Let a
bench-cloud agent fix them, and let **mechanical** fixes merge without him,
while **majors and truly risky changes still get a human**.

## Decisions (George, 2026-09-28)

| # | Question | Decision |
|---|---|---|
| 1 | Existing `renovate-review` (two-call risk reviewer, `held-by-review`) | **Retire it entirely.** Superseded 2026-10-08: George chose to keep it and move its model calls to the Claude subscription (`claude -p`) instead of an API key. See "Retiring renovate-review". |
| 2 | Who starts fixes | `renovate-automerge` kicks off the task and picks up its result. |
| 3 | What merges without George | Mechanical fixes (rebase / conflict, dependency-file fixes like `go mod tidy`) on patch/minor. **Majors and risky changes: a human reviews.** |

## Today

- `renovate` (daily) opens PRs from branches `renovate/…`, **as George**:
  self-hosted, authenticating with `RENOVATE_TOKEN`, George's PAT (the same
  token `renovate-automerge`, `github-rulesets` and `bench-merge-audit` use).
  Commits carry `RENOVATE_GIT_AUTHOR` (`Renovate Bot <bot@renovateapp.com>`) as
  their git author, but the pushes and the PRs are George's.
- `renovate-automerge` (every 6 h, George's token) merges PRs it classifies as
  patch/minor/digest/pin from the **PR body's** update table (title as a
  fallback), holds runtimes, majors, unparseable tables and `held-by-review`.
  It calls update-branch only when all three merge methods return 405 (i.e.
  after CI already looks green) — never for a conflicting PR.
- `is_renovate_pr()` treats a PR as Renovate's if **any** of: head branch starts
  `renovate/`, **or** title starts `chore(deps):`, **or** the author login
  contains `renovate`. It does not check that the head is in the same repo,
  nor who authored the PR.
- `ci_passing()` counts only **completed** check runs, and returns true when
  there are no check runs and the combined status is `pending` (which is what
  GitHub reports for a commit with no statuses at all).
- `renovate-review` (daily) runs in `REVIEW_MODE: shadow`: it calls the API and
  posts nothing. No open PR in the ten watched repos carries `held-by-review`
  (checked 2026-09-28). Nothing fixes a stuck PR.

## ⚠️ Prerequisite: existing holes

`renovate-automerge` merges with George's token, so anything that makes it
classify a PR as a safe Renovate PR is a merge path that the hourly
`bench-merge-audit` never sees (the merger is George, not `bench-cloud[bot]`).
Four such paths exist today:

1. **App creates or pushes to a `renovate/` branch.** The `bench-cloud` App
   (Contents RW) can create `renovate/anything`, or add commits to a real
   Renovate branch, and open or ride a PR whose body says "patch".
2. **App edits a real Renovate PR.** With Pull requests RW the App can rewrite
   the body of a genuine Renovate *major* PR so its table reads `patch`, or
   edit its title; the classifier reads exactly those. The branch ruleset does
   not touch this.
3. **App removes `held-by-review`** (Issues RW). Moot while nothing is labelled,
   but the plan keeps honouring the label.
4. **Anyone, via a fork (public repos).** Nine of the ten watched repos are
   public. A fork PR's `head.ref` is the fork's branch name, so a stranger's
   PR from a fork branch named `renovate/x` — or with any branch and a title
   starting `chore(deps):` — is classed as Renovate's. Its body is theirs to
   write. Whether it then merges depends on `ci_passing()` (see above: no check
   runs + no statuses reads as passing; a returning contributor's fork CI runs
   workflow files from the PR itself) and on whether George's token, as admin
   with `enforce_admins: false` on every protected default branch, merges past
   required checks. **Unverified end to end** — test on a scratch repo before
   relying on either answer; the classifier half is certain from the code.

**Fix (PR 1, first):**

- **Identify Renovate PRs strictly:** head branch starts `renovate/` **and**
  `head.repo.full_name == repo` **and** the PR author is the token owner
  (`gjcourt`). Drop the title and author-substring fallbacks.
- **Refuse edited PRs:** read GraphQL `userContentEdits` (body) and
  `RenamedTitleEvent` (title) and hold unless every editor is the token owner.
  Allowlist, not denylist: GraphQL reports a bot's login **without** the
  `[bot]` suffix, so a check for `bench-cloud[bot]` would never match. Hold a
  PR whose `held-by-review` was removed by anyone other than George
  (`UnlabeledEvent.actor`), or drop the label from the classifier entirely
  since nothing applies it.
- **Tighten `ci_passing()`:** at least one check run, none pending, all
  `success`; no "no checks = pass". Merge with the `sha` parameter set to the
  head that was evaluated, so a push between check and merge fails the merge.
- **`renovate-branch-guard` ruleset** on `refs/heads/renovate/**` in every
  repo — creation, update, deletion and force-push restricted, bypass the Admin
  role (`always`). Renovate's pushes/force-pushes/deletes and automerge's
  update-branch all act as George, so they bypass; the App can no longer
  create or touch a `renovate/` branch. `scripts/github-rulesets.sh` manages a
  single ruleset today (`NAME`, one `BODY`); generalise it to a list so the
  daily `--apply`/`--check` covers both. Its in-cluster `--check` runs with
  `RENOVATE_TOKEN` and asserts `current_user_can_bypass=always`, which is the
  right proof that *Renovate's* token (not just `gh` on the Mac) bypasses.
- Not "skip PRs containing a commit by `bench-cloud[bot]`": git author and
  committer are free text the pusher sets, so that signal is forgeable. The PR
  author field and the ruleset are what hold.

Trial on a scratch repo first, exactly as for `default-branch-guard`: Renovate
(with `RENOVATE_TOKEN`) pushes, force-pushes and deletes a `renovate/` branch;
the App's create is refused; a fork PR and an App-edited body are both held.

## Design (PR 2)

### Which PRs, and what happens

| Renovate PR state | Agent's job | Merge |
|---|---|---|
| patch/minor/digest, **conflicting** (`mergeable: false`) | rebase / resolve in a new PR off the default branch | **automerge**, if the gates below pass |
| patch/minor/digest, **CI failing on dependency files** (`go mod tidy`, lockfile regen) | same bump + the mechanical fix, new PR, supersede Renovate's | **automerge**, if the gates pass |
| patch/minor/digest, fix needs **source-code changes** (API rename, real syntax change) | fix it, read the release notes, explain in the PR | **George** |
| **major**, runtime/interpreter, anything automerge already holds | none — not launched | **George** |

The agent never pushes to a `renovate/` branch (Renovate rebases over outside
commits — and PR 1 forbids it anyway). It opens `bench-fix/<task-id>` off the
default branch. **Automerge**, not the agent, closes the Renovate PR as
superseded — after it has merged the fix PR.

### Launch and "wait"

`renovate-automerge` gains one step per run: for each Renovate PR it classifies
patch/minor/digest that is **conflicting** (GitHub reports `mergeable: false`;
update-branch returns 422 there, so today's 405 path never reaches it) or has
a **completed, failed** required check, and has no live fix task:

1. `bench-cloud run --repo <repo> --deadline 2h "<prompt>"` — prompt names the
   Renovate PR, the failing check and log excerpt, the tiers above, the branch
   name to use, and "if it isn't mechanical, say so and stop".
2. Record `{task id, repo, renovate PR, renovate head SHA, classification,
   launched_at}` in a ConfigMap `renovate-fixer-state` in `renovate` —
   **written only by automerge**. Task pods run as `bench-agent` (cluster-wide
   `view`, so they can *read* it) and the console as `bench-console` (Jobs in
   `bench-cloud` only); neither can write it.

Next runs (≤ 6 h later) read the state: task still running → wait; task done →
evaluate its PR (below); no PR after 24 h → one retry, then give up and leave
the Renovate PR for George with a comment. At most 2 tasks per run, so fixes
never crowd out George's own use of the 5-task quota.

### Auto-merge gates for an agent's fix PR

All must hold, else it's left for George with a comment saying which failed.
Everything the App does is the App — any bench-cloud task, not only the fixer,
acts as `bench-cloud[bot]`, and the fixer itself reads untrusted release notes
— so the gates that carry the security weight are the ones computed from the
**diff**, not from who opened the PR:

1. **Bookkeeping:** the PR is authored by `bench-cloud[bot]`, head in the same
   repo, branch `bench-fix/<task-id>`, and that task id is in
   `renovate-fixer-state`. This ties the PR to a launch; it is **not**
   provenance — task ids (`bt-YYYYMMDD-<6 hex>`) are Job names every task can
   list via `view`, so any concurrent task could open that branch.
2. **Classification:** taken from the state record, computed by automerge at
   launch, from a Renovate PR that passed PR 1's strict identification and
   edit check at that moment.
3. **Scope, as diff equality:** every file the fix touches is one the Renovate
   PR touches or a lock/sum file; for each non-lock file the fix's change is
   **identical** to Renovate's change to that file (not merely "same target
   version" — `package.json` scripts, a `go.mod` `replace` or `toolchain`
   line would pass a version comparison). No workflow, CI or source files.
   **Lock files are code:** a `package-lock.json`/`yarn.lock`/`pnpm-lock.yaml`
   `resolved` URL, a `uv.lock`/`poetry.lock` source URL or a `Cargo.lock` git
   source can point at any tarball. `go.sum` is hash-only and checked against
   the checksum database, so it is safe to accept; other lock files go to
   George unless every changed source resolves to the ecosystem's default
   registry.
4. **CI green** under PR 1's strict `ci_passing()` on the head SHA, merged
   with that `sha` so a later push by any task voids the merge.

A fix that fails gate 3 because it touched source code is the "George
reviews" tier by construction — the agent can still do the work; it just
can't land it.

### Retiring renovate-review

> **Superseded 2026-10-08.** George chose to keep `renovate-review`, with its
> calls billed to a Claude subscription token through `claude -p` instead of an
> Anthropic API key. Nothing below in this section is to be done.

Remove `infra/controllers/renovate-review/` (CronJob, script, ConfigMap,
encrypted Anthropic-key secret and `secret.yaml.example`) and its entry in
`infra/controllers/kustomization.yaml`; it has no homelabscope rule. It has
only ever run in shadow mode, so nothing it does today is lost at merge time;
what is given up is the planned independent release-notes check on *green*
patch/minor PRs — the viem-style "patch that's actually breaking" case. The
fix task reads release notes for the PRs it touches; green ones keep merging
on the declared type, as they do today. **Accepted by George (decision 1).**

### Plumbing

- `renovate-automerge` moves to the bench-cloud image (Python, `bench-cloud`,
  kubectl) and a ServiceAccount `renovate-automerge` bound to a Role in
  `bench-cloud` (create/get/list/watch Jobs, get Deployments for the image
  tag) and a Role in `renovate` (create/get/update the state ConfigMap). The
  `bench-cloud-task-jobs` admission policy still applies to everything it
  launches (bench-agent, task PriorityClass, 5-pod quota).
- `renovate-fixer-state` must **not** be a Flux-managed manifest — Flux would
  revert the job's writes on every reconcile. Automerge creates it on first
  run.
- Egress: `renovate` has no NetworkPolicy today; nothing to add.

## Risks

- **Agent code reaching master without George**, via gate failure. Mitigated
  by gates 3–4 being computed by automerge from the diff and CI, and by the
  auto tier being dependency-file-only.
- **The `bench-merge-audit`** stays as it is (it flags merges *by the App*;
  these merges are by automerge/George's token — which is also why PR 1's
  holes matter: they launder an App-driven merge past the audit).
- **Subscription usage** — capped at 2 fix tasks per 6 h run.
- **Renovate reacting to a closed PR** — closing as superseded makes Renovate
  ignore that update; if the fix PR is later abandoned the update is lost until
  the next version. Automerge closes it only after the fix has merged. The App
  can also close any Renovate PR at any time (Pull requests RW), silently
  suppressing an update; an audit of Renovate-PR closes by the App is cheap to
  add to `bench-merge-audit`.

## Phases

1. **PR 1 — close the holes**: strict Renovate-PR identification, edit/label
   checks, strict CI + `sha`-pinned merge in `renovate-automerge`;
   `renovate-branch-guard` via a generalised `github-rulesets.sh`. Trial on a
   scratch repo, then George applies to all repos.
2. **PR 2 — the fixer**: launch step, state ConfigMap, gates, plumbing, retire
   `renovate-review`. Exercise on a real stuck PR (or a staged one in a
   scratch repo) before relying on it.
