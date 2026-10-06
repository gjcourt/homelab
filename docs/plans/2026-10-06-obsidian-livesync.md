---
status: planned
last_modified: 2026-10-06
summary: "Self-hosted Obsidian sync: one CouchDB in the cluster for the LiveSync plugin, one database per vault, E2EE, S3 backups with a restore drill"
---

# Obsidian sync on the homelab (CouchDB + Self-hosted LiveSync)

## Goal

Sync Obsidian vaults between George's Mac, iPhone/iPad and winpc, and give other household members
their own vaults, without Obsidian Sync. George chose this option (2026-10-06) over running Obsidian
in a browser or keeping a vault on a hestia share. Mobile Obsidian can't open a vault from a network
share, and a browser-based Obsidian would need a login the cluster can't give it today.

Every device keeps using the normal Obsidian app plus the community plugin
[Self-hosted LiveSync](https://github.com/vrtmrz/obsidian-livesync). The plugin syncs each vault to
a database on a CouchDB server in the cluster. Devices work offline and catch up when they can reach
the server.

## Design

### One CouchDB, one database per vault

- **One CouchDB server** (`couchdb` app), holding many databases. In CouchDB a database is a
  lightweight unit.
- **One database per vault.** LiveSync syncs a whole vault into a single database, and CouchDB's
  read permission is per database: any member of a database can read every document in it. So
  personal vaults get their own database, and a shared family vault is one database with several
  members.
- **One CouchDB user per person**, listed as a `member` (not `admin`) in the `_security` of each
  database they may use
  ([CouchDB security model](https://docs.couchdb.org/en/stable/intro/security.html)).
- **End-to-end encryption on every vault** (LiveSync's E2EE passphrase, recommended by its
  [settings doc](https://github.com/vrtmrz/obsidian-livesync/blob/main/docs/settings.md)). The
  server only ever stores ciphertext note contents. Filenames stay readable on the server unless
  "Path obfuscation" is also enabled, which is recommended for the shared server. Each vault gets
  its own passphrase.

### Server configuration

- **Image:** official `couchdb`, currently `3.5.2.1`
  ([Docker Hub](https://hub.docker.com/_/couchdb)), pinned by tag and digest.
- **Single node** (`[couchdb] single_node = true`), so no Erlang cookie and no node name.
- **LiveSync's documented `local.ini`**, from its
  [setup guide](https://github.com/vrtmrz/obsidian-livesync/blob/main/docs/setup_own_server.md)
  and mounted from a ConfigMap:
  - `require_valid_user = true`;
  - `max_document_size = 50000000`;
  - `max_http_request_size = 4294967296`;
  - CORS enabled for `app://obsidian.md, capacitor://localhost, http://localhost` with credentials.
- **Admin credentials** come from a SOPS secret (`COUCHDB_USER` / `COUCHDB_PASSWORD`). The repo
  ships `secret-couchdb-admin.yaml.example`; George generates and encrypts the real one.
- **Non-root:** UID/GID 5984 (the image's `couchdb` user), all capabilities dropped. The image
  starts as root and drops privileges itself; running non-root from the start skips its
  ownership fix-ups ([entrypoint](https://github.com/apache/couchdb-docker/blob/main/3.5.2/docker-entrypoint.sh)),
  so `fsGroup: 5984` owns the volume. Writable mounts: the data PVC at `/opt/couchdb/data`, and an
  emptyDir at `/opt/couchdb/etc/local.d`, where the entrypoint writes the admin `docker.ini`. Aim
  for a read-only root filesystem. **Unverified** — no source documents this image running fully
  non-root and read-only, so staging proves it first; if it fails, relax `readOnlyRootFilesystem`
  rather than run as root.
- **Storage:** `truenas-iscsi` PVC (start at 10 Gi; compaction needs ~2× transiently). Default
  automatic compaction (`[smoosh]`) is enough at household scale. Like every iSCSI PVC it's
  covered by `pvc-writeprobe`, which catches the read-only-remount failure mode.
- **Resources:** request 100m / 256Mi, limit 1 CPU / 1Gi. Community reports show OOM at 256 MB
  during a first full sync; measure in staging.
- **Probes:** `/_up` (unauthenticated health endpoint) for readiness, TCP for liveness, per the
  repo's probe convention.

### Access and TLS

- **Hostname:** `obsidian.burntbytes.com`; staging `obsidian.stage.burntbytes.com`. An HTTPRoute
  on the production gateway's `https` listener, using the existing Let's Encrypt wildcard.
  LiveSync on iOS requires a certificate the OS trusts — "Plain HTTP and self-signed certificates
  are not supported" ([troubleshooting](https://github.com/vrtmrz/obsidian-livesync/blob/main/docs/troubleshooting.md)) —
  and the wildcard qualifies.
- **Login:** CouchDB's own (`require_valid_user`). Nothing relies on the Authelia gateway gate,
  which isn't enforcing today (cilium#32793).
- **Network policy:** ingress from the gateway (`host`, `remote-node`, `ingress`) on 5984;
  egress DNS only. The backup job gets its own egress to S3.
- **CouchDB's admin UI** (`/_utils`, Fauxton) is reachable on the same hostname behind the admin
  login. Acceptable on the LAN; see the off-LAN decision below.

### Backups

CNPG's S3 backups only cover Postgres, so CouchDB needs its own:

- **Nightly logical backup CronJob:** dump every vault database with
  [`couchbackup`](https://github.com/IBM/couchbackup) and upload the gzipped dumps to
  `s3://gjcourt-homelab-backup/production/couchdb/`, using the same AWS credentials pattern as the
  other apps. LiveSync stores note data as plain JSON documents, not CouchDB attachments
  ([data structure](https://github.com/vrtmrz/obsidian-livesync/blob/main/docs/datastructure.md)),
  which `couchbackup` handles; its documented limitation is attachments. **Unverified for LiveSync
  data**, hence the restore drill.
- **Restore drill** (exit criterion): restore a staging vault's dump into a fresh database, point
  a test device at it, and confirm notes open with the vault's passphrase.
- Retention: keep 30 days of dailies, matching the other apps.

### Provisioning (George runs it; agents don't create accounts)

`scripts/couchdb/provision-vault.sh <vault-db> <user> [<user>…]` takes the admin credentials from
the environment and:

1. creates the users that don't exist yet (prompting for each password);
2. creates the database;
3. writes its `_security` with those users as members.

Run it once per vault. On each device, LiveSync's setup wizard then connects with that user's
credentials. Known issue: the wizard's "Detect and Fix CouchDB issues" check can return 403 for
non-admin users ([#988](https://github.com/vrtmrz/obsidian-livesync/issues/988)). Provisioning
the database as admin first, as above, avoids depending on it.

## Decisions for George

1. **LAN-only, or reachable from anywhere?**
   - **LAN-only:** phones sync only at home. That's the default for every app, and the simplest.
   - **Through the Cloudflare Tunnel**, like `memos`, `links` and `food`: sync from anywhere.
     CouchDB's own login plus E2EE (and path obfuscation) protect it, but the admin UI would also
     be public behind the admin password.
   - **Recommendation:** start LAN-only in staging and production, then add a tunnel route in a
     separate PR once backups and the restore drill pass.
2. **Which vaults, for whom?** A list like `george-personal` (George), `family` (George + …)
   decides what `provision-vault.sh` creates first.

## Phases

| Phase | PR | Done when |
|---|---|---|
| 1 | `apps/base/couchdb/` plus staging overlay: Deployment, Service, PVC, ConfigMap (`local.ini`), CiliumNetworkPolicy, PDB, HTTPRoute, `secret-couchdb-admin.yaml.example`; runbook `docs/operations/apps/couchdb.md` | Staging pod Ready non-root with no restarts; `/_up` answers; one desktop vault syncs to `obsidian.stage.burntbytes.com` |
| 2 | Production overlay | Same checks in production |
| 3 | Backup CronJob + restore runbook | A dump lands in S3; the restore drill passes in staging |
| 4 | `scripts/couchdb/provision-vault.sh` + device setup guide in the runbook | George provisions the first vaults; Mac, iPhone/iPad and winpc sync with E2EE |
| 5 (optional) | Cloudflare Tunnel route | Off-LAN sync works, if George chooses it |

Every PR gets a babysit before George merges. Secrets are SOPS-encrypted by George (`.yaml.example`
only from agents). No cluster changes outside merged PRs.

## Exit criteria

- [ ] Mac, iPhone/iPad and winpc sync one vault through `obsidian.burntbytes.com` with E2EE on.
- [ ] A second person's vault is readable only by its members (checked with another user's
      credentials: 401/403).
- [ ] A nightly backup lands in S3, and a restore drill has been done once.
- [ ] CouchDB runs non-root with no restarts over a week; memory measured and limits adjusted.
