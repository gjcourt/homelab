# music-pull (alcatraz)

Daily off-box copy of the music library: alcatraz **pulls**
`hestia:/mnt/main/family/media/music` into `/volume1/music/library`.

Before this, hestia was the only copy. `main/family` snapshots are on the same
pool, so they cover deletion but not losing the pool — and once the local rips on
winpc are cleared, the discs they came from are back at SFPL.

## Why a pull, and why not root

Same direction as [`immich-photos-pull`](../immich-photos-pull/README.md), for the
same reason: Synology's setuid-root inbound rsync rejects a hestia-initiated push
(root is a disabled DSM account). alcatraz's rsync as the **client** writing its
own filesystem has no such check.

Unlike photos, nothing here has to be re-owned to a DSM account for indexing, so
the job runs as **`truenas-backup`**, not root.

## Behaviour

| | |
|---|---|
| New file on hestia | copied |
| Changed file (a retag rewrites the whole FLAC) | updated; the previous version is moved to `library/.replaced/<run-id>/` first |
| File deleted on hestia | **kept**. There is no `--delete`, ever |

`.replaced/` grows only when files change; prune it by hand if it matters.

## Access

| Where | What |
|---|---|
| alcatraz | key `/volume1/homes/truenas-backup/.ssh/id_ed25519_hestia_music` (600, owned by `truenas-backup`); host key pinned in `known_hosts_hestia`, shared with the photo job |
| hestia | `truenas_admin` `authorized_keys`, pinned to a read-only rrsync of the music root: |

```
command="sudo -n --preserve-env=SSH_ORIGINAL_COMMAND /usr/bin/rrsync -ro /mnt/main/family/media/music",no-agent-forwarding,no-port-forwarding,no-pty,no-X11-forwarding ssh-ed25519 AAAA… truenas-backup@alcatraz-music-pull
```

A leaked key can read music and nothing else. Because rrsync roots the client at
the music directory, the script's source path is **relative** (`./`).

## Install / schedule (DSM)

Script lives at `/volume1/homes/truenas-backup/music-pull/pull-music-from-hestia.sh`
(mode 755); it logs to `pull-music.log` beside it, ending every run with
`=== … END (success, Ns) ===` or `END (FAILED, …)`.

DSM → Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script:

- **User:** `truenas-backup`
- **Schedule:** daily, **06:00** (after the 05:00 photo pull)
- **Run command:** `/volume1/homes/truenas-backup/music-pull/pull-music-from-hestia.sh`

## Verify

Content, not counts. From hestia:

```sh
cd /mnt/main/family/media/music && sudo find . -type f ! -path '*/@eaDir/*' -print0 | sort -z | xargs -0 sha256sum > /tmp/hestia.sha
# on alcatraz, in /volume1/music/library, the same command minus sudo and excluding ./.replaced
diff <(hestia list) <(alcatraz list)   # empty = every file is present and identical
```

The first full copy (2026-09-25, 71 GB) was verified this way before the winpc
rips were deleted.
