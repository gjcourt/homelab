# CouchDB (Obsidian sync)

## 1. Overview
CouchDB for the Obsidian community plugin
[Self-hosted LiveSync](https://github.com/vrtmrz/obsidian-livesync): every device keeps using the
normal Obsidian app and syncs each vault to its own database here. Plan and decisions:
[`docs/plans/2026-10-06-obsidian-livesync.md`](../../plans/2026-10-06-obsidian-livesync.md).
LAN-only by decision (2026-10-06); devices sync at home and work offline elsewhere.

## 2. Architecture
- **Image**: official `couchdb` (3.5.2.1), single node, pinned by tag and digest.
- **Namespaces**: `couchdb-stage` now; `couchdb-prod` in phase 2.
- **Storage**: `couchdb-data-pvc` (`truenas-iscsi`, 10 Gi) at `/opt/couchdb/data`, covered by
  `pvc-writeprobe`. Compaction is CouchDB's default automatic compaction (`[smoosh]`) and needs
  about 2× the live size transiently.
- **Config**: `apps/base/couchdb/livesync.ini` (ConfigMap, mounted as
  `/opt/couchdb/etc/local.d/10-livesync.ini`) holds LiveSync's required settings:
  `require_valid_user`, document and request size limits, and CORS for the Obsidian apps. The
  entrypoint writes the admin user into `local.d/docker.ini` (an emptyDir) on every start from the
  `couchdb-admin` secret.
- **Security**: UID/GID 5984, read-only root filesystem, all capabilities dropped. Writable:
  the PVC, `local.d`, `/tmp` (`HOME`, for the Erlang cookie) and `/opt/couchdb/var/log`.
- **Networking**: ClusterIP on 5984, HTTPRoute on the LAN gateway (Let's Encrypt wildcard; iOS
  LiveSync requires a trusted certificate). Egress is DNS only.
- **Access control**: CouchDB's own login. One database per vault; each person is a `member` of
  only their vaults' `_security`.

## 3. URLs
- **Staging**: https://obsidian.stage.burntbytes.com
- **Production**: https://obsidian.burntbytes.com (phase 2)
- Admin UI (Fauxton): `/_utils` on the same host, with the admin credentials.

## 4. Configuration
- **Admin credentials**: `secret-couchdb-admin.yaml` (SOPS). Template:
  `secret-couchdb-admin.yaml.example`. A change takes effect on the next pod restart.
- **Vaults and users**: created by George with `scripts/couchdb/provision-vault.sh` (phase 4), never
  by agents. First vaults: `george`, `mara`, `family`.

## 5. Device setup (phase 4)
1. Install **Self-hosted LiveSync** from Obsidian's community plugins.
2. Run its setup wizard: URI `https://obsidian.burntbytes.com`, the vault's database name, and
   that person's CouchDB username and password.
3. Turn on **end-to-end encryption** with the vault's passphrase. Also turn on **path
   obfuscation**. Every device on that vault uses the same passphrase.
4. Known issue: the wizard's "Detect and Fix CouchDB issues" check can return 403 for non-admin
   users ([#988](https://github.com/vrtmrz/obsidian-livesync/issues/988)). The database already
   exists (provisioned as admin), so this check can be skipped.

## 6. Testing
- `kubectl -n couchdb-stage get pods` — Ready, no restarts.
- Health without credentials: `curl -s https://obsidian.stage.burntbytes.com/_up` → `{"status":"ok"…}`.
- Login required: `curl -s -o /dev/null -w '%{http_code}' https://obsidian.stage.burntbytes.com/_all_dbs` → `401`.

## 7. Disaster recovery
Phase 3 adds a nightly logical backup to `s3://gjcourt-homelab-backup/production/couchdb/` and a
restore runbook. Until then, the data exists only on the PVC and on each synced device. LiveSync
can rebuild a server database from a device ("Rebuild everything" / overwrite remote).

## 8. Troubleshooting
- **CrashLoop at start, writing a file**: the log names the path. Add an emptyDir for it rather
  than dropping `readOnlyRootFilesystem` or running as root.
- **Read-only file system errors on `/opt/couchdb/data`**: the iSCSI read-only remount; see
  "Recovering read-only iSCSI volumes" in `AGENTS.md`.
- **Mobile can't connect**: confirm HTTPS works from the phone's browser (trusted cert), and that
  the CORS origins in `livesync.ini` include `capacitor://localhost`.
