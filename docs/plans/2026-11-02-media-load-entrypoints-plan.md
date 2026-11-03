---
status: planned
last_modified: 2026-11-02
summary: "One load.ps1 entry point per media type on daedalus, a generated video queue, and Radarr-correct ownership on new movie dirs"
---

# Repeatable media loads from daedalus — plan

**Scope:** `hosts/winpc-5600x/media-pipeline/`

## Problem

The pipeline scripts exist and are good, but they are not what actually gets run.

- **Audio.** `import-music.ps1` rsyncs with `--chown=george:users --chmod=D755,F644`, sha256-verifies, and
  checks ownership afterwards. On 2026-11-02 two loads (11 albums) were done by hand instead: scp to an
  `_inbox`, then `sudo rsync -a`. The scp-created inbox dirs were `truenas_admin 707`, and `rsync -a`
  copied that onto five existing artist folders (David Bowie, Prince and The Revolution, The Beatles,
  The Clash, The Rolling Stones). Fixed by hand the same day. The script would not have done this.
- **Video.** Every run needs a hand-written queue TSV (main feature + `Extra tNN (M min)` rows), and
  the detach step is improvised each time. `C:\media-work` holds ~80 one-off `launch-*.ps1`,
  `wait-*.ps1`, `probe*.ps1` and `status*.ps1` files from past sessions. That is the evidence that
  there is no single entry point.
- **Video ownership gap.** `transcode.ps1` creates title dirs with `sudo mkdir -p` and only chowns the
  file. 43 of 113 movie dirs are `root:truenas_admin 2755`. Radarr (uid 568, group `apps`) needs group
  write. The other 67 are `truenas_admin:apps 2775`, the intended state.

## Goal

One command per media type, run from the daedalus git checkout, that is safe to re-run and leaves
nothing to improvise:

```powershell
.\load.ps1 music [-DryRun]                       # C:\Rips -> Navidrome library
.\load.ps1 video -Disc 'C:\Video\Looper' -Name 'Looper (2012)' [-DryRun]
.\load.ps1 status                                # what is running, last result of each
```

## Changes

1. **`load.ps1`: a thin entry point, with no pipeline logic of its own.**
   - `git pull --ff-only` the checkout, then refuse to run if the tree is dirty.
   - Refuse to start if another load or encode is running (one lock file).
   - `-DryRun` runs in the foreground. A real run detaches via WMI (`Win32_Process.Create`) and logs
     to `C:\media-work\load-<type>-<stamp>.log`. Today that recipe is copy-pasted from the README.
   - `music` calls `import-music.ps1` unchanged.
   - `video` builds the queue (step 2) and then calls `transcode.ps1 -Queue`.
2. **`build-queue.ps1`: generate the video queue from a MakeMKV disc folder.**
   - Probe every `*.mkv`. The longest title becomes `<Name>` in `movies/<Name>`. The rest become
     `Extra tNN (M min)` in `movies/<Name>/extras`. This matches the 2026-11-01 queues exactly.
   - Titles under a minimum duration (default 60 s) are skipped, with a log line.
   - Writes `C:\media-work\queue-<slug>.tsv` and prints it. `-DryRun` stops there, so the
     main-feature pick can be checked before hours of encoding.
   - `-Name` is required: the `Title (Year)` naming rule (README) is a human decision, not something
     inferred from the disc label (`WAR_GAMES_25AE`).
3. **`transcode.ps1`: fix destination dir ownership.**
   - After `mkdir -p`, set every dir it created to `truenas_admin:apps 2775`. That matches the
     majority and the Radarr requirement in the media docs.
   - Add a post-push check like the one in `import-music.ps1`: fail the title if its dir is not
     group `apps` with group write.
4. **One-time normalize of the 43 existing movie dirs: needs George's OK, run separately.**
   - `chown truenas_admin:apps` + `chmod 2775` on dirs only. Files are not touched.
   - A list-only pass comes first.
5. **Docs.**
   - The README leads with `load.ps1`.
   - The memory/agent notes say: never hand-run scp/rsync for media.
   - The ~80 ad-hoc scripts in `C:\media-work` are left alone (that directory is off-limits for
     cleanup).

## Not changing

- `import-music.ps1` and the transcode encode/validate/ledger logic. Both are proven.
- Navidrome/Jellyfin scanning. The pipeline ends at hestia, and services pick up on their own schedules.

## Verification

- `music -DryRun` against the current `C:\Rips` (Core is already loaded, so it should report 0 new).
- `video -DryRun` on `C:\Video\Looper`: inspect the generated queue.
- One real video run, then check `stat` on the new dir (`truenas_admin:apps 2775`) and Radarr can
  see it.
- Re-run the same load: it reports nothing to do.
