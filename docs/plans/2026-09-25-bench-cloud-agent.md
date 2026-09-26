---
status: planned
last_modified: 2026-09-26
summary: "bench-cloud: up to 5 parallel Claude Code agents in the cluster on George's subscription — write/push code, run tests, reach the internet, move bits to hestia"
---

# bench-cloud — a private cloud coding agent

## Goal

A cloud version of the `bench` workbench agent: runs in the homelab, keeps working
while the laptop is closed, and can

- **write and push code** — branches and PRs on any of George's repos,
- **run tests** — the toolchains those repos use,
- **reach the internet** — docs, packages, APIs,
- **move bits to hestia** — into a dedicated inbox, read media,
- **read the cluster** — to debug, not to change it,

with **up to 5 agents working in parallel** by default (ceiling 10).

## Why Claude Code (not pi)

It runs on George's Claude subscription, and Anthropic's terms scope subscription
OAuth to "ordinary use of Claude Code and other native Anthropic applications"
([legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)).
pi was evaluated first: its subscription path works only by impersonating Claude
Code (system prompt, `claude-cli` user-agent, Claude Code beta flags and tool
names — `@earendil-works/pi-ai` 0.87.1, `dist/api/anthropic-messages.js`), which
circumvents that restriction. Rejected. pi stays an option on API-key billing
through llmux (llmux gateway plan, phase 2).

## Decisions (George, 2026-09-25)

| # | Question | Decision |
|---|---|---|
| 1 | hestia scope | Read-write to a new `agent-inbox` dataset; read-only to media. Nothing else. |
| 2 | Cluster access | Read-only to start. |
| 3 | GitHub scope | All of George's repos. |
| 4 | Parallelism | Multiple agents. Cap **5** concurrent (George rarely runs more), raisable to a hard ceiling of **10**. |

## Design

### Shape

- **Namespace `bench-cloud`.** Everything below lives here.
- **Tasks are Kubernetes Jobs.** One task, one pod, one fresh `git clone`, running
  `claude -p "<task>"`. Fresh checkouts mean parallel agents never share a working tree.
- **The cap is enforced by the platform**, not by convention: a `ResourceQuota`
  of 5 task pods (plus one console pod). A 6th task waits in the Job queue. Raising
  it is a one-line change, up to the agreed ceiling of 10.
- **One console pod** (`tmux` + Claude Code) for hands-on sessions: attach from the
  Mac, detach, come back later. Remote Control (phone / claude.ai) needs a
  full-scope `claude auth login` session, so only the console gets one — see
  *Test results*.
- **Submitting a task** from the Mac: a small `bench-cloud run --repo <repo> "<task>"`
  wrapper that creates a Job from a template. Later: a GitHub label trigger.
- **Results** come back as PRs. Each Job's transcript and summary are rsynced to
  `hestia:agent-inbox/runs/<date>-<job>/` before the pod exits, so the record
  outlives the pod.

### Capabilities, each deliberately narrow

| Capability | Mechanism | Boundary |
|---|---|---|
| Claude | Task Jobs: `claude setup-token` token as `CLAUDE_CODE_OAUTH_TOKEN` from a SOPS secret (verified 2026-09-26). Console: an interactive `claude auth login` kept on its PVC | Jobs get an inference-only token: no refresh races, and no Remote Control. The full-scope login lives in one pod only |
| Code | A **GitHub App** (`bench-cloud`) installed on all of George's repos: **Contents RW, Pull requests RW, Issues RW, Actions R, Commit statuses R**; no Administration, **no Workflows**. Installation tokens (1 h) minted in-pod from the app key | Acts as `bench-cloud[bot]`, not as George. A **default-branch ruleset with George as the only bypass actor** means it can push branches and open PRs but **can't merge or push the default branch**. **Can't change CI**, so it can't widen its own permissions. |
| Tests | Toolchains in the image (Go, Node, Python, make, gcc) | No container builds in v1 (needs privileges); images still build in CI |
| Internet | Egress 80/443 to `world` | No inbound; no Gateway route |
| hestia | SSH as a new **`bench-agent`** user, key restricted with `rrsync`: RW under `agent-inbox`, RO media | Not `truenas_admin`; no sudo; no shell; no other datasets |
| Cluster | ServiceAccount bound to the built-in **`view`** ClusterRole | Read-only; `view` excludes Secrets |

### Guardrails

- Runs as non-root with a read-only root filesystem apart from the workspace;
  CPU and memory limits per pod.
- Network policy: DNS, `world` 80/443, hestia 22, the API server — nothing else on
  the LAN.
- The agent's `CLAUDE.md` carries George's rules: branch + PR only, never push the
  default branch, never bypass branch protection, SOPS is operator-only. Claude
  Code's permission settings also deny `gh pr merge` and pushes to `main`/`master`
  — belt and braces behind branch protection.
- Every secret is operator-created and SOPS-encrypted. Each is revocable on its own.
- Transcripts on hestia are the audit trail.

### Image

A new image (Debian + Node for Claude Code, Go, Python, git, gh, rsync, kubectl,
tmux), built by GitHub Actions with date-sha tags like the other images. Claude
Code's version is pinned and bumped by Renovate.

## Risks

- **Subscription limits.** Five agents in parallel consume the plan's usage limits
  roughly five times as fast as one, and Anthropic describes the plans' limits as
  assuming ordinary, individual use. The cap is a single number to lower; phase 2
  records per-task usage from Claude Code's JSON output so the actual burn is
  visible.
- **An agent with push rights.** A personal token would act *as George*, and
  George merges his own PRs without review — so branch protection could not stop
  the agent merging its own work. Hence a separate App identity plus a ruleset it
  can't bypass. A repo missing the ruleset is a repo the agent can push `master`
  on — phase 1 applies it everywhere by script and CI-checks for drift.
- **hestia exposure.** The `bench-agent` user is new attack surface on the NAS.
  `rrsync` plus a dataset of its own keep a mistake inside `agent-inbox`.

## Phases (one PR each unless noted)

1. **Foundations.** Image and its workflow (new repo); `bench-cloud` namespace,
   netpol, quota, `view` binding; the console pod; secrets (operator); hestia
   `bench-agent` user and `agent-inbox` dataset (TrueNAS, operator-assisted);
   the GitHub App and the default-branch ruleset on every repo. Verify: `claude auth login` inside
   the console pod and Remote Control from it. **Exit:** from the console, Claude
   Code clones a repo, runs its tests, opens a PR, and rsyncs a file to
   `agent-inbox`.
2. **Task runner.** The Job template, `bench-cloud run`, the transcript upload,
   per-task usage capture, and the quota of 5. **Exit:** five tasks submitted at
   once produce five PRs; a sixth waits.
3. **Triggers and visibility.** A GitHub label trigger (issue labelled `bench` →
   Job); a Grafana panel for running tasks, outcomes and usage.

## Test results (2026-09-26, throwaway `bench-cloud-test` namespace)

A restricted-PSA, non-root pod (`node:22-bookworm`, Claude Code 2.1.283) with the
`setup-token` token in `CLAUDE_CODE_OAUTH_TOKEN`:

- **Headless works.** `claude -p` answered in 1.3 s (default model
  `claude-sonnet-5`); npm install and the API both reached over plain egress.
- **Remote Control does not work with that token.** Verbatim: *"Remote Control
  requires a full-scope login token. Long-lived tokens (from `claude setup-token`
  or CLAUDE_CODE_OAUTH_TOKEN) are limited to inference-only for security reasons.
  Run `claude auth login` to use Remote Control."* Hence the split above: the
  inference-only token for Jobs, a full-scope login for the console only.
- **Remote Control works with a full-scope login.** `claude auth login` via
  `kubectl exec -it` completed (URL opened on the Mac); credentials land in
  `~/.claude/.credentials.json` (0600). `claude remote-control` then connected
  and George drove the pod from claude.ai/code. Three requirements for the
  console, all found in the test:
  1. **No `CLAUDE_CODE_OAUTH_TOKEN` in the console's env.** It takes precedence
     over the stored login (`auth status` reports `oauth_token`) and Remote
     Control refuses it. Jobs get the variable; the console must not.
  2. **Workspace trust.** Remote Control exits with "Workspace not trusted" until
     the directory is trusted — the image or an init step sets
     `projects["<workspace>"].hasTrustDialogAccepted` in `~/.claude.json`.
  3. **A one-time "Enable Remote Control? (y/n)" consent**, answered by George
     interactively — not scripted. Run it under tmux so it outlives the
     `kubectl exec`.

## Open questions (resolved in phase 1)

- Whether the full-scope login and the Remote Control consent survive pod
  restarts when `~/.claude` and `~/.claude.json` are kept on the console's PVC,
  and how often the login needs redoing.
- Storage for workspaces: ephemeral `emptyDir` per Job is the default; the
  console needs a persistent volume.
