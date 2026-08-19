# Libation — Audible → Audiobookshelf pipeline

Exports the owned Audible library to DRM-free **M4B** on hestia, in the layout
Audiobookshelf expects. The goal is durability: the content survives an Audible
account lapse, a subscription cancellation, or a title being pulled from the
store.

## Shape

```
Libation (docker, hestia)
   └─ scan → liberate → M4B (chapters + cover + metadata embedded)
        └─ /mnt/main/family/media/audiobooks/<Author>/<Title>/
             └─ NFS export → audiobookshelf-audiobooks-pv-prod (ROX)
                  └─ ABS library scan (must be triggered — see below)
```

**One writer, one reader.** The ABS pod mounts the share **read-only** (ROX), so
it cannot write here even in principle. That is why the producer runs host-side
on hestia rather than as a sidecar in the cluster.

## Bootstrap (operator, one time)

### 1. Create the directories

```bash
sudo -n mkdir -p /mnt/main/apps/libation/config
sudo -n chown -R 1028:100 /mnt/main/apps/libation
sudo -n chmod 700 /mnt/main/apps/libation/config    # holds a live Audible token
```

**Do this BEFORE installing the app.** If the directories do not exist when the
container first starts, Docker creates them itself as `root:root` mode 755, the
container cannot write, and it crash-loops on `unable to create database, check
permissions on host`. That is not a subtle failure, but it is an avoidable one.

### 2. Create the Custom App in the SCALE UI (one-time)

**Auto-deploy cannot do the initial create** — same as every other hestia
workload. Do this once by hand:

- Apps → Discover Apps → **Custom App**
- Name: **`libation`** (must match the directory name — `deploy-hestia.yml`
  derives the app name from the parent directory for a plain
  `docker-compose.yml`)
- Paste the contents of `docker-compose.yml` from this directory
- Install

**Do not wait for `Running` here — it will crash-loop, and that is correct.**
With no Audible account configured yet, `liberate.sh` exits 3 on
`No accounts. Exiting.` and Docker restarts it. The app only settles after step
4. Judging the install by container state at this point will look like a broken
deploy when nothing is wrong.

**This create must happen BEFORE the PR merges.** `deploy-hestia.yml` fires on
every push touching `hosts/hestia/**/docker-compose*.yml`, and
`scripts/truenas-update-app.sh` exits `app 'libation' not found on TrueNAS` if
the app does not already exist. Merging first produces a red deploy run.

After this bootstrap, **every change to `docker-compose.yml` on `master`
auto-deploys**: `.github/workflows/deploy-hestia.yml` calls
`scripts/truenas-update-app.sh libation ...` on the self-hosted runner and
applies the new compose over the TrueNAS WebSocket API. This directory has no
`x-deploy.archived` block, so it is in scope for that workflow.

To roll back: revert the commit and merge — auto-deploy applies the old compose.

```bash
# confirm what the entrypoint actually did
ssh truenas_admin@10.42.2.10 'sudo -n docker logs libation 2>&1 | head -20'
```

Watch that first log. It should say **`running every 86400`**. If it instead
says `running once`, `SLEEP_TIME` is not reaching the container and you have an
accidental hot loop — the container will exit after each pass, Docker will
restart it, and it will run again immediately, hammering Audible's API. Stop it
and fix the env before letting it continue.

This was verified against the image on 2026-08-18 by reading `liberate.sh`:

```bash
if [[ -z "${SLEEP_TIME}" ]]; then SLEEP_TIME=-1; fi
while true; do
  run
  if [ "${SLEEP_TIME}" == -1 ]; then break; fi   # default: run once, exit
  sleep "${SLEEP_TIME}"
done
```

So `SLEEP_TIME=86400` is load-bearing, not a tuning knob. The image default of
`-1` combined with `restart: unless-stopped` is the failure mode.

**Ownership: the container runs as `1028:100`, set by `user:` in the compose.**

The image's own baked-in `User` is `1001:1001` — neither the `APP_UID=1654` env
var it also sets, nor a PUID/PGID convention, since `liberate.sh` contains no
PUID/PGID/useradd/chown handling at all and silently ignores those variables.

We override it because `1001` is wrong for *this* deployment. Three uids meet on
the audiobooks dataset:

| Actor | uid:gid | Needs |
|---|---|---|
| existing dataset owner | `1028:100` | — |
| Audiobookshelf pod | `1000:1000` | read (PV is ROX) |
| Libation | `user:` override | **write** |

`/mnt/main/family/media/audiobooks` is `1028:100` mode 755, so a `1001` process
falls through to the `other` bits: it can read and traverse but cannot write. It
would clear the database create, look healthy, and then fail on the first
download. Running Libation as the dataset's existing owner keeps the whole tree
single-owner and touches nothing that Audiobookshelf depends on — ABS reads
through the `other` bits as `1000`, which is already how it reads the existing
library, and Libation's output at default umask (`755`/`644`) stays readable to
it.

Chowning the dataset to `1001` was considered and rejected: it mutates a share
with a second consumer to fix a problem that one compose line fixes. Adding
`group_add: ["100"]` plus `chmod g+w` also works, but leaves correctness resting
on a group-write bit that nothing enforces and any restore or replication drops.

An explicit `user:` has one more benefit: it pins identity against an image bump
silently changing the baked-in `User`, which would otherwise reintroduce exactly
this failure with no diff to point at.

### 3. Write `Settings.json` BEFORE authenticating

Two settings are load-bearing and both must be in place **before** the first
login and the first download. Write the file directly — `LibationCli` cannot be
relied on to set these (`get-setting`/`-override` are documented only against
other settings, and the templates are not shown as settable that way anywhere):

```bash
sudo -n tee /mnt/main/apps/libation/config/Settings.json >/dev/null <<'JSON'
{
  "FolderTemplate": "<first author>/<if series-><first series><has series#-> Vol <series#><-has> - <-if series><title>",
  "FileTemplate": "<title>",
  "TokenStorageMethod": "Plaintext"
}
JSON
sudo -n chown 1028:100 /mnt/main/apps/libation/config/Settings.json
sudo -n chmod 644 /mnt/main/apps/libation/config/Settings.json
```

**`TokenStorageMethod: Plaintext` is mandatory, not a preference.** Libation
defaults to `Encrypted`, which requires an OS secret store to hold the key. A
container has none, so it falls back to a "last-resort" key written to
`/config-internal` — which is ephemeral. Every container recreate mints a new key
and orphans the tokens encrypted under the previous one, producing:

```
Encrypted authentication tokens in AccountsSettings.json could not be decrypted
on this machine.  Underlying error: Failed to decrypt ExistingAccessToken.
```

`export-master-key` cannot help *from inside the container* — it fails with
`Cannot export the encryption master key because the OS secret store is
unavailable`.

To be precise about what is and isn't possible: `liberate.sh` does have an
`init_master_key()` that honours `LIBATION_MASTER_KEY`, `LIBATION_MASTER_KEY_FILE`,
and a `/config/libation-master.key` copied in on start — the same mechanism used
for `AccountsSettings.json`. So encrypted tokens *can* be made to survive, but
only with a key exported from a Libation install that has an OS secret store
(i.e. a desktop install), which this deployment does not have.

Plaintext is therefore a scoped trade-off, not a forced move — and it is chosen
deliberately, because keeping the decryption key in the same directory as the
file it decrypts is not a meaningful security gain over a `chmod 700` directory
on the same pool. If a desktop Libation ever enters the picture, revisit.

**Template syntax has two traps, both of which silently corrupt directory
names.** Conditional tags close by repeating the full tag name:

```
<if series->...<-if series>      correct
<if series->...<-if>             WRONG — unmatched
```

An unmatched closer does not error. For non-series books it swallows the rest of
the template (every book collapses into `<Author>/` with no title folder); for
series books it renders `<-if>` **literally into the folder name on disk**.
Likewise `<series#>` is empty for series that have no number, so it must be
guarded by `<has series#-> ... <-has>` or you get `Series Vol  - Title` with a
doubled space.

Verify what Libation actually parsed before continuing:

```bash
sudo -n docker run --rm --user 1028:100 \
  -v /mnt/main/apps/libation/config:/config \
  -v /mnt/main/family/media/audiobooks:/data \
  rmcrackan/libation:13.7.8 \
  bash -c '/libation/LibationCli get-setting -b | grep -iE "Template|TokenStorage"'
```

### 4. Authenticate to Audible — one-off container, not `docker exec`

Audible login needs a browser round-trip, so it is a human step. It **cannot** be
done with `docker exec` against the running app: pre-authentication the container
exits with `No accounts. Exiting.` (exit 3) and Docker restarts it on a backoff,
so there is never a running container to exec into. Use a one-off container
instead — the entrypoint runs its full config setup and then execs whatever
command it is given:

```bash
sudo -n docker run --rm -it --user 1028:100 \
  -v /mnt/main/apps/libation/config:/config \
  -v /mnt/main/family/media/audiobooks:/data \
  rmcrackan/libation:13.7.8 \
  bash -c 'set -e
    /libation/LibationCli login-external \
      -a "<audible-account-email>" -l us \
      -o TokenStorageMethod="Plaintext"
    cp -v /config-internal/AccountsSettings.json /config/
    chmod 600 /config/AccountsSettings.json
    ls -l /config/AccountsSettings.json
    /libation/LibationCli list-accounts'
```

Notes, each of which was a failed attempt first:

- The binary is `/libation/LibationCli`, not `libationcli`.
- The verb is `login-external`. There is **no** `account add` verb in 13.7.8.
- It prints a URL; sign in in a browser and paste the final address-bar URL back.
  The browser session determines which account is bound — the `-a` flag only
  names it. Use a private window if the browser is signed into another account.
- **The copy-back is required.** `LibationCli` writes `AccountsSettings.json` into
  `/config-internal`, which is ephemeral and regenerated from `/config` on every
  start. Only the database is symlinked out. Without the `cp` the login is lost
  on the next container recreate.

### 5. First bulk run

Once an account exists, the app container clears `No accounts` on its next
restart and begins downloading unattended. Nothing else is needed.

**Every book must end up in its own folder.** Audiobookshelf identifies a book by
the folder containing audio files, so two loose `.m4b` files in one author folder
are ingested as *one book with two tracks*. The ABS docs call this out explicitly,
and the remedy is expensive — remove the item in the UI and rescan, losing
listening progress. The `FolderTemplate` above is what prevents it; verify before
letting ABS scan:

```bash
# must be 0 — any audio file sitting directly in an author folder is wrong
find /mnt/main/family/media/audiobooks -mindepth 2 -maxdepth 2 \
     \( -name '*.m4b' -o -name '*.mp3' \) | wc -l
```

PDFs must live inside the book's own folder too, or ABS ignores them.

Libation 13.7.8 ships an `upload` verb that pushes liberated books to
Audiobookshelf over its API. It is deliberately unused: ABS reads this share
directly, so uploading would write a second copy into ABS's own storage.

*(A `docker` verification step existed here previously; there is no `ffprobe`
in this image — see "Verifying it worked".)*

**Repairing a bad layout does not require re-downloading.** Moving files on disk
does not change `BookStatus` in the database, and `liberate` only processes books
marked `NotLiberated` — so a layout fix is a `mv` loop, not a re-download. Do not
reach for `set-status -d` to "re-detect" moved files: it matches against the path
recorded at download time, not the current template, so it will not find them and
you will conclude wrongly that the files must be re-fetched.

### 6. Trigger the ABS scan — it will NOT self-detect

Same gotcha as the Immich photo pipeline: files appearing on the share do not
reliably wake the consumer. After a liberate pass:

```bash
curl -X POST "https://audiobooks.burntbytes.com/api/libraries/<LIBRARY_ID>/scan" \
     -H "Authorization: Bearer <ABS_API_TOKEN>"
```

Get `<LIBRARY_ID>` from the ABS UI URL when the library is open; mint the token
in ABS under **Settings → Users → your user → API Token**. Once verified by
hand, fold this into a cron on hestia so the pipeline is genuinely
end-to-end — until then it is "automated download, manual publish".

## Verifying it worked

```bash
D=/mnt/main/family/media/audiobooks
ls "$D" | head                                    # authors appear
find "$D" -name '*.m4b' | wc -l                   # book count
find "$D" -mindepth 1 -maxdepth 2 \( -name '*.m4b' -o -name '*.mp3' \) | wc -l  # MUST be 0 (step 5)
find "$D" -mindepth 2 -maxdepth 2 -type d -name '*<-if>*' # MUST be empty (template leak)
```

Chapters are the thing most likely to be silently wrong, and the thing you'll
miss most. There is **no `ffprobe` on hestia and none in the Libation image**, so
check with a throwaway container:

```bash
sudo -n docker run --rm -v "$D":/data:ro \
  --entrypoint /usr/local/bin/ffprobe linuxserver/ffmpeg:latest \
  -v error -show_chapters -show_streams -print_format json \
  "/data/<author>/<title>/<title>.m4b" | head -60
```

`-show_streams` is required: `-show_chapters` alone emits only the chapters
array and will not show the audio or cover-art streams described below. The
mount is read-only because that container runs as root.

A good file shows named chapters (`"title": "1. Effectiveness Can Be Learned"`),
an `aac` audio stream, and an `mjpeg` stream — that last one is the embedded
cover art.

## Operational notes

**The config directory is a credential store.** `/mnt/main/apps/libation/config`
holds a live Audible auth token, and by design (see step 3) it is **unencrypted**.
It is `chmod 700`, owned by `1028:100`, and never enters this repo. Treat a leak
the same as an account compromise: change the Amazon password, then re-run the
`login-external` step.

**`main/apps` is snapshotted — it was not before this app existed.** The Libation
database (`LibationContext.db`) is the record of which books have been downloaded.
Lose it and Libation re-downloads the entire library, because it matches on the
path stored at download time and cannot re-detect existing files. `main/apps` had
**no** periodic snapshot task while `main/family` and `main/homes` did; a daily
task with 14-day retention was added (TrueNAS task id 10, mirroring `main/family`).
That covers every other app config on that dataset too. This lives in TrueNAS
config, not in this repo, so it will not appear in any diff — if the dataset is
ever rebuilt, recreate the task.

**Storage is not a constraint.** ~16T free on `main` against a <100-title
library. The `audiobooks` PV is provisioned 1Ti.

**Renovate.** The image is digest-pinned, so bumps arrive as PRs like any other
workload. Libation tracks Audible's API, which changes without notice —
if `liberate` starts failing, check for a newer release before debugging
locally.

## Why this tool

Libation writes M4B with chapters, cover art and metadata already embedded.
Alternatives that produce bare MP3s force a post-processing stage to rebuild
chapter markers, which is both lossy and tedious. Choosing the tool whose output
already matches the consumer's expected format removes an entire pipeline stage.
