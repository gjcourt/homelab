---
status: planned
last_modified: 2026-10-25
summary: "Claude Code in WSL2 on winpc so the laptop can use George's agents remotely, via Remote Control and SSH over WireGuard"
---

# Claude Code on winpc

Plane: HOMELAB-71.

## Goal

George wants his Claude Code setup on an always-on machine at home, so the laptop can use the same
agents remotely. HOMELAB-71 started with the office Raspberry Pi. Asked about it, George
proposed winpc instead ("Could we run this off winpc?", 2026-10-25), and noted it has a 1 TB
high-quality SSD.

This plan settles the questions HOMELAB-71 lists and proposes the build. It changes nothing until
George approves it. George answered every question on 2026-10-25; his answers are recorded as
[Decisions](#decisions-george-2026-10-25). The network move it depends on is done in lab 03-043.

## Why winpc, not the office Pi

The office audio node was checked first (read-only, 2026-10-25):

| | Office Pi (dietpi-audio node) | winpc |
| --- | --- | --- |
| RAM | 3.8 GiB, no swap, shared with snapclient and go-librespot | 31.9 GB (WSL2 gets 15 GiB by default) |
| CPU | Pi 4, 4 cores, aarch64 | Ryzen 5 5600X, x86_64 |
| Claude Code and Remote Control on the CPU | Unverified on aarch64 | x86_64 Linux, the same as bench-cloud's console, where Remote Control already runs |
| Network | Wi-Fi only, IoT VLAN, flaky SSH | Wired |
| Always on | Yes | Sleep on AC power is "never"; it had been up 11.5 days |
| Storage | 59 GB SD card, set up write-light | Samsung SSD 860 EVO 1 TB, 772 GB free on C: |

winpc removes the two biggest risks of the Pi plan: running out of memory next to the audio
services, and the unverified ARM build. The Pi stays an audio node.

## What was checked on winpc (2026-10-25)

All read-only, over SSH from the Mac (Windows OpenSSH as `george`). Nothing was installed or
changed.

| Item | Finding |
| --- | --- |
| Host | `DESKTOP-T91IG8F`, Windows 11 Pro 10.0.26200 (build 26200.9711) |
| CPU, RAM | AMD Ryzen 5 5600X (6 cores), 31.9 GB |
| Disk | Samsung SSD 860 EVO 1TB (SATA, healthy); C: 772 GB free, 158 GB used. A second drive, D:, is full (43 GB) |
| Power | Sleep on AC: never. Uptime 11.5 days |
| Network | Wired, `10.42.1.116`, which is the **Core VLAN** (`br0`, see below). The Windows profile also shows a `169.254.x` address on a second adapter |
| Internet from winpc | `github.com` 200, `registry.npmjs.org` 200, `plane.burntbytes.com` 200, `api.anthropic.com` answers (404 at `/`), `claude.ai` 403 to curl (probably a bot check: reached, but not proven usable). Checked from Windows; WSL has no `curl` yet |
| WSL | WSL 2.4.11, kernel 5.15.167. Default distro **Debian 12 (bookworm), WSL 2**, plus Docker Desktop's two distros |
| Inside Debian | x86_64; 15 GiB RAM visible (WSL's default, half the host); a 251 GB virtual disk, 1% used. **No `/etc/wsl.conf`**, so C: is mounted at `/mnt/c`. PID 1 is WSL's own init, **not systemd**. No `git`, `tmux`, `gh`, `node`, `claude`, `curl` or `mosh` |
| `.wslconfig` | None, so all WSL defaults apply |
| Windows OpenSSH | `sshd` running, version 9.5; no `DefaultShell` set (logins get `cmd`) |

**Not verified:** whether WireGuard clients can reach winpc's VLAN; whether WSL keeps running with no
session attached (see [Keeping WSL running](#keeping-wsl-running)); real memory use with several
sessions; whether `claude.ai` login works from winpc's browser (it will, almost certainly, but it
wasn't tried).

**Core VLAN.** winpc sits on the **Core VLAN** (`10.42.1.0/24`), which
[the addressing map](../architecture/networking/addressing.md) describes as "UniFi infrastructure
(switches, APs, gateway mgmt)" and "locked-down management plane". George decided to move it to
Lab (see [Dependency: the move to Lab](#dependency-the-move-to-lab)).

## Decisions (George, 2026-10-25)

| Question | George's answer |
| --- | --- |
| Host | winpc, not a Pi |
| Storage | winpc's 1 TB SSD (the WSL virtual disk lives on C:) |
| Where Claude Code runs | WSL2 Debian, with C: **not** mounted, so agents can't read Windows files |
| Access from the laptop | Remote Control, plus SSH over WireGuard |
| Memory | winpc may write memory; sync by hand |
| Life agents and valet | Yes, all agents. So the git-crypt keys and valet's Gmail OAuth token go to winpc |
| GitHub | Its own fine-grained PAT |
| Cluster | The same access as the Mac: the admin kubeconfig |
| "The laptop" | This Mac |
| GitHub PAT scope | All `gjcourt` repos |
| Network | Move winpc off Core to the Lab VLAN, done under lab 03-043 (below) |
| WSL memory | Capped at 24 GB (`.wslconfig` `memory=24GB`) |

## Dependency: the move to Lab

George's decision (2026-10-25): winpc moves off the Core VLAN to **Lab** (`10.42.2.0/24`). The move
itself is network work and is done in
[lab 03-043](https://github.com/gjcourt/lab/blob/main/03-homelab-automation/03-043-ip-addressing-cleanup.md)
(Plane AUTO-43, IP addressing cleanup), not here. This plan's build can start on Core and needs
the move finished before its exit criteria are signed off.

What winpc must still reach from Lab, for 03-043 to check before and after the move:

| Need | Destination | Why |
| --- | --- | --- |
| Internet egress | `github.com`, `registry.npmjs.org`, `api.anthropic.com`, `claude.ai` (HTTPS) | Claude Code, Remote Control, git, npm |
| Cluster API | `10.42.2.20:6443` | The admin kubeconfig (same VLAN after the move) |
| hestia | SSH `:22` (and whatever else the media pipeline uses today; 03-043 records it) | Media hand-off to hestia (the transcode push scp's to hestia), and agents' hestia access |
| Plane | `plane.burntbytes.com` (HTTPS via the gateway) | The plane MCP server and agent-desk work |
| WireGuard in | VPN clients (`wgsrv1`, `192.168.2.0/24`) to winpc's SSH `:22` | SSH from the Mac away from home. **Untested today** |
| From the Mac (Family VLAN) | winpc SSH `:22` | SSH on the home network, and the existing media scripts |

**winpc's other roles must keep working after the move:** CD and Blu-ray ripping (local), and the
media hand-off to hestia. 03-043 should run one rip-and-push end to end after the move, as well
as the Claude checks above. Its address changes (it's `10.42.1.116` today), so the SSH config on
the Mac, the `HostKeyAlias` in George's notes, and any script that names the IP need updating.

## Runtime: WSL2 Debian

### No Windows files

`/etc/wsl.conf` in Debian:

```ini
[automount]
enabled = false        # no /mnt/c: agents can't read C:\
mountFsTab = false

[interop]
enabled = false        # no running Windows .exe files from Linux
appendWindowsPath = false

[boot]
systemd = true         # for the Remote Control unit below

[user]
default = <linux user>
```

Turning off `interop` also stops agents from calling `powershell.exe` or `cmd.exe`, which would
otherwise reach C: anyway. Neither setting is a security boundary: the WSL VM runs as George's
Windows user, and a determined process could still find a way across. They keep an ordinary agent
session, and an ordinary mistake, out of his Windows files. Files that need to cross (the
credentials below) are streamed in over SSH, never through `/mnt/c`.

### Keeping WSL running

WSL stops a distro when nothing is running in it, and stops its VM after `vmIdleTimeout`
(60 s by default). Remote Control has to keep running with no one attached. The proposal, to be
confirmed in build step 4:

- **`systemd = true`** (above) and a user unit, `claude-remote.service`, that runs
  `claude remote-control` inside tmux, restarts on failure, and logs to the journal.
- **A Windows scheduled task, "at startup", run whether George is logged in or not**, that runs
  `wsl.exe -d Debian --exec /bin/sleep infinity`. That holds the distro open, so systemd and the unit
  stay up after a reboot without anyone logging in.
- **`%UserProfile%\.wslconfig`**:

  ```ini
  [wsl2]
  memory=24GB            # George's decision; default is half the host (15 GiB seen); leaves 8 GB for Windows
  vmIdleTimeout=-1       # don't stop the VM when idle
  ```

**Unverified:** that `vmIdleTimeout=-1` is honoured by WSL 2.4.11, and that systemd with a running
service keeps the distro alive with no `wsl.exe` attached. The scheduled task covers the second
either way. Step 4 tests both by rebooting winpc and connecting without logging in.

**Windows Update restarts the machine.** With the scheduled task, Remote Control comes back by
itself after a reboot. Active hours (set in Windows Update) keep restarts away from the hours
George works.

## Network

- **Internet:** works from winpc today for GitHub, npm, Plane and the Anthropic API.
- **Laptop on the home network:** SSH to winpc works from the Mac on the Family VLAN (it's how
  this plan's checks ran).
- **Laptop away from home:** "the laptop" is this Mac (George). Remote Control needs no inbound
  path. SSH needs WireGuard (the gateway's `wgsrv1`, `192.168.2.0/24`) to reach winpc, which is
  **untested**; it's one of the checks for the move to Lab above.

## How the laptop uses it

1. **Remote Control (primary).** `claude-remote.service` above. The laptop (or a phone) drives it
   from claude.ai/code or the Claude app. It only connects outward, so it works from anywhere with
   no VPN. It needs a full `claude auth login` (Remote Control refuses a
   `CLAUDE_CODE_OAUTH_TOKEN` setup-token, as on bench-cloud's console).
2. **SSH over WireGuard (secondary).** Through **Windows' own OpenSSH**, which is already running
   with George's key:

   ```bash
   ssh -t winpc wsl -d Debian -- tmux new -A -s main
   ```

   (a `Host winpc` entry in `~/.ssh/config` on the laptop, with `RequestTTY yes`). Running `sshd`
   inside WSL instead would need a port forward from Windows into the WSL VM, which changes address
   on restart under WSL's default NAT networking. The Windows `sshd` avoids that, and it's one
   listener to keep up to date, not two. Setting OpenSSH's `DefaultShell` to `wsl.exe` would make
   plain `ssh winpc` land in Debian, but it would also change what the existing media scripts
   (which SSH in for PowerShell) get, so this plan doesn't touch it.

## What bench-cloud already covers

| | bench-cloud console (in the cluster) | winpc |
| --- | --- | --- |
| Claude Code from the laptop or phone | Yes, Remote Control | Yes, Remote Control, plus SSH |
| Skills and agents | Allowlist only: babysit, critique, ci, deploy-verify; bench, webscout | Everything in `~/src/agents`, gstack included |
| Memory | Excluded on purpose | Shared with the Mac, written on both |
| Life agents, valet | Excluded | Yes (git-crypt keys and Gmail OAuth on winpc) |
| GitHub | The bench-cloud App: PRs only, no Workflows | Its own fine-grained PAT |
| Home LAN | None except hestia `:22` | winpc's VLAN reach |
| Cluster | Read-only `view` | Admin, as on the Mac |
| Survives restarts | Yes, PVC (verified 2026-09-28) | Yes, the SSD; the service comes back after a reboot (step 4) |

So winpc adds George's **whole personal setup**: memory, life agents, valet, gstack, LAN and
cluster admin. For engineering work that only touches GitHub, the console already does the job.

## What moves, and how it stays in sync

| Item | How | Notes |
| --- | --- | --- |
| Agents, skills | `git clone gjcourt/agents ~/src/agents`, unlock git-crypt, `./install.sh` | The same symlinks as on the Mac. Sync is `git pull` |
| Memory | Comes with the agents repo (`memory/store`, git-crypt-encrypted) | **Two writers, synced by hand** (George's decision): each machine commits and pushes its memory changes, and pulls before starting work. A memory written on one machine isn't seen on the other until then. Conflicts are resolved by hand like any git conflict |
| gstack | Its own installer | Hooks use absolute paths, so it's installed fresh, not copied |
| `settings.json` | A winpc-specific file, written by hand | Paths differ (`/home/<user>` against `/Users/george`) |
| `CLAUDE.md` (global) | Copied once, then kept in step by hand, or moved into the agents repo | It has Mac-specific lines (Podman, Docker Desktop, this Mac's kubectl) |
| Repos under `~/src` | `git clone`, only the ones needed | Sync is git push and pull; never rsync a working tree that two machines edit |
| life repo | `git clone gjcourt/life`, unlock git-crypt | Same rules as on the Mac (branch and PR) |
| valet | Comes with the agents repo; its venv rebuilt on winpc | Needs its OAuth files (Credentials) |
| Transcripts (`~/.claude/projects`) | Not moved | Per-machine history |
| Plugins, MCP connectors | Re-added on winpc | claude.ai connectors follow the account; local MCP servers need installing |

**git-crypt and worktrees.** Both `agents` and `life` are git-crypt repos, and `git worktree add`
in them fails with `smudge filter git-crypt failed` until the key is copied into the worktree's
`$GIT_DIR` (`.git/worktrees/<wt>/git-crypt/keys/default`). That isn't corruption; the fix is in the
memory note `feedback_life_repo_gitcrypt_worktree`. It applies on winpc exactly as on the Mac.

## Credentials

**How secrets get there.** Every secret file is streamed from the Mac straight into WSL over SSH,
so it never lands on the Windows disk, in a chat, or in a repo:

```bash
ssh winpc 'wsl -d Debian -- sh -c "umask 077; mkdir -p ~/.secrets; cat > ~/.secrets/<name>"' < <file>
```

Then it's moved to where the tool expects it, mode `600`, owned by the Linux user. The WSL virtual
disk sits on C:, so anyone with George's Windows login can still reach it; that's the same trust
as his Windows account today.

| Credential | Plan | Revocation |
| --- | --- | --- |
| Claude | `claude auth login` in WSL, completed in a browser | claude.ai → Settings → sessions, or `claude auth logout` |
| GitHub | A **new fine-grained PAT** just for winpc (George's decision). Contents and pull requests read/write on **all `gjcourt` repos** (George's decision); no admin, no Workflows unless he adds them. Stored by `gh auth login --with-token` | Revoke the PAT on GitHub; the Mac is untouched |
| git-crypt keys (agents, life) | `git-crypt export-key` on the Mac, streamed in as above, then `git-crypt unlock <key>` in each clone, then the exported files deleted on both ends | **A git-crypt key can't be revoked.** Anyone holding it can decrypt every past and future commit until the repo is re-keyed, which means re-encrypting under a new key and rewriting history. Losing winpc means treating the key as exposed |
| valet (Gmail OAuth) | `credentials.json` and `token.json` streamed into `~/src/agents/valet/` (mode `600`) | Google Account → Security → third-party access → remove valet's app; then a new token on the Mac (valet's usual re-auth) |
| kubeconfig | The **admin kubeconfig**, as on the Mac (George's decision), streamed in as above to `~/.kube/config` | **Not revocable on its own:** Kubernetes can't revoke one client certificate. Fetching a new kubeconfig doesn't invalidate the old one; only rotating the cluster CA (`talosctl rotate-ca`) does, which affects every client |
| SOPS age key | **Not copied.** SOPS is operator-only, and nothing agent-side needs it. Secrets work stays on the Mac | — |
| talosconfig, infra SSH keys | Not copied; George didn't ask for them | — |

**Risk, stated once.** With the admin kubeconfig, the git-crypt keys and the Gmail token, winpc
holds the same reach as the Mac. A compromise of George's Windows account, or of anything running
under it, becomes a compromise of all of those. That is George's decision; the plan records it.

## Build steps (after approval)

1. **WSL basics:** create the Linux user, write `/etc/wsl.conf` and `.wslconfig` (above), then
   `wsl --shutdown` and start Debian again. Check `/mnt/c` is gone, `powershell.exe` doesn't run,
   and PID 1 is systemd.
2. **Packages:** `apt install git tmux curl mosh ca-certificates`, then `gh` (GitHub's apt repo),
   `git-crypt` and Node if a tool needs it.
3. **Claude Code:** the native installer for linux-x64, then `claude auth login`.
4. **Keep it running:** the `claude-remote.service` user unit and the Windows startup task. Test:
   reboot winpc, **don't log in**, and connect with Remote Control from the laptop. Leave it idle
   for an hour and connect again. If either fails, fix this step before going on.
5. **Agents and memory:** clone `gjcourt/agents`, stream in and unlock the git-crypt key, run
   `./install.sh --check` then `./install.sh`, and set up the memory symlink in winpc's project
   directory.
6. **Life, valet:** clone `gjcourt/life` and unlock it; stream in valet's OAuth files; rebuild
   valet's venv; run valet's read-only `list-recruiter-threads` to prove the token works.
7. **GitHub and cluster:** the new PAT into `gh`; the kubeconfig into `~/.kube/config`;
   `kubectl get nodes` to prove it.
8. **Verify from the laptop:** a Remote Control session uses a personal agent, reads memory, writes
   a test memory, pushes it, and the Mac sees it after a pull. Do it once on the LAN and once on
   cellular. Then SSH over WireGuard from outside (if it can't reach winpc's VLAN, record that and
   decide with George).
9. **Record:** a short host note under `hosts/winpc-5600x/`. The access details and the list of
   credentials on winpc go in George's private infra access log, not the repo.

## Exit criteria

- [ ] From the laptop away from home, a Remote Control session on winpc runs a personal agent and
      reads and writes memory.
- [ ] After a reboot with nobody logged in, Remote Control is reachable again without anyone
      touching winpc.
- [ ] `/mnt/c` doesn't exist in Debian, and Windows programs can't be started from it.
- [ ] A memory written on winpc shows up on the Mac after a pull, and the other way round.
- [ ] valet's read-only `list-recruiter-threads` and a life-repo decrypt both work on winpc.
- [ ] winpc is on the Lab VLAN (03-043 done), and ripping and the hand-off to hestia still work.
- [ ] No SOPS key on winpc. Every credential on it is listed in the private access log with how
      to revoke it.

## Open questions for George

None left: George answered all of them on 2026-10-25 (see [Decisions](#decisions-george-2026-10-25)).
What remains unverified is listed under [What was checked on winpc](#what-was-checked-on-winpc-2026-10-25)
and is settled by the build steps.
