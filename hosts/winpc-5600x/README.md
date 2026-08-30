# winpc-5600x

Windows 11 Pro desktop. **Hostname `DESKTOP-T91IG8F`**, `10.42.4.195`,
SSH as `george` with an installed ed25519 key.

| | |
| :--- | :--- |
| CPU | AMD Ryzen 5 5600X, 6 cores / 12 threads |
| Roles | CD ripping (EAC + MusicBrainz Picard), Blu-ray ripping (MakeMKV), x265 transcoding |
| Rip outputs | audio `C:\Rips` (flat, tagged FLAC) · video `C:\Video\<Title>\` |
| Work dir | `C:\media-work` (one in-progress encode + logs) |
| Scripts | `C:\media-pipeline` — canonical copy is [`media-pipeline/`](media-pipeline/) here |

## Naming

⚠️ The hestia archive dataset for this machine is `archive/winpc-5800x`, but the
CPU is a **5600X** (verified 2026-08-29). This directory uses the accurate id.
Reconciling the dataset name is Open Question D6 in
[`docs/plans/2026-07-06-hestia-data-organization.md`](../../docs/plans/2026-07-06-hestia-data-organization.md).

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
