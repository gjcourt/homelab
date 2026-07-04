# immich-photos-pull (alcatraz side)

Daily additive **pull** of the Immich photo library from hestia → alcatraz,
run **on alcatraz** by a DSM Task Scheduler job. This is the backup leg that
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
| Runs on | alcatraz (Synology DSM, `10.42.2.11`), as **root**, via Task Scheduler |
| Source (server) | `truenas_admin@10.42.2.10:/mnt/main/family/images/photos/{mara,george}/` |
| Destination (local) | `/volume1/homes/{mara,george}/Photos/` (owned `<uid>:100`; mara=1027, george=1028) |
| Mode | `rsync -a --ignore-existing` (additive; **no `--delete`, ever**) |
| SSH key | `/volume1/homes/truenas-backup/.ssh/id_ed25519_hestia` (mode 600) |
| Schedule | Daily ~05:00 local (after hestia's 04:00 pull) |
| Script (on alcatraz) | `/volume1/homes/truenas-backup/immich-photos-pull/pull-from-hestia.sh` (mode 755) |

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

**A human/assistant with hestia access installs this line — the alcatraz-side
operator does not touch hestia.** On hestia, append the public key from step 1
to `truenas_admin`'s `authorized_keys`, restricted to **read-only rsync of the
photos path** via an `rrsync` forced command:

```
command="rrsync -ro /mnt/main/family/images/photos",no-agent-forwarding,no-port-forwarding,no-pty,no-X11-forwarding ssh-ed25519 AAAA...alcatraz-photos-pull
```

`rrsync -ro` locks this key to read-only rsync rooted at the photos directory —
even if the key leaks it can only pull photos, never write or run a shell.

**If `rrsync` isn't present on TrueNAS SCALE** (it ships with rsync but the
path varies; check `command -v rrsync` and `ls /usr/share/doc/rsync*/support/`):
either

- fall back to an **unrestricted** key line on the trusted LAN and accept the
  trade-off (this key can then run any command as `truenas_admin` — acceptable
  only because the LAN is trusted and the key never leaves alcatraz), or
- drop a tiny forced-command wrapper script that execs `rsync --server
  --sender ...` limited to the photos path and point `command="..."` at it.

Verify from alcatraz once installed:

```bash
rsync -n -a --rsh="ssh -i /volume1/homes/truenas-backup/.ssh/id_ed25519_hestia \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=/volume1/homes/truenas-backup/.ssh/known_hosts_hestia" \
    truenas_admin@10.42.2.10:/mnt/main/family/images/photos/george/ /tmp/pull-test/
```

A dry-run (`-n`) file list with no errors means the key + forced command work.

### 3. Place the script on alcatraz

Copy `pull-from-hestia.sh` from this directory onto alcatraz and make it
executable:

```bash
mkdir -p /volume1/homes/truenas-backup/immich-photos-pull
# copy pull-from-hestia.sh into that dir, then:
chmod 755 /volume1/homes/truenas-backup/immich-photos-pull/pull-from-hestia.sh
```

### 4. DSM Task Scheduler job

DSM Control Panel → **Task Scheduler** → **Create** → **Scheduled Task** →
**User-defined script**.

- **General**: User = **`root`** (required — the script `chown`s the received
  files to each DSM account; only root can). Give it a name like
  `immich-photos-pull`.
- **Schedule**: Daily, ~**05:00** (after hestia's 04:00 alcatraz→hestia pull,
  so the SD-card imports it surfaces are already on hestia).
- **Task Settings**: Run command:
  ```
  /volume1/homes/truenas-backup/immich-photos-pull/pull-from-hestia.sh
  ```
  Tick "Send run details by email" (and set the notification address in
  Control Panel → Notification) so a **non-zero exit** — which the script
  returns if any user's pull or chown fails — lands in your inbox. DSM emails
  task output on non-zero exit only if this is configured.

### 5. First-run validation (the key acceptance test)

Run the task manually (Task Scheduler → select the task → **Run**), then
confirm:

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
