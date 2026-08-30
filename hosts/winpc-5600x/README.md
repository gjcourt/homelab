# winpc-5600x

Windows 11 Pro desktop. **Hostname `DESKTOP-T91IG8F`**, `10.42.4.195`,
SSH as `george` with an installed ed25519 key.

| | |
| :--- | :--- |
| CPU | AMD Ryzen 5 5600X, 6 cores / 12 threads |
| Roles | CD ripping (EAC + MusicBrainz Picard), Blu-ray ripping (MakeMKV), x265 transcoding |
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
desktop that sleeps (S3) and reboots, and a push-deploy to a sleeping machine
fails silently rather than loudly.

**Why the queue is not in git:** it changes every batch. Committing before each
run is friction that gets bypassed, and bypassed conventions are exactly how the
previous five scripts drifted. Only `queue.example.tsv` is tracked.

## Naming

**`winpc-5600x` is the machine id** — settled 2026-08-29. The CPU is a Ryzen 5
5600X, verified on the box.

⚠️ The hestia archive dataset is still `archive/winpc-5800x` and needs renaming to
match. That is Open Question D6 in
[`docs/plans/2026-07-06-hestia-data-organization.md`](../../docs/plans/2026-07-06-hestia-data-organization.md),
now decided: rename the dataset, do not rename this directory.

Note `gauss` refers to an **old MacBook** in `archive/`, not this box.

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
