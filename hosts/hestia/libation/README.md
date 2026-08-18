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
sudo mkdir -p /mnt/main/apps/libation/config
sudo chown -R 1001:1001 /mnt/main/apps/libation
sudo chmod 700 /mnt/main/apps/libation/config    # holds a live Audible token
```

### 2. Create the Custom App in the SCALE UI (one-time)

**Auto-deploy cannot do the initial create** — same as every other hestia
workload. Do this once by hand:

- Apps → Discover Apps → **Custom App**
- Name: **`libation`** (must match the directory name — `deploy-hestia.yml`
  derives the app name from the parent directory for a plain
  `docker-compose.yml`)
- Paste the contents of `docker-compose.yml` from this directory
- Install, and wait for it to reach **Running**

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

**Ownership: the container runs as `1001:1001`.** That is the image's own
`User`, and it is neither the `APP_UID=1654` env var the image also sets nor a
PUID/PGID convention — `liberate.sh` contains no PUID/PGID/useradd/chown
handling at all, so those variables are silently ignored. If the host
directories are owned by anything else, the container starts normally and
simply cannot write, which is a slow and confusing way to discover the problem.

### 3. Authenticate to Audible — interactive, cannot be automated

Audible login needs the account password **and an OTP**, so this is deliberately
a human step and is not scripted anywhere in this repo.

```bash
docker exec -it libation libationcli account add
```

It is one-time. Libation persists the token under `/config`, and every run after
this is unattended. If the token is ever invalidated (password change, Amazon
security event), re-run exactly this command — nothing else needs touching.

### 4. Set the naming template — do this BEFORE the first bulk run

Audiobookshelf infers author, title and series **from the directory layout**.
Target:

```
/mnt/main/family/media/audiobooks/
  └─ Malcolm Gladwell/
      └─ Outliers/
          └─ Outliers.m4b
```

and for series:

```
  └─ Author Name/
      └─ Series Name Vol 2 - Title/
          └─ Title.m4b
```

Set Libation's folder and file templates to produce that before liberating
anything. **Retrofitting the layout across a whole library afterwards is
genuinely painful** — ABS caches its inferred metadata, so a later rename means
re-matching every book by hand.

### 5. First bulk run

```bash
docker exec libation libationcli scan        # refresh the library list
docker exec libation libationcli liberate    # download + decrypt everything
```

<100 titles — expect a single evening, not a multi-day backfill. No throttling
configured, and none needed at this size.

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
ls /mnt/main/family/media/audiobooks | head          # authors appear
find /mnt/main/family/media/audiobooks -name '*.m4b' | wc -l
ffprobe -v error -show_chapters <a-file>.m4b | head  # chapters survived
```

Chapters are the thing most likely to be silently wrong, and the thing you'll
miss most. Check one file explicitly rather than assuming.

## Operational notes

**The config directory is a credential store.** `/mnt/main/apps/libation/config`
holds a live Audible auth token. It is `chmod 700`, owned by `1001:1001`, and
never enters this repo. Treat a leak the same as an account compromise: change
the Amazon password, then re-run `account add`.

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
