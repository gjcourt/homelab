# immich-photos-pull (alcatraz side)

> **Now a GitOps-managed compose service.** `pull-from-hestia.sh` is baked into
> `ghcr.io/gjcourt/immich-photos-pull` (`images/immich-photos-pull/`) and run by
> a busybox-crond container defined in [`docker-compose.yml`](docker-compose.yml),
> deployed by the alcatraz self-hosted runner
> ([`hosts/alcatraz/actions-runner/`](../actions-runner/README.md)) via
> `.github/workflows/alcatraz-deploy.yaml`. This **supersedes** the manual DSM
> Task Scheduler setup described in the "Operator setup" section below: the SSH
> key + hestia-authorization steps (**1 & 2**) still apply, but steps **3–4**
> (copy the script onto alcatraz + create a Task Scheduler job) are replaced by
> the compose service. See
> [`docs/plans/2026-06-26-alcatraz-gitops-docker.md`](../../../docs/plans/2026-06-26-alcatraz-gitops-docker.md#first-managed-workload-immich-photos-pull).
>
> The script also gained a root-cause fix vs the DSM-task version: it now chowns
> **only newly-transferred files** (numerically) instead of `chown -R` over the
> whole ~289k-file library each run, and an EXIT trap guarantees the
> `END (success, Ns)` log trailer always logs — the trailer the hestia-side
> homelabscope-heartbeat collector keys off. A full-tree chown could exceed the
> runtime cap and get killed before the trailer wrote, silently staling the
> freshness metric.

Daily additive **pull** of the Immich photo library from hestia → alcatraz,
run **on alcatraz** by the `immich-photos-pull` compose container. This is the backup leg that
keeps alcatraz a full second copy of the source-of-truth library on hestia —
in particular it copies back direct-to-hestia SD-card imports (see
`scripts/import-sd-photos.sh`) that alcatraz's phone-upload libraries never
saw, so DSM Photos can index them.

The complementary leg — phone uploads flowing alcatraz → hestia — runs from
hestia (`hosts/hestia/immich-photos-backup/`). Together they converge both
sides to the union, with hestia authoritative and no `--delete` in either
direction.

## Why this direction (not a hestia→alcatraz push)

A hestia-initiated push **cannot work**: Synology's `/bin/rsync` is
setuid-root and, in inbound server mode, authenticates the **real uid**
against the DSM account database. `sudo rsync` → real uid root (a disabled DSM
account) is rejected; `sudo -u mara rsync` → real uid mara (non-admin) is
rejected; only an administrator (e.g. `truenas-backup`) passes the account
check, but an admin that isn't the owning user can't write that user's private
`0700` `homes/<user>/Photos` with correct ownership. No sudoers rule squares
this — it's Synology's inbound-rsync security model.

Reversing the direction sidesteps it: alcatraz's rsync is the **client**
writing to its **own local** filesystem as **local root**, so no inbound
account check applies and local root can `chown` the received files to the
owning DSM user. The rsync **server** runs on hestia (plain Linux, no Synology
setuid patch). Full root cause + decision record:
[`docs/plans/2026-07-04-alcatraz-photos-pull.md`](../../../docs/plans/2026-07-04-alcatraz-photos-pull.md).

| Attribute | Value |
|---|---|
| Runs on | alcatraz (Synology DSM, `10.42.2.11`), as **root**, via the `immich-photos-pull` compose container (busybox crond) |
| Source (server) | `truenas_admin@10.42.2.10:/mnt/main/family/media/photos/{mara,george}/` |
| Destination (local) | `/volume1/homes/{mara,george}/Photos/` (owned `<uid>:100`; mara=1027, george=1028) |
| Mode | `rsync -a --ignore-existing` (additive; **no `--delete`, ever**) |
| SSH key | `/volume1/homes/truenas-backup/.ssh/id_ed25519_hestia` (mode 600) |
| Schedule | Daily ~05:00 local (after hestia's 04:00 pull) |
| Script | baked into `ghcr.io/gjcourt/immich-photos-pull` (source: `images/immich-photos-pull/pull-from-hestia.sh`) |

## Operator setup

### 1. SSH key on alcatraz

Log in to alcatraz over SSH as a DSM admin (or the `truenas-backup` account)
and generate a dedicated key for reaching hestia:

```bash
mkdir -p /volume1/homes/truenas-backup/.ssh
ssh-keygen -t ed25519 -N '' \
    -C 'alcatraz-photos-pull' \
    -f /volume1/homes/truenas-backup/.ssh/id_ed25519_hestia
chmod 700 /volume1/homes/truenas-backup/.ssh
chmod 600 /volume1/homes/truenas-backup/.ssh/id_ed25519_hestia
chmod 644 /volume1/homes/truenas-backup/.ssh/id_ed25519_hestia.pub
# known_hosts is created on first connect by StrictHostKeyChecking=accept-new;
# pre-touch it so the file exists with the right perms:
touch /volume1/homes/truenas-backup/.ssh/known_hosts_hestia
chmod 644 /volume1/homes/truenas-backup/.ssh/known_hosts_hestia
```

Print the public key to hand to whoever installs it on hestia (step 2):

```bash
cat /volume1/homes/truenas-backup/.ssh/id_ed25519_hestia.pub
```

> The key path is a config var at the top of `pull-from-hestia.sh`
> (`SSH_KEY` / `KNOWN_HOSTS`). If you place the key elsewhere, edit those.

### 2. Authorize the key on hestia (done by someone with hestia access — NOT this runbook's DSM operator)

**A human/assistant with hestia access installs this key — the alcatraz-side
operator does not touch hestia.** The public key from step 1 goes into
`truenas_admin`'s authorized keys with a **forced command** that confines it to
read-only rsync of the photos path:

```
command="sudo -n --preserve-env=SSH_ORIGINAL_COMMAND /usr/bin/rrsync -ro /mnt/main/family/media/photos",no-agent-forwarding,no-port-forwarding,no-pty,no-X11-forwarding ssh-ed25519 AAAA...alcatraz-photos-pull
```

Two things about this line are load-bearing:

- **It runs `rrsync` as root (`sudo -n`).** The photo *files* on hestia are
  `0700`/owner-only (mara or george — inherited from the SD card by `rsync -a`).
  `truenas_admin` can traverse the `0755` dirs but cannot read the files, and it
  **cannot** be added to the owning `users` group — TrueNAS refuses
  (`membership of this builtin group may not be altered`). So the read has to be
  as root. `rrsync -ro` still hard-confines it: even as root the key can **only**
  read-only-rsync this one path — no writes, no other files, no shell. Blast
  radius if the key leaks ≈ read access to photos that alcatraz (where the key
  lives) already holds. `--preserve-env=SSH_ORIGINAL_COMMAND` is required so the
  client's rsync command survives `sudo`'s env reset and reaches `rrsync`.
- **Paths are relative to the rrsync root.** `rrsync -ro <root>` prepends
  `<root>` to every client path, so the script requests `mara/` / `george/`
  (see `SRC_HOST` note in `pull-from-hestia.sh`), NOT the absolute
  `/mnt/.../photos/mara/` — an absolute path gets the root prepended twice and
  fails with `change_dir ...photos/mnt/.../photos/mara: No such file`.

**Install it via the TrueNAS middleware, not by editing the file.** TrueNAS
manages `~truenas_admin/.ssh/authorized_keys` from the user's `sshpubkey` field;
a direct file edit is clobbered on the next user update or reboot. Either use the
UI (Credentials → Local Users → truenas_admin → Authorized Keys, append the line
above, preserving existing keys) or the API, reading-appending-writing so the
existing keys are kept:

```bash
# on hestia (truenas_admin has passwordless sudo). Query current sshpubkey,
# append the forced-command line, and write it back via user.update.
sudo midclt call user.query '[["username","=","truenas_admin"]]'   # note the id + current sshpubkey
sudo midclt call user.update <id> '{"sshpubkey": "<existing keys>\n<forced-command line>"}'
```

`sudo` NOPASSWD for `rrsync` must be available to `truenas_admin` — on TrueNAS
its admin account already has passwordless sudo, so no extra sudoers entry is
needed. (`rrsync` ships at `/usr/bin/rrsync` on TrueNAS SCALE.)

Verify from alcatraz once installed — note the **relative** source path:

```bash
rsync -n -a --rsh="ssh -i /volume1/homes/truenas-backup/.ssh/id_ed25519_hestia \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=/volume1/homes/truenas-backup/.ssh/known_hosts_hestia" \
    truenas_admin@10.42.2.10:george/ /tmp/pull-test/
```

A dry-run (`-n`) file list with no errors means the key + forced command work.
A real (non-`-n`) pull of a file should transfer bytes; a push attempt must be
refused with `rrsync error: sending to read-only server is not allowed`.

### 3. Deploy the compose service (replaces the retired manual script + Task Scheduler)

The script is **no longer copied onto alcatraz by hand**, and there is **no DSM
Task Scheduler job**. `pull-from-hestia.sh` is baked into
`ghcr.io/gjcourt/immich-photos-pull` (source of truth:
`images/immich-photos-pull/pull-from-hestia.sh`) and fired by the image's
busybox crond at 05:00 local. It ships as
[`docker-compose.yml`](docker-compose.yml) in this directory and is deployed by
the alcatraz self-hosted runner ([`hosts/alcatraz/actions-runner/`](../actions-runner/README.md))
via `.github/workflows/alcatraz-deploy.yaml`. To roll it out:

1. Pre-`touch /var/log/immich-photos-pull.log` on the alcatraz host (the compose
   bind-mounts this single file; see the compose header).
2. After the first `build-immich-photos-pull.yml` publish, pin the `@sha256`
   digest in `docker-compose.yml` and flip `x-deploy.archived` to `false`; the
   runner then applies it on the next push to master.

### 4. First-run validation (the key acceptance test)

Trigger a run (`docker exec immich-photos-pull /usr/local/bin/pull-from-hestia.sh`
on alcatraz, or wait for the 05:00 crond fire), then confirm:

1. **Files landed with correct ownership** — the main risk of this approach is
   ownership/ACL of rsync-dropped files:
   ```bash
   ls -la /volume1/homes/george/Photos | head
   ls -la /volume1/homes/mara/Photos   | head
   # owner column must be george/mara (uid 1028/1027), group users — NOT root.
   ```
   The tail of `/var/log/immich-photos-pull.log` should show `END (success)`.
2. **DSM Photos indexes the new files** — this is the acceptance test. rsync
   drops files outside DSM's normal upload path, so DSM Photos may not notice
   them until it re-indexes. In DSM **Photos** → user's library, confirm the
   SD-card imports now appear. If they don't, trigger a re-index: DSM Photos →
   Settings → (re-index / rebuild the library), or `synoindex -R
   /volume1/homes/<user>/Photos` from an SSH shell as that user. Treat "does
   DSM Photos surface rsync-dropped files after a re-index" as the go/no-go for
   this whole approach — it's the open risk called out in the plan doc.

## Notes

- **Additive only.** `--ignore-existing` never overwrites an alcatraz copy;
  there is no `--delete`. alcatraz's phone-upload originals always win over
  hestia's copy of the same file.
- **No rsync sudoers on alcatraz.** The retired hestia→alcatraz push needed
  (or would have needed) a `truenas-backup` sudo-rsync rule on alcatraz; the
  pull needs none — the script runs as root locally. See the hestia-side
  README for the (unrelated) chmod sudoers rule that still supports the
  alcatraz→hestia pull.
