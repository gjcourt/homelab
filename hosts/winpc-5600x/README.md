# winpc-5600x

Windows 11 Pro desktop. **Hostname `daedalus`** (renamed from `DESKTOP-T91IG8F`
on 2026-10-25), `10.42.1.116` on wired Ethernet, SSH as `george` with an installed
ed25519 key. The SSH host key was first recorded under the old Wi-Fi address, so
connect with `ssh -o HostKeyAlias=10.42.4.195 george@10.42.1.116`.

| | |
| :--- | :--- |
| CPU | AMD Ryzen 5 5600X, 6 cores / 12 threads |
| Board / RAM / GPU | ASRock B550M-ITX/ac (BIOS P1.80) · 2 × 16 GB DDR4-3200 (both slots) · Radeon RX 580 |
| Disk | Samsung 860 EVO 1 TB (SATA) |
| Roles | CD ripping (EAC + MusicBrainz Picard), Blu-ray ripping (MakeMKV), x265 transcoding, Claude Code host (WSL2, below) |
| Rip outputs | audio `C:\Rips` (flat, tagged FLAC) · video `C:\Video\<Title>\` |
| Work dir | `C:\media-work` (one in-progress encode + logs) |
| Scripts | **deployed from git** — the box clones this repo to `C:\Users\george\src\homelab` and runs from the checkout |

## Deployment — the box is not authoritative

Scripts are **not** edited on this machine. `run-transcode.bat` does a
`git pull --ff-only` and then executes from the checkout, so a local edit is
silently discarded on the next run. Edit here, merge, run.

```
C:\Users\george\src\homelab      sparse checkout, hosts/winpc-5600x only (~3.5 MB)
C:\media-work\queue.tsv           working queue - per-run DATA, deliberately not in git
```

The point is not the pull, it is that afterwards there is **one copy** at a known
commit. `git -C C:\Users\george\src\homelab log -1` says exactly what is deployed,
and the launcher prints it on every run.

**Access.** A dedicated **read-only deploy key** on `gjcourt/homelab`
(`winpc-5600x (transcode pipeline, read-only)`), used via the `github-homelab`
ssh alias so it is scoped to this repo rather than becoming the box's default
GitHub identity. Cloned with `--filter=blob:none` and a sparse cone on
`hosts/winpc-5600x`, so **no SOPS blobs and no `apps/`/`infra/` content ever land
on this machine**.

Deliberately *not* a self-hosted GHA runner like hestia and alcatraz: this is a
desktop that reboots, and a push-deploy to a machine that is down fails silently
rather than loudly. (Sleep and hibernate are off on AC power, checked 2026-10-25,
because the Claude Code host below must stay up.)

**Why the queue is not in git:** it changes every batch. Committing before each
run is friction that gets bypassed, and bypassed conventions are exactly how the
previous five scripts drifted. Only `queue.example.tsv` is tracked.

## Naming

**`winpc-5600x` is the machine id** — settled 2026-08-29. The CPU is a Ryzen 5
5600X, verified on the box.

**`daedalus` is the hostname**, chosen 2026-10-25. The machine id stays
`winpc-5600x`, the directory name here and in hestia's `archive/`, because the box's
sparse checkout and the transcode pipeline are pinned to `hosts/winpc-5600x`.

The hestia archive directory was renamed `archive/winpc-5800x` → `archive/winpc-5600x`
on 2026-08-29 (metadata-only `mv` — it is a plain directory inside `main/archive`, not
its own dataset; the `_inventory` catalog was renamed to match and no NFS/SMB share
referenced it). Open Question D6 in
[`docs/plans/2026-07-06-hestia-data-organization.md`](../../docs/plans/2026-07-06-hestia-data-organization.md)
is closed.

Note `gauss` in `archive/` is an **old MacBook**, not this box. An ssh key here carries
a `gauss-transcode` comment purely because it was copied from that laptop — it is not a
name for this machine, and it is dead on the GitHub account.

Note `gauss` refers to an **old MacBook** in `archive/`, not this box.

## Claude Code host (WSL2)

Built 2026-10-25 from
[`docs/plans/2026-10-25-winpc-claude-code-plan.md`](../../docs/plans/2026-10-25-winpc-claude-code-plan.md)
(HOMELAB-74 to HOMELAB-81). The agents run in WSL2 Debian 12, never on Windows.

| | |
| :--- | :--- |
| Distro | `Debian`, user `george`, hostname follows Windows (`daedalus`) |
| `/etc/wsl.conf` | automount off, interop off, `appendWindowsPath = false`, systemd on |
| `%UserProfile%\.wslconfig` | `memory=24GB`, `vmIdleTimeout=-1` |
| Keepalive | Windows scheduled task *WSL Debian keepalive (Claude Remote Control)*: at startup, S4U as `george`, runs `wsl.exe -d Debian --exec /bin/sleep infinity` |
| Remote Control | user unit `~/.config/systemd/user/claude-remote.service` (linger on) runs `claude remote-control --name daedalus` in tmux session `remote`, working dir `~/src` |

Look at it with `wsl -d Debian -- tmux attach -t remote`; detach with Ctrl-B D,
never Ctrl-C.

**Reboot test passed 2026-10-25:** after a restart with nobody logged in, the
keepalive task ran 9 s after boot and Remote Control was connected 17 s after boot.

**No Windows drives in Debian.** `/mnt/c` still exists as an empty directory that
WSL recreates as a mount point, but nothing is mounted on it. A terminal opened from
Windows starts in the translated Windows path, which doesn't exist; use
`wsl -d Debian --cd ~`.

Credentials on this box, and how to revoke each, are in the private infra access
log, not here.

## Gotchas

- **Detach long jobs via WMI.** `Start-Process` dies with the SSH session; use
  `Invoke-CimMethod Win32_Process Create` off a `.bat`.
- **PowerShell over `ssh` → `cmd` is quoting hell.** Send scripts as
  `powershell -EncodedCommand <base64 UTF-16LE>`, and set
  `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8` or non-ASCII tags
  come back mangled.
- **Finished transcodes do not accumulate here.** They are pushed to hestia and
  the local copy deleted, so `C:\media-work` holding no `.mkv` is normal — check
  hestia, not this box. The exception is a *failed validation*, which
  deliberately keeps the local copy.
