---
status: in-progress
last_modified: 2026-07-04
summary: "Retire the impossible hestia→alcatraz rsync push-back; alcatraz pulls from hestia as local root via a DSM Task Scheduler job"
---

# Alcatraz pulls photos from hestia (retire the push-back)

## Problem

hestia (`main/family/images/photos`) is the source of truth for the photo
library. Two flows keep alcatraz and hestia in sync:

1. **alcatraz → hestia** (phone uploads): the hestia-side container
   `images/immich-photos-backup/` pulls each user's `homes/<user>/Photos/`
   additively (no `--delete`). This works and stays.
2. **hestia → alcatraz** (backfill): so alcatraz stays a full second copy and
   DSM Photos indexes direct-to-hestia **SD-card imports** (see
   `scripts/import-sd-photos.sh`), which never passed through alcatraz.

Flow 2 was implemented as a **push** from the hestia container — a second
rsync per user with `--ignore-existing --chown=<user>:users
--rsync-path="sudo -n rsync"`. It never worked, and it **cannot** work.

## Root cause (proven)

Synology's `/bin/rsync` is **setuid-root**. In inbound server mode it
authenticates the **real uid** of the process against the DSM account
database. A hestia-initiated push has no working invocation:

| Push invocation on alcatraz | Real uid | Result |
|---|---|---|
| `sudo rsync` (`--rsync-path="sudo -n rsync"`) | root | **Rejected** — root is a disabled DSM account ("user has disabled/expired" / "rsync service is no running"); 0 bytes |
| `sudo -u mara rsync` | mara (1027) | **Rejected** — non-admin; the check gates on the administrators group |
| bare `truenas-backup` (admin) rsync | truenas-backup | **Passes** the account check — but an admin that isn't the owning user **can't write** mara's private `0700` `homes/mara/Photos` with correct ownership |

No sudoers rule squares this — it is Synology's inbound-rsync security model,
not a permissions misconfiguration. (The **outbound** direction is unaffected:
`truenas-backup` reading its own homes for the flow-1 pull is fine, modulo the
separate `0700`-upload chmod fix already documented in the hestia runbook.)

## Decision

**Reverse the direction: alcatraz PULLS from hestia** (Option 1).

- alcatraz's rsync is the **client**, writing to its **own local**
  filesystem as **local root** → no inbound DSM account check applies, and
  local root can `chown` received files to the owning DSM user.
- The rsync **server** runs on **hestia** — plain Linux, no Synology setuid
  patch, no account gate.

The hestia-side push-back leg is removed from
`images/immich-photos-backup/immich-photos-backup.sh` (a code comment plus this
plan warn against re-adding it).

## Architecture

```
alcatraz (DSM Task Scheduler, runs as ROOT, ~05:00 daily)
  └─ pull-from-hestia.sh
       for user in mara(1027) george(1028):
         rsync -a --ignore-existing \
           --exclude=@eaDir,.DS_Store,Thumbs.db \
           --rsh="ssh -T -x -i <key> -c chacha20-poly1305 -o Compression=no \
                  -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=<kh>" \
           truenas_admin@10.42.2.10:/mnt/main/family/images/photos/<user>/ \
           /volume1/homes/<user>/Photos/
         chown -R <uid>:100 /volume1/homes/<user>/Photos/   # runs as root
```

- **Additive only.** `--ignore-existing` never overwrites alcatraz's
  phone-upload originals; **no `--delete`, ever**.
- **Runs as root** so the post-rsync `chown` can re-own the received tree to
  the DSM account (rsync-received files land root-owned; DSM Photos only
  indexes files owned by the account).
- **Key restriction.** The `truenas_admin@hestia` `authorized_keys` line pins
  this key to read-only rsync of the photos path (`rrsync -ro`), so a leak
  can only read photos.
- Runs at ~05:00, after hestia's 04:00 alcatraz→hestia pull, so the SD-card
  imports it surfaces are already on hestia.

Repo artifacts:
- `hosts/alcatraz/immich-photos-pull/pull-from-hestia.sh` — the pull script.
- `hosts/alcatraz/immich-photos-pull/README.md` — operator runbook.
- `images/immich-photos-backup/immich-photos-backup.sh` — push-back leg removed.
- `hosts/hestia/immich-photos-backup/README.md` — limitation documented.

## Operator steps (summary — full detail in the alcatraz runbook)

1. **On alcatraz** (DSM admin / `truenas-backup`): `ssh-keygen` an
   `id_ed25519_hestia` key (mode 600) under the path the script expects; place
   `pull-from-hestia.sh` at
   `/volume1/homes/truenas-backup/immich-photos-pull/` (mode 755).
2. **On hestia** (someone with hestia access — not the DSM operator): install
   the public key into `truenas_admin`'s `authorized_keys` with the
   `rrsync -ro /mnt/main/family/images/photos` forced command.
3. **On alcatraz**: create a DSM Task Scheduler user-defined script, **run as
   `root`**, daily ~05:00, invoking the script.
4. **Validate**: run the task manually; confirm files land under
   `/volume1/homes/<user>/Photos` owned by `<uid>:users`; then confirm DSM
   Photos indexes them (re-index if needed).

## Open validation risk

**Does DSM Photos surface rsync-dropped files?** rsync writes files outside
DSM's normal upload path, so ownership/ACL/indexing behavior for those files is
the main unknown. The go/no-go acceptance test is step 4: after a manual run,
the SD-card imports must appear in each user's DSM Photos library (possibly
after a `synoindex -R` / library re-index). If they don't index cleanly, revisit
whether a `synoindex` trigger belongs in the script, or whether a different
backfill mechanism is needed.
