---
status: planned
last_modified: 2026-10-25
summary: "Claude Code on the office Pi 4 (or a spare) so the laptop can use George's agents remotely, via Remote Control"
---

# Claude Code on the office Pi

Plane: HOMELAB-71.

## Goal

George wants his Claude Code setup on the office Raspberry Pi, so the laptop can use the same
agents remotely. The Pi is always on the home network.

This plan settles the questions HOMELAB-71 lists and proposes the build. It changes nothing
until George approves it. Decisions that are his are listed under [Open questions](#open-questions-for-george).

## What was checked (2026-10-25)

All read-only, over SSH from the Mac on the Family VLAN, plus the gateway's neighbour table.

| Item | Finding |
| --- | --- |
| Which Pi | Hostname `office`, the dietpi-audio node ([`hosts/dietpi-audio/`](../../hosts/dietpi-audio/README.md)): snapclient and go-librespot both `active` |
| Board | Raspberry Pi 4 Model B Rev 1.5, 4 cores, **3.8 GiB RAM**, **no swap** |
| OS | Debian 13 (trixie), kernel 6.18 aarch64 (DietPi) |
| Storage | 59 GB root, 2% used. The node was set up as write-light (DietPi RAMlog) |
| Network | **Wi-Fi only** (`eth0` down), IoT VLAN, now at **10.42.7.148**. The ticket's `.149` is stale: the gateway has no MAC for it |
| Load today | Idle: load 0.00; the audio services use about 55 MB between them |
| Installed | No `git`, `tmux`, `gh`, `node` or `claude` |
| Internet from IoT | `github.com` 200, `registry.npmjs.org` 200, `api.anthropic.com` answers (404 at `/`), `claude.ai` 403 to curl (probably a bot check: reached, but not proven usable) |
| LAN from IoT | `plane.burntbytes.com` 200; hestia `:22`, the Kubernetes API `10.42.2.20:6443` and snapserver `:1704` all open |
| From the Mac | Family → IoT SSH works |
| VPN | The gateway runs a WireGuard server (`wgsrv1`, 192.168.2.0/24). **Not checked:** whether a VPN client can reach `10.42.7.0/24` |
| SSH to the Pi | Flaky: several connections timed out while ping showed 0% loss (12 ms average). Each attempt succeeded once the port answered again. Cause not found (the Pi runs dropbear) |

On the Mac side: `~/.claude` is 21 GB. Most of that is not configuration: `projects/` (transcripts) is
828 MB and `skills/` 1.1 GB (mostly gstack). `~/src` is 8.5 GB across 67 entries. The personal
agents and several skills are symlinks into `~/src/agents`, installed by its `install.sh`.
`~/.claude/projects/-Users-george-src/memory` is a symlink into `~/src/agents/memory/store`.
`settings.json` holds absolute `/Users/george/...` paths (gstack hooks), so it can't be copied
across as it is.

## Which Pi, and the RAM

**The office node is a 4 GB Pi 4, so it fits the ticket's "about 4 GB" only with nothing to spare.**
That 4 GB figure is the ticket's estimate, not a measurement. Two risks follow from sharing the board
with audio:

- **Memory.** With no swap, a few concurrent Claude Code sessions or subagents could push the
  kernel's OOM killer onto snapclient or go-librespot.
- **Audio glitches.** The node already showed underruns on IoT Wi-Fi in testing ([lab
  01-016](https://github.com/gjcourt/lab/blob/main/01-audio-midi/01-016-diy-digital-domain-streamer.md)).
  CPU and Wi-Fi bursts from git clones and npm installs make that worse.

So there are two options:

- **A. Use the office node as it is.** Add zram swap, lower Claude's priority (`nice`, and
  `CPUWeight` in a systemd unit), and measure RSS with real sessions before trusting it.
- **B. Use one of the spare Pi 4s** that endpoints were standardised on (lab 01-016, 2026-07-16),
  wired if the office has a drop. That keeps audio and agents apart. Whether a spare has 4 or 8 GB
  isn't recorded.

Recommendation: **B if a spare exists**, ideally on Ethernet; A otherwise, with the measurement
step below as a gate.

**Storage.** The card was sized for a write-light Snapcast client. Repos, `node_modules`, Claude
transcripts and git writes are not light. A USB SSD for `$HOME`, or at least a card bought for
endurance, is worth it on either option.

## Network

- **Internet:** works from IoT today. Nothing needs to change for Claude, GitHub or npm.
- **Laptop on the home network:** Family → IoT SSH works.
- **Laptop away from home:** depends on how the laptop connects (next section). With Remote Control
  no inbound path is needed. With SSH, it needs WireGuard to reach IoT, which hasn't been tested.
- **Finding, not part of this plan:** IoT can open hestia's SSH port and the Kubernetes API. That's
  wider than an IoT VLAN usually allows. It's relevant here because an agent host on IoT inherits
  that reach. [lab 03-043](https://github.com/gjcourt/lab/blob/main/03-homelab-automation/03-043-ip-addressing-cleanup.md) (Plane AUTO-43) (addressing cleanup) is the
  natural home for it.

## How the laptop uses it

1. **Remote Control (recommended primary).** `claude remote-control` runs on the Pi under tmux, kept
   alive by a systemd unit. The laptop (or a phone) drives it from claude.ai/code or the Claude app.
   It only connects outward, so it works from anywhere with no VPN. bench-cloud's console already
   runs this way ([bench-cloud runbook](../operations/apps/bench-cloud.md)). That's proof it works on
   Linux, but only on that image's architecture: on the Pi's aarch64 it is **unverified**.
   It needs a full `claude auth login`. Remote Control refuses a `CLAUDE_CODE_OAUTH_TOKEN`
   setup-token.
2. **SSH + tmux (secondary, for admin).** `ssh office` then `tmux attach`. This works today on the
   LAN. Away from home it needs WireGuard to reach IoT (untested; see Open questions). `mosh` would
   cope better with the flaky link than plain SSH.

## What bench-cloud already covers

| | bench-cloud console (in the cluster) | Office Pi |
| --- | --- | --- |
| Claude Code from the laptop or phone | Yes, Remote Control | Yes, Remote Control |
| Skills and agents | Allowlist only: babysit, critique, ci, deploy-verify; bench, webscout | Whatever George installs from `~/src/agents`, gstack included |
| Memory | Excluded on purpose | Shared with the Mac (see below) |
| Life agents, valet | Excluded | Possible. Their data is git-crypt-encrypted, so the Pi needs the key |
| GitHub | The bench-cloud App: PRs only, no Workflows | Whatever credential George gives it |
| Home LAN | None except hestia `:22` | The IoT VLAN's reach (all the LAN checks above) |
| Cluster | Read-only `view` | None unless a kubeconfig is copied (see Credentials) |
| Survives restarts | Yes, PVC (verified 2026-09-28) | Yes, the SD card or SSD |

So the Pi adds George's **personal** setup: memory, life agents, gstack and valet, plus LAN reach.
For engineering work that only touches GitHub, the console already does the job.

## What moves, and how it stays in sync

| Item | How | Notes |
| --- | --- | --- |
| Agents, skills | `git clone gjcourt/agents ~/src/agents && ./install.sh` | The same symlinks as on the Mac. Sync is `git pull` |
| Memory | Comes with the agents repo (`memory/store`) | **Two writers.** The Mac and the Pi both write memory files. They need to commit and pull, or one session overwrites the other. See Open questions |
| gstack | Its own installer | Hooks use absolute paths, so it's installed fresh, not copied |
| `settings.json` | A Pi-specific file, written by hand | Paths differ (`/root` or `/home/<user>` against `/Users/george`) |
| `CLAUDE.md` (global) | Copied once, then kept in step by hand, or moved into the agents repo | It has Mac-specific lines (Podman, Docker Desktop, this Mac's kubectl) |
| Repos under `~/src` | `git clone`, only the ones needed | Sync is git push and pull; never rsync a working tree that two machines edit |
| Transcripts (`~/.claude/projects`) | Not moved | Per-machine history |
| Plugins, MCP connectors | Re-added on the Pi | claude.ai connectors follow the account; local MCP servers need installing |

## Credentials

| Credential | Proposal |
| --- | --- |
| Claude | `claude auth login` on the Pi, completed in the laptop's browser. Full scope, as Remote Control requires |
| GitHub (`gh`, git) | A **new** key or token just for this machine, so it can be revoked on its own. Scope is George's call |
| SOPS age key | **Not copied.** SOPS is operator-only, and an agent host on IoT is the wrong place for it. Secrets work stays on the Mac |
| git-crypt keys (agents, life) | George decides. Without them, the encrypted parts of those repos stay ciphertext on the Pi |
| kubeconfig, talosconfig | Not copied by default. If George wants cluster access from the Pi, the plan would be a `view`-only kubeconfig, not the admin one |
| Infra SSH keys | Not copied by default |

## Build steps (after approval)

1. **Choose the board** (Open question 1). If it's a spare, flash DietPi with the dietpi-audio
   provisioning steps, minus the audio script.
2. **Base:** `apt install git tmux mosh`, then `gh`. Add zram swap. Use a non-root user for Claude
   (the audio node only has root today).
3. **Claude Code:** the native installer for linux-arm64. Confirm it runs on aarch64 before going
   further. Then `claude auth login`.
4. **Measure (gate for option A):** run two or three typical sessions with subagents and record peak
   RSS and swap use, and whether the audio stuttered. If audio stutters or memory runs out, stop
   and switch to option B.
5. **Agents and memory:** clone `gjcourt/agents`, run `./install.sh --check` then `./install.sh`, and
   set up the memory symlink in the Pi's project directory.
6. **Remote Control:** a systemd unit that runs it in tmux, restarts on failure and logs its state.
   Answer the one-time consent prompt.
7. **Verify from the laptop:** a session started from claude.ai/code uses a personal agent and reads
   memory. Do it once on the LAN and once on cellular.
8. **Record:** a short host note under `hosts/`. The access details go in George's private infra
   access log, not the repo.

## Exit criteria

- [ ] From the laptop away from home, a Remote Control session on the Pi runs a personal agent and
      reads and writes memory.
- [ ] A memory written on the Pi shows up on the Mac after a pull, and the other way round.
- [ ] If it runs on the audio node: a week of normal use with no OOM kills of snapclient or
      go-librespot, and no audio stutter that George notices.
- [ ] No SOPS key on the Pi. Every credential on it is listed in the private access log and can be
      revoked on its own.

## Open questions for George

1. **Board:** the office audio node (4 GB, shared with audio, Wi-Fi), or a spare Pi 4 (and is one
   free, with how much RAM)? Is there an Ethernet drop in the office?
2. **Storage:** is a USB SSD for `$HOME` acceptable, or stay on the card?
3. **"The laptop":** is it this Mac, or another machine? If it's another machine, does it need its
   own copy of the setup at all once the Pi exists?
4. **Access away from home:** is Remote Control enough, or do you also want SSH over WireGuard? The
   latter needs a check that VPN clients can reach IoT, and maybe a firewall rule.
5. **Memory with two writers:** commit and pull by hand, or a small sync (for example, the Pi commits
   and pushes memory changes on a timer)? Should the Pi be allowed to write memory at all?
6. **Personal agents on the Pi:** life agents and valet need the git-crypt keys and Gmail OAuth on
   the Pi. In scope, or engineering plus memory only?
7. **GitHub scope** for the Pi's credential: your account with full access, or something narrower?
8. **Cluster access** from the Pi: none, or read-only?
