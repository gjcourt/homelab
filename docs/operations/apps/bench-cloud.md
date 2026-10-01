---
title: bench-cloud
status: Stable
created: 2026-09-26
updated: 2026-09-28
updated_by: gjcourt
tags: [operations, apps, agents, claude-code]
---

# bench-cloud

Claude Code agents in the cluster: write code and open PRs, run tests, reach the
internet, move files to hestia. Plan and decisions:
[docs/plans/2026-09-25-bench-cloud-agent.md](../../plans/2026-09-25-bench-cloud-agent.md).

## Overview

| Piece | What | Where |
|---|---|---|
| Image | Claude Code + Go/Node/Python toolchains + GitHub App token helper | `images/bench-cloud/`, `ghcr.io/gjcourt/bench-cloud` |
| Console | One long-lived pod, tmux + Remote Control, `$HOME` on a 30 Gi PVC | `deploy/bench-console` |
| Task Jobs | Phase 2: one Job per task, submitted with `bench-cloud run` from the Mac | `scripts/bench-cloud/` (`job.yaml`, `bench-cloud`) |
| Namespace | `bench-cloud`, PSA `restricted` | `apps/base/bench-cloud/` |

## Access model

| Capability | Mechanism | Limit |
|---|---|---|
| Claude | Console: full `claude auth login` on the PVC. Jobs (phase 2): `setup-token` token | Console must never get `CLAUDE_CODE_OAUTH_TOKEN` — it overrides the login and Remote Control refuses it |
| GitHub | `bench-cloud` GitHub App, 1 h installation tokens | Contents/PRs/Issues RW; no Workflows, no Administration. `default-branch-guard` ruleset → can't push a default branch, only open PRs. **Can merge its own PR** (option 2): policy forbids it, `BenchCloudAppMerged` pages if it happens |
| Cluster | Tasks: `bench-agent` → built-in `view`. Console: `bench-console` → `view` + create/delete Jobs in `bench-cloud` only | Read-only elsewhere, no Secrets, no exec. A ValidatingAdmissionPolicy (`bench-cloud-task-jobs`) only admits Jobs that run as `bench-agent` with the `bench-cloud-task` PriorityClass, so tasks can't spawn tasks and every task counts against the quota. Creating Jobs does let the console reach any Secret in the namespace through a pod it starts — in practice only `bench-cloud-claude-token` is new to it, and its own full-scope login already covers that. `view` **does** include ConfigMaps and `pods/log` cluster-wide — anything an app logs, an agent can read |
| Internet | CiliumNetworkPolicy: 0.0.0.0/0 **minus RFC1918/CGNAT/link-local**, ports 80/443 | The home LAN is unreachable except hestia:22 |
| hestia | User `bench-agent`, two keys, each forced to `rrsync` | `hestia-inbox:` → `/mnt/main/agent-inbox` RW (symlinks munged); `hestia-media:` → `/mnt/main/family/media` RO |
| Concurrency | ResourceQuota on the `bench-cloud-task` PriorityClass | 8 task pods (raised from 5 on 2026-10-01; ceiling 10) |

## One-time operator setup

Everything here is George's: it involves secrets, GitHub account settings, or
hestia users.

**Status (2026-09-27): steps 1–3b are done** — App created, rulesets on every
repo, hestia user set up and tested, all three secrets committed encrypted. Only
step 4 (first console login) remains, after Flux starts the console.

**Order matters on a rebuild.** Committing the encrypted secrets is what lets
Flux start the console. Do steps 1–3 (App, rulesets, hestia) and prepare the
secret files, but **commit the secrets only after the rulesets from step 2
exist.**

### 1. GitHub App

1. github.com → Settings → Developer settings → GitHub Apps → **New GitHub App**.
   - Name `bench-cloud`; homepage `https://github.com/gjcourt/homelab`.
   - Webhook: **off** (uncheck Active).
   - Repository permissions: **Contents: Read and write · Pull requests: Read and
     write · Issues: Read and write · Actions: Read · Commit statuses: Read ·
     Metadata: Read**. Everything else, including **Workflows** and
     **Administration**: No access. No account permissions.
   - Where can it be installed: **Only on this account**.
2. Generate a private key (downloads a `.pem`).
3. **Install App** → gjcourt → **All repositories**. Note the installation ID at
   the end of the resulting settings URL.
4. Bot email: `gh api '/users/bench-cloud%5Bbot%5D' --jq .id` →
   `<id>+bench-cloud[bot]@users.noreply.github.com`.
5. Fill `apps/production/bench-cloud/secret-github-app.yaml` from its `.example`
   and `sops -e -i` it. Don't commit it until step 2 is done.

### 2. Default-branch rulesets and the merge audit

`scripts/github-rulesets.sh` puts `default-branch-guard` on every repo: default
branch changes need a PR (0 approvals), no deletion, no force-push; George
(Admin role) is the only bypass. Trialled 2026-09-26/27 on a scratch repo: the
App's direct push to `main` was refused (GH013); George's plain `gh pr merge`
worked without `--admin`; **the App could merge its own PR**.

That last one is deliberate (option 2, George 2026-09-27): a ruleset that blocks
the App's merges also blocks George's. Instead:
- the agent's managed `CLAUDE.md` and deny list forbid merging;
- `infra/controllers/bench-merge-audit` checks hourly for PRs merged by
  `bench-cloud[bot]` and pages **critical** (`BenchCloudAppMerged`) —
  response: revert the PR (on homelab it has already reached Flux), then read
  the task record under `hestia:/mnt/main/agent-inbox/runs/`.

Rollout, as George:

```bash
scripts/github-rulesets.sh           # dry run
scripts/github-rulesets.sh --apply
scripts/github-rulesets.sh --check   # also confirms current_user_can_bypass=always
```

Then unsuspend `infra/controllers/github-rulesets` (daily --apply + --check, so
new repos get the ruleset). Done 2026-09-27: `--check` reported `ok=76
needs-change=0 failed=0`, and the CronJob was unsuspended in #1493. A repo
without the ruleset is one the App can push the default branch of directly —
**never start the console with the App secret before the rulesets are in
place.**

### 3. hestia

On TrueNAS (UI, as it's the source of truth for users/datasets):

1. Dataset `main/agent-inbox` (mountpoint `/mnt/main/agent-inbox`).
2. User `bench-agent`: no password login, no sudo, shell `sh` (forced commands
   run through the login shell; `nologin` would refuse them), primary group its
   own. Make it the owner of `/mnt/main/agent-inbox`.

   ⚠️ **Its home directory must be outside both rrsync roots** — e.g.
   `/mnt/main/homes/bench-agent`, never `/mnt/main/agent-inbox`. sshd reads
   `~/.ssh/authorized_keys` (hestia: `AuthorizedKeysFile .ssh/authorized_keys`,
   `StrictModes yes`). If home were the inbox, the RW key could
   `rsync my_keys hestia-inbox:.ssh/authorized_keys`, drop the forced command,
   and get a shell on hestia.
3. Its `authorized_keys` — exactly two lines, from the two `.pub` files made
   while filling `secret-hestia-ssh.yaml`:

   ```
   command="/usr/bin/rrsync -munge /mnt/main/agent-inbox",restrict ssh-ed25519 AAAA… bench-cloud-inbox
   command="/usr/bin/rrsync -ro /mnt/main/family/media",restrict ssh-ed25519 AAAA… bench-cloud-media
   ```

   `restrict` turns off PTY, port/agent/X11 forwarding; `rrsync` allows only an
   rsync server confined to that directory. `-munge` (rsync `--munge-links`)
   stops an uploaded symlink (`x -> /mnt/main/family`) from being followed back
   out of the inbox; the read-only key can't upload one. (`/usr/bin/rrsync` on
   hestia is the rsync 3.4.1 Python version, which has `-ro` and `-munge`.)
   Media is already world-readable (755/644), so no group membership is
   needed — and must not be granted.
4. Fill `apps/production/bench-cloud/secret-hestia-ssh.yaml` from its `.example`
   and `sops -e -i` it.

### 3a. Image visibility

Done — `ghcr.io/gjcourt/bench-cloud` published **public** on its first build
(2026-09-26, `2026-09-26-129c30d`), like the other in-repo images. The pod has no
`imagePullSecrets` and needs none; the image holds no secrets. If it is ever
made private, add `ghcr-secret` to the namespace and pod spec (see
`apps/base/golinks/`).

### 3b. Task token (for phase 2 task Jobs)

Fill `apps/production/bench-cloud/secret-claude-token.yaml` from its `.example`
(the `claude setup-token` token) and `sops -e -i` it. Task Jobs only — the
console must never see it.

### 4. First console login

Done 2026-09-28. For a fresh PVC (login and consent lost):

```bash
kubectl -n bench-cloud exec -it deploy/bench-console -- tmux new -A -s main
# inside tmux:
claude auth login                 # opens a URL; complete it on the Mac
# the pod's `rc` session retries remote-control every 30s; answer its
# one-time consent there:
tmux attach -t rc                 # answer y, then Ctrl-b d
```

## Usage

The console starts Remote Control itself (tmux session `rc`, loop +
60 s watchdog), so after any restart **bench-cloud** reappears in
claude.ai/code and the Claude app on its own — login and consent persist on
the PVC (verified 2026-09-28).

```bash
# Hands-on shell (your own session; leave `rc` alone):
kubectl -n bench-cloud exec -it deploy/bench-console -- tmux new -A -s main
# Check Remote Control:
kubectl -n bench-cloud exec deploy/bench-console -- tmux capture-pane -p -t rc
```

Unattended tasks from the Mac: `scripts/bench-cloud/bench-cloud run --repo <repo> "<task>"`
(see `bench-cloud` with no arguments for `ls` / `logs` / `rm`).

The **console** can start tasks too — ask it from claude.ai/code or the app ("start
bench-cloud tasks for X in golinks and Y in llmux"); it runs the same
`bench-cloud` CLI baked into the image, as the `bench-console` ServiceAccount
(read-only `view` + create/delete Jobs in `bench-cloud` only). Task pods run as
`bench-agent`, which cannot create Jobs, so tasks never spawn tasks — and the
`bench-cloud-task-jobs` admission policy rejects any Job that asks for another
ServiceAccount or leaves out the `bench-cloud-task` PriorityClass, so a Job the
console writes by hand can't get around that or the 8-pod quota.

## George's skills and agents in the cluster

On every pod start (console and task Jobs) the entrypoint runs
`bench-agents-sync`: a shallow clone of private `gjcourt/agents` into
`~/.cache/bench-agents`, then symlinks **only** the items in
`/usr/local/share/bench-cloud/agents-allowlist` into `~/.claude` — same layout
as `install.sh` on the Mac: `skills/<name>/` is a real directory holding a
`SKILL.md` link plus the skill's `.skill-assets` (critique's `lenses/`), and
`agents/<name>.md` is a file link. Anything whose real path leaves the clone is
refused, and git is time-boxed (90 s) so a pod start can't hang on GitHub:

| Kind | Items |
|---|---|
| Skills | `babysit`, `critique`, `ci`, `deploy-verify` |
| Agents | `bench`, `webscout` |
| User CLAUDE.md | `cluster/CLAUDE.md` — the engineering sections of George's global rules |

The allowlist is reviewed **here** (it's in the image), so a change to
`gjcourt/agents` can't widen it. Left out on purpose: memory (personal; agents
read the open web), the life agents, valet / coach-* / collections-scan, gstack
(needs Chrome), settings/hooks/MCP. git-crypt'd parts of the repo arrive as
ciphertext (no key in the cluster). Updates to the allowlisted skills reach the
console on its next restart and every task on its next run — no image rebuild;
re-sync the console by hand with `kubectl -n bench-cloud exec deploy/bench-console
-- bench-agents-sync`. It never fails a pod: without GitHub access it logs and
skips.

## Verification

```bash
kubectl -n bench-cloud exec deploy/bench-console -- sh -c '
  claude auth status | grep -E "loggedIn|authMethod"      # true, not oauth_token
  gh api /installation/repositories --jq .total_count     # App sees your repos
  rsync --list-only hestia-inbox:                         # RW root
  rsync --list-only hestia-media:                         # RO root
  touch /tmp/x && rsync /tmp/x hestia-inbox:verify-x; echo "expect 0: $?"
  rsync --list-only hestia-inbox:.ssh/; echo "expect failure (home not in inbox): $?"
  rsync /tmp/x hestia-media:x; echo "expect failure: $?"
  curl -m 5 -sS https://10.42.2.10 >/dev/null; echo "expect failure (LAN): $?"
  kubectl auth can-i get secrets -A                       # no
'
```

## Monitoring

- **`BenchCloudAppMerged`** (critical) — the last `bench-merge-audit` run
  (`renovate` namespace, hourly) failed. The audit fails when it finds a PR
  merged by `bench-cloud[bot]` in its 25 h lookback, but also when the audit
  itself breaks, so read the run's log first:
  `kubectl -n renovate logs job/<latest bench-merge-audit job>`. A real merge:
  revert the PR, then read the task record under
  `hestia:/mnt/main/agent-inbox/runs/`.
- **`HomelabscopeJobStale{job="bench-merge-audit"}`** — the audit hasn't
  succeeded in 30 h (suspended, deleted, or failing every run).
- The home PVC is covered by `pvc-writeprobe` (`PvcNotWritable`), and the
  readiness probe writes to it too, so a read-only remount shows as an unready
  pod. Check the pod and PVC with `kubectl -n bench-cloud get pods,pvc`.

## Troubleshooting

- **Remote Control: "requires a full-scope login token"** — `CLAUDE_CODE_OAUTH_TOKEN`
  is set in the console's environment. It must not be.
- **Remote Control: "Workspace not trusted"** — the entrypoint marks `$WORKSPACE`
  (`/home/node/work`) trusted; run it from there.
- **git: "could not read Username"** — the App token couldn't be minted. Run
  `github-app-token` in the pod for the real error (bad key, wrong installation ID).
- **Pod not Ready** — the readiness probe writes to `$HOME`. An iSCSI read-only
  remount looks exactly like this; see AGENTS.md "Recovering read-only iSCSI
  volumes". Recovery is `kubectl -n bench-cloud delete pod -l app=bench-console`
  (not `rollout restart`), which also ends the tmux sessions.
- **ssh config change not picked up** — `hestia.conf` is mounted by `subPath`,
  so ConfigMap edits reach the pod only after a restart.

## Disaster recovery

The PVC holds only the Claude login, caches, and scratch checkouts; everything
that matters is in GitHub PRs or on hestia. Losing it means redoing step 4.
