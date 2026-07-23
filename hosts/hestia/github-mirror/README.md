# github-mirror

Daily off-site-on-hestia backup of **every** `gjcourt` GitHub repo — all branches
and tags — as bare `git clone --mirror` clones under the family tree at
`admin/backups/global/github/`. So if GitHub ever loses a repo (or you do), the
full history is recoverable from hestia with `git clone <mirror>.git`.

## Why hestia, not k8s

github-mirror is a **hestia-data backup job**: the bytes land on hestia's local
disk. Co-locating compute with the destination gives fast local git packfile I/O
and skips an NFS-PV round-trip back to hestia (and the `runAsUser` + export
gymnastics to write the write-protected `admin/backups` tree). The image itself is
storage-agnostic, so the same artifact runs as a k8s CronJob where the target PVC
is cluster-local — see [`k8s-cronjob.example.yaml`](./k8s-cronjob.example.yaml).

## How it works

- `entrypoint.sh` runs `github-mirror.sh` on start (seeds immediately), then every
  `INTERVAL_SECONDS` (default 86400 = daily). A loop, not cron, so it runs cleanly
  as a non-root uid.
- `github-mirror.sh` lists every repo the token owns via the GitHub REST API, then
  for each does `git clone --mirror` (first run) or `git remote update --prune`
  (thereafter) into `/mirror`. `--mirror` captures **all** refs — branches, tags,
  in-flight work — not just the default branch.
- The token is read from a mounted file and passed per-command via
  `http.extraHeader`, so it is **never** persisted into any repo's git config.
- Emits `homelabscope_job_last_success_seconds{job="github-mirror"}` (and friends)
  to the node-exporter textfile collector, so the generic `HomelabscopeJobStale` /
  `HomelabscopeJobMetricAbsent` alerts cover it automatically (48h budget).

## First-time setup (operator, on hestia)

1. **GitHub token.** Create a fine-grained PAT — **Contents: Read-only**, scoped to
   **All repositories** (owner: gjcourt). Save it (a trailing newline is fine —
   it's stripped) into the locked keys area:
   ```
   umask 077
   printf '%s' '<PAT>' | sudo tee /mnt/main/family/admin/keys/github-mirror.token >/dev/null
   sudo chown 1028:1028 /mnt/main/family/admin/keys/github-mirror.token
   sudo chmod 0600     /mnt/main/family/admin/keys/github-mirror.token
   ```
2. **Mirror destination**, george-owned so the container (uid 1028) can write it:
   ```
   sudo install -d -o 1028 -g 100 -m 0750 /mnt/main/family/admin/backups/global/github
   ```
3. **(Optional) metrics.** Ensure the node-exporter textfile dir is writable by uid
   1028, else the staleness metric silently skips (the backup still runs):
   ```
   sudo chgrp 100 /var/lib/node-exporter/textfile && sudo chmod g+w /var/lib/node-exporter/textfile
   ```
4. **Deploy** the compose in this directory as a TrueNAS SCALE **Custom App**.

## Bootstrap → steady state

The compose ships `image: …:latest` as a placeholder. After this PR merges,
`build-github-mirror.yml` publishes the first image; **pin its `@sha256:` digest**
in `docker-compose.yml` in a follow-up PR (like the other hestia apps) and
`deploy-hestia.yml` auto-rolls it. Thereafter, editing `images/github-mirror/**`
rebuilds the image; bump the digest to deploy.

## Restore

```
git clone /mnt/main/family/admin/backups/global/github/<repo>.git <repo>
# or push it back to a fresh GitHub repo:
git clone --mirror <mirror>.git && cd <repo>.git && git push --mirror git@github.com:gjcourt/<repo>.git
```
