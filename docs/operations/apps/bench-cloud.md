---
title: bench-cloud
status: Stable
created: 2026-09-26
updated: 2026-09-26
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
| Task Jobs | Phase 2 — not yet built | — |
| Namespace | `bench-cloud`, PSA `restricted` | `apps/base/bench-cloud/` |

## Access model

| Capability | Mechanism | Limit |
|---|---|---|
| Claude | Console: full `claude auth login` on the PVC. Jobs (phase 2): `setup-token` token | Console must never get `CLAUDE_CODE_OAUTH_TOKEN` — it overrides the login and Remote Control refuses it |
| GitHub | `bench-cloud` GitHub App, 1 h installation tokens | Contents/PRs/Issues RW; no Workflows, no Administration. Default-branch ruleset only George bypasses → can't merge |
| Cluster | ServiceAccount `bench-agent` → built-in `view` | Read-only, no Secrets, no exec. `view` **does** include ConfigMaps and `pods/log` cluster-wide — anything an app logs, an agent can read |
| Internet | CiliumNetworkPolicy: 0.0.0.0/0 **minus RFC1918/CGNAT/link-local**, ports 80/443 | The home LAN is unreachable except hestia:22 |
| hestia | User `bench-agent`, two keys, each forced to `rrsync` | `hestia-inbox:` → `/mnt/main/agent-inbox` RW (symlinks munged); `hestia-media:` → `/mnt/main/family/media` RO |
| Concurrency | ResourceQuota on the `bench-cloud-task` PriorityClass | 5 task pods (ceiling 10) |

## One-time operator setup

Everything here is George's: it involves secrets, GitHub account settings, or
hestia users.

**Order matters.** Committing the two encrypted secrets is what lets Flux start
the console. Do steps 1–3 (App, rulesets, hestia) and prepare both secret files,
but **commit the secrets only after the rulesets from step 2 exist.**

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

### 2. Default-branch rulesets (what actually stops merges)

Applied to every repo by a script — separate PR. Until it runs, the App can merge
and push to default branches on any repo without one. **Do not start the console
with the App secret before the rulesets are in place.**

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
   hestia is the rsync 3.4.1 Python version, which has `-ro` and `-munge`.) Media is already world-readable
   (755/644), so no group membership is needed — and must not be granted.
4. Fill `apps/production/bench-cloud/secret-hestia-ssh.yaml` from its `.example`
   and `sops -e -i` it.

### 3a. Image visibility

The pod has no `imagePullSecrets`. After the first `build-bench-cloud.yml` run,
check `ghcr.io/gjcourt/bench-cloud` is **public** (github.com → Packages →
bench-cloud → Package settings); it holds no secrets. Confirm with an anonymous
pull. If it must stay private, add `ghcr-secret` to the namespace and the pod
spec instead (see `apps/base/golinks/`).

### 4. First console login

```bash
kubectl -n bench-cloud exec -it deploy/bench-console -- tmux new -A -s main
# inside tmux:
cd ~/work                         # the only directory the entrypoint marks trusted
claude auth login                 # opens a URL; complete it on the Mac
claude remote-control --name bench-cloud   # answer y to the one-time consent
# detach: Ctrl-b d
```

## Usage

```bash
# Attach (creates the session if it's gone):
kubectl -n bench-cloud exec -it deploy/bench-console -- tmux new -A -s main
```

Or open **bench-cloud** in claude.ai/code or the Claude app while the
`remote-control` session runs in tmux.

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

No app-specific alerts. The home PVC is covered by `pvc-writeprobe`
(`PvcNotWritable`), and the readiness probe writes to it too, so a read-only
remount shows as an unready pod. Check the pod and PVC with
`kubectl -n bench-cloud get pods,pvc`.

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
