---
status: planned
last_modified: 2026-09-28
summary: "Stuck Renovate PRs fixed by bench-cloud tasks; mechanical fixes auto-merge, risky ones go to George; renovate-review retired"
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
| 1 | Existing `renovate-review` (two-call risk reviewer, `held-by-review`) | **Retire it entirely.** |
| 2 | Who starts fixes | `renovate-automerge` kicks off the task and picks up its result. |
| 3 | What merges without George | Mechanical fixes (rebase / conflict, dependency-file fixes like `go mod tidy`) on patch/minor. **Majors and risky changes: a human reviews.** |

## Today

- `renovate` (daily) opens PRs from branches `renovate/…`, **as George** (self-hosted, his PAT).
- `renovate-automerge` (every 6 h) merges patch/minor/digest PRs whose CI is
  green, holds runtimes and majors, and clears `BEHIND` with update-branch.
  It recognises Renovate PRs **by the `renovate/` branch prefix only** — the
  author is George, so the author can't be used.
- `renovate-review` (daily) labels risky PRs `held-by-review`. Nothing fixes a
  stuck PR.

## ⚠️ Prerequisite: an existing hole

Since bench-cloud went live, the `bench-cloud` GitHub App can create any
branch — including `renovate/anything` — and write any PR body, including a
Renovate-style update table saying "patch". `renovate-automerge` would merge
it with George's token. That is an auto-merge path around the agent's
never-merge rule, open today.

**Fix (PR 1, first):** a second ruleset, `renovate-branch-guard`, on
`refs/heads/renovate/**` in every repo — creation, update, deletion and
force-push restricted to the Admin role. Renovate and `renovate-automerge` act
as George (Admin, bypass `always`), so they're unaffected; the App can no
longer create or touch a `renovate/` branch. Plus, in `renovate-automerge`:
skip any PR authored by, or containing a commit by, `bench-cloud[bot]`.
Trial on a scratch repo first (Renovate-as-George pushes and force-pushes; the
App's create is refused), exactly as for `default-branch-guard`.

## Design (PR 2)

### Which PRs, and what happens

| Renovate PR state | Agent's job | Merge |
|---|---|---|
| patch/minor/digest, **conflicting** after update-branch | rebase / resolve in a new PR off the default branch | **automerge**, if the gates below pass |
| patch/minor/digest, **CI failing on dependency files** (`go mod tidy`, lockfile regen) | same bump + the mechanical fix, new PR, supersede Renovate's | **automerge**, if the gates pass |
| patch/minor/digest, fix needs **source-code changes** (API rename, real syntax change) | fix it, read the release notes, explain in the PR | **George** |
| **major**, runtime/interpreter, anything automerge already holds | none — not launched | **George** |

The agent never pushes to a `renovate/` branch (Renovate rebases over outside
commits — and PR 1 forbids it anyway). It opens `bench-fix/<task-id>` off the
default branch and closes the Renovate PR as superseded **only after its own
PR is green**.

### Launch and "wait"

`renovate-automerge` gains one step per run: for each patch/minor Renovate PR
that is **not mergeable after update-branch** (conflict, or a failing required
check that isn't pending), and has no live fix task:

1. `bench-cloud run --repo <repo> --deadline 2h "<prompt>"` — prompt names the
   Renovate PR, the failing check and log excerpt, the tiers above, and "if it
   isn't mechanical, say so and stop".
2. Record `{task id, repo, renovate PR, classification, launched_at}` in a
   ConfigMap `renovate-fixer-state` in `renovate` — **written only by
   automerge** (the App has no cluster write access).

Next runs (≤ 6 h later) read the state: task still running → wait; task done →
evaluate its PR (below); no PR after 24 h → one retry, then give up and leave
the Renovate PR for George with a comment. At most 2 tasks per run, so fixes
never crowd out George's own use of the 5-task quota.

### Auto-merge gates for an agent's fix PR — none forgeable by the App

All must hold, else it's left for George with a comment saying which failed:

1. **Provenance:** the PR's branch is `bench-fix/<task-id>` and that task id is
   in `renovate-fixer-state` (automerge-written, cluster-only).
2. **Classification:** taken from the state record, computed by automerge at
   launch — and the Renovate PR's body must never have been edited by
   `bench-cloud[bot]` (GraphQL `userContentEdits`); else George.
3. **Scope:** files changed ⊆ the Renovate PR's files ∪ lock/sum files
   (`go.sum`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `uv.lock`,
   `poetry.lock`, `Cargo.lock`); the version bumps equal Renovate's
   (same package → same target version); no workflow, CI or source files.
4. **CI green** on the fix PR, same required checks as any PR.

A fix that fails gate 3 because it touched source code is the "George
reviews" tier by construction — the agent can still do the work; it just
can't land it.

### Retiring renovate-review

Remove `infra/controllers/renovate-review/` (CronJob, script, ConfigMap,
encrypted Anthropic-key secret) and its homelabscope/Flux references.
`renovate-automerge` keeps honouring an existing `held-by-review` label so
anything held today stays held until George clears it. What's lost: the
independent release-notes check on *green* patch/minor PRs — the viem-style
"patch that's actually breaking" case. The fix task reads release notes for
the PRs it touches; green ones merge on the declared type as they did before
the reviewer existed. **Accepted by George (decision 1).**

### Plumbing

- `renovate-automerge` moves to the bench-cloud image (Python, `bench-cloud`,
  kubectl) and a ServiceAccount `renovate-automerge` bound to a Role in
  `bench-cloud` (create/get/list/watch Jobs, get Deployments for the image
  tag) and a Role in `renovate` (get/update the state ConfigMap). The
  `bench-cloud-task-jobs` admission policy still applies to everything it
  launches (bench-agent, task PriorityClass, 5-pod quota).
- Egress: `renovate` has no NetworkPolicy today; nothing to add.

## Risks

- **Agent code reaching master without George**, via gate failure. Mitigated
  by gates 1–4 being checked by automerge from data the App can't write; the
  auto tier is dependency-file-only.
- **The `bench-merge-audit`** stays as it is (it flags merges *by the App*;
  these merges are by automerge/George's token, which is the point).
- **Subscription usage** — capped at 2 fix tasks per 6 h run.
- **Renovate reacting to a closed PR** — closing as superseded makes Renovate
  ignore that update; if the fix PR is later abandoned the update is lost until
  the next version. The agent only closes after its own PR is green.

## Phases

1. **PR 1 — `renovate-branch-guard` ruleset** + automerge skips App-authored
   PRs/commits. Trial on a scratch repo, then George applies to all repos.
2. **PR 2 — the fixer**: launch step, state ConfigMap, gates, plumbing, retire
   `renovate-review`. Exercise on a real stuck PR (or a staged one in a
   scratch repo) before relying on it.
