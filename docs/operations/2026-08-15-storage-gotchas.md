---
title: Storage gotchas — PVCs, PVs, ZFS, TrueNAS, Synology
status: Stable
created: 2026-08-15
updated: 2026-08-15
updated_by: gjcourt
tags: [operations, storage, pvc, zfs, truenas, synology, gotchas]
---

# Storage gotchas — PVCs, PVs, ZFS, TrueNAS, Synology

Everything that has bitten us on persistent storage: immutable fields, destructive migrations, reclaim policies, and NAS-side surprises. Several of these are data-loss-adjacent — read before destroying anything. Promoted 2026-08-15.

---

## audit pvc before lossy destroy

**Before destroying a PVC as 'lossy', exec into the running pod and `ls -la $mountpath` + `du -sh`. App names like 'adguard-config' and 'navidrome-data' SOUND lossy but hold sqlite DBs, sessions, automations — destroying them wipes user-visible state.**

The "lossy vs preserve" classification of small Deploy/STS PVCs in the alcatraz→hestia migration was wrong for almost everything I called lossy. Real per-pod content audit on 2026-05-23:

| PVC | Bytes | Content found | Actually lossy? |
|---|---|---|---|
| audiobookshelf-data-pvc | 476K | absdatabase.sqlite (library state, progress, users) | **NO — preserve** |
| audiobookshelf-meta-data-pvc | 68K | empty backups/cache/logs/streams dirs | yes |
| authelia-data | 340K | db.sqlite3 (sessions, MFA), configuration.yaml, users.yml | **NO — preserve** |
| homeassistant-config-pvc | 107M | .storage/, automations.yaml — ALL automations + integrations | **NO — critical** |
| jellyfin-cache-pvc | 25M | transcodes/omdb cache | yes |
| jellyfin-config-pvc | 364M | watch progress, library DB, users, plugins | **NO — preserve** |
| linkding-data-pvc | 48K | empty (DB is in CNPG postgres) | yes |
| mealie-data-pvc | 29M | mealie.db — RECIPES + log files | **NO — preserve** |
| memos-data-pvc | 0K | empty (DB is in CNPG postgres) | yes |
| navidrome-data-pvc | 9.3M | navidrome.db (play history, playlists, users) | **NO — preserve** |
| snapcast-spotify-state | 20K | state.json | yes (regen on first play) |
| adguard config/work (STS VCT) | ~10K | AdGuardHome.yaml — all blocklists, clients, rewrites | **NO — wiping = household DNS down** |

Bigger insight: **adguard-prod config PVC was on the original "lossy" list and got destroyed → household DNS went down**. Saved by old PV being on `Retain` policy — restored via rsync from `pvc-2d7d6f58...` (see [[pv-retain-recovery-pattern]]).

**Why:** I trusted an earlier classification ("lossy" tag from migration plan) without verifying actual contents. The plan said "data PVC" but small data PVCs in tiny apps often hold their entire sqlite + configuration.

**How to apply:**

Before any `kubectl delete pvc` for a "lossy" cutover:

```bash
# 1. Find a running pod that mounts the PVC
pod=$(kubectl get pods -n $NS -o json | python3 -c "
import json,sys
d=json.load(sys.stdin)
for p in d['items']:
    for v in p['spec'].get('volumes',[]):
        if v.get('persistentVolumeClaim',{}).get('claimName')=='$PVC':
            for c in p['spec']['containers']:
                for m in c.get('volumeMounts',[]):
                    if m['name']==v['name']:
                        print(p['metadata']['name'], c['name'], m['mountPath'])
                        sys.exit(0)
")

# 2. Look at sizes + file types
kubectl exec -n $NS $POD -c $CONTAINER -- sh -c "du -sh $MOUNT; ls -la $MOUNT" 

# 3. RED FLAGS for "lossy":
#    - any .sqlite / .db file > 0 bytes (user data)
#    - .yaml/.json config (state)
#    - .storage/ directory (Home Assistant)
#    - files >1MB total
#    - mtime within last 7 days (active use)
```

**Heuristic:** "X-cache" or "X-transcodes" or "X-state" PVCs are usually lossy. "X-data" or "X-config" or just "X" without a qualifier are usually preserve. Empty dirs (0K) are obviously lossy.

**For staging vs prod:** staging is fine to lose. **Prod requires per-PVC verification** even if staging was wiped — production data has different blast radius.

**Related cross-references:**
- [[flux-suspend-during-cluster-ops]] — suspend Flux first so the workload doesn't re-grab the PVC
- [[pvc-storage-class-migration]] — destroy-and-recreate pattern (assumes you've correctly classified as lossy)
- [[pv-retain-recovery-pattern]] — if you destroy and regret it, Retain policy is your safety net

---

## pvc storage class migration

**PVCs are immutable on storageClassName. Migrating across storage classes requires destroy-and-recreate, with different patterns for StatefulSet templates vs standalone Deployment PVCs.**

`spec.storageClassName` is one of the immutable PVC fields. Kustomize/Helm/Flux can't change it on an existing PVC — they patch and silently fail. To actually migrate, you have to delete the PVC and let Flux recreate it on the new class.

**Why:** Learned this the hard way across the alcatraz → hestia migration (PRs #742, #747, #753, #754). A manifest spec change alone is a no-op for already-bound PVCs; the cluster stays on the old SC and Flux logs immutable-field warnings forever.

**How to apply:**

| PVC source | Migration mechanics |
|---|---|
| **Standalone PVC** (referenced by Deployment) | Scale workload to 0 → delete PVC → reconcile Flux → new empty PVC on new SC → scale up. |
| **StatefulSet `volumeClaimTemplates`** (Loki, Adguard) | STS templates are also immutable. Scale STS to 0 → `kubectl delete sts X --cascade=orphan` → delete PVCs → reconcile (Helm/Flux recreates STS) → new STS with new template + fresh PVCs. |
| **CNPG cluster PVCs** | Operator-managed: edit `spec.storage.storageClass` in the Cluster CR, then `kubectl cnpg destroy <cluster> <id>` one replica at a time. CNPG re-bootstraps via pg_basebackup from primary. |

**Gotchas:**
- **Flux gets stuck on the immutable-field error and stops reconciling downstream resources** — must clear ALL stuck PVCs in one batch, not one at a time, or Flux blocks itself.
- **Terminating PVCs hang when a Pending pod still references them.** Force-delete Pending pods (`kubectl delete pod X --force --grace-period=0`) to clear the finalizer chain.
- **Lossy migrations wipe app state** — adguard ends up in "first launch" mode, crashlooping on probes that check the production port instead of the wizard port 3000. Either scale to 0 until you reconfigure, or use [[cnpg-promote-pg-resetwal]]-style operator-only recovery if the manifest defaults insist on a running pod.

For data-preserving migrations across storage classes, use `kubectl pv-migrate` with a temp PVC (orig → tmp on new SC → delete orig → recreate empty on new SC → tmp → orig). PVC names can't be renamed, so this 2-step copy is unavoidable when you want to keep the original PVC name.

---

## pv retain recovery pattern

**When a PV has `persistentVolumeReclaimPolicy: Retain` and goes to phase=Released after PVC deletion, the underlying volume is still intact. Recover by clearing claimRef + creating a new PVC with volumeName pointing to the old PV.**

If a "lossy" PVC destroy turned out to wipe real data (see [[audit-pvc-before-lossy-destroy]]), and the old PV had `Retain` reclaim policy, the data is still on the storage backend. Recovery is mechanical.

**Why:** Burned and recovered from this on 2026-05-23 destroying adguard-prod config PVC. Old PV `pvc-2d7d6f58-6293-4a0f-8e29-d13ddf47ea6e` was phase=Released with Retain, underlying Synology iSCSI LUN still had AdGuardHome.yaml. Without that backstop, the household DNS resolver would have needed a full re-setup.

**How to apply:**

```bash
# 1. Find the old Released PV
kubectl get pv -o json | python3 -c "
import json,sys
d = json.load(sys.stdin)
for pv in d['items']:
    cr = pv['spec'].get('claimRef', {})
    if cr.get('namespace')=='$NS' and cr.get('name')=='$OLD_PVC_NAME':
        print(pv['metadata']['name'], pv['status']['phase'], pv['spec']['persistentVolumeReclaimPolicy'])
"

# 2. Clear claimRef so it becomes Available
kubectl patch pv $OLD_PV_NAME --type=merge -p '{"spec":{"claimRef":null}}'

# 3. Create a temp PVC bound to the old PV
cat <<YAML | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${OLD_PVC_NAME}-recovery-source
  namespace: $NS
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: $OLD_STORAGE_CLASS   # e.g. synology-iscsi (must match the PV's class)
  resources:
    requests:
      storage: $SIZE
  volumeName: $OLD_PV_NAME              # binds to specific PV
YAML

# 4. Scale workload to 0 (release the new PVC)

# 5. Run a Job that mounts BOTH old + new PVCs, rsync/cp old -> new
#    (use a permissive image like alpine + rsync, OR the same app image
#     to avoid ImagePullBackOff if cluster DNS is impaired)

# 6. Scale workload back up
# 7. Clean up: delete the recovery-source PVC. Old PV goes back to Released (or Available depending on reclaim).
```

**Notes:**
- Must match the PV's `storageClassName` exactly when creating the temp PVC, or binding fails silently.
- If cluster DNS is impaired (e.g., the workload you destroyed WAS the DNS resolver), pick a container image that's already cached on the nodes — busybox/alpine pulls will fail. Adguard's own image worked because nodes already had it.
- The PV's underlying volume (iSCSI LUN / NFS export / ZFS dataset) must not have been deleted by the storage operator. With Retain, that won't happen automatically, but if you've explicitly issued a `synology-csi`/`democratic-csi` delete or rm'd the dataset, this trick won't help.

**Related cross-references:**
- [[audit-pvc-before-lossy-destroy]] — the upstream cause; verify before destroying so this recovery is rare
- [[pvc-storage-class-migration]] — the migration pattern this protects against

---

## immutable pv staging preview wedge

**Changing an immutable PV field (nfs.path/server) in a homelab base/overlay hits the STAGING preview at PR-open (not merge) and wedges the whole apps-staging Kustomization until the -staging PVs are recreated.**

Homelab `staging` is an auto-preview env rebuilt by CI (`staging-deploy.yaml`) from `master + open PRs` the moment a PR's checks go green — so a manifest change lands on the LIVE staging cluster **before merge**.

**Why:** if that change edits an **immutable PV field** (`spec.nfs.path`, `spec.nfs.server`, any `persistentvolumesource`), Flux's server-side apply is rejected (`spec.persistentvolumesource is immutable after creation`). Because `apps-staging` is ONE Kustomization over ~24 apps with no `dependsOn`, the failed PV apply drives the whole thing `Ready=False`, retrying every 1m, blocking every other staging app from converging — triggered by merely OPENING the PR. `kustomize build` CI can't catch it (client-side render; immutability is admission-time), so the auto-merge gate stays green.

**How to apply:** when a PR changes an immutable PV field, expect to recreate the `-staging` PVs/PVCs as part of landing it (same one-time delete/recreate the `-prod` ones needed). Recreate procedure (staging, read-only NFS, `Retain` → no data loss), confirmed 2026-07-17 on jellyfin (PR #1136):
```
flux suspend kustomization apps-staging -n flux-system
kubectl scale deploy <app> -n <ns>-stage --replicas=0            # release PVC mounts; wait pod gone
kubectl delete pvc -n <ns>-stage <pvc>...                        # then
kubectl delete pv  <pv>-staging...                               # Retain PV, data untouched
flux resume kustomization apps-staging -n flux-system
flux reconcile kustomization apps-staging -n flux-system --with-source   # recreates at new path + scales app back up
```
Prod (`apps-production`) does NOT auto-apply open PRs, so prod only needs the recreate at actual merge — and only if the prod render's PV path actually changes (a base change that prod already overlays to the same value = no-op). Related: [[feedback_flux_suspend_during_cluster_ops]], [[feedback_pvc_storage_class_migration]], [[feedback_flux_pvc_volumename_anti_pattern]], [[project_jellyfin_library_cleanup]].

---

## zfs destroy busy container mount

**ZFS "dataset is busy" on destroy despite unmounted — caused by hestia containers bind-mounting a parent dir capturing child-dataset submounts; + TrueNAS altroot mountpoint gotcha**

Retiring a ZFS dataset on hestia (TrueNAS SCALE 26.x, pool `main` altroot `/mnt`).
Three traps, in order of how much time each burned:

**1. `zfs destroy` "dataset is busy" while `mounted=no` + not in `/proc/mounts` =
a container holds a STALE submount in its own mount namespace.** hestia docker
containers (`immich-photos-backup`, `homelabscope-heartbeat`) bind-mount a PARENT
dir (`/mnt/main/family`). A ZFS *child* dataset mounted under it (e.g.
`family/images/photos-staging-30d`) gets copied into the container's mount ns at
container-start. Later unmounting the child on the HOST does NOT remove the
container's private copy → it pins the dataset. Survives host unmount, `exportfs
-f`, nfsd cache flush, AND a full NFS service restart (mount namespaces are
independent). **Fix: `docker restart <container>`** to rebuild its ns cleanly
(safe once the child is host-unmounted — nothing left to recapture).
**Why:** don't trust `zfs get mounted` / `/proc/1/mounts` alone; scan ALL
namespaces: `for f in /proc/[0-9]*/mountinfo; do grep -l "<path>" $f; done`, then
map PID→container via `/proc/<pid>/cgroup`. `docker inspect .Mounts` won't show it
(the bind is the parent, the submount is implicit).

**2. `showmount -a` can show a PHANTOM client** (`10.42.2.25:/...images...`) that
has NO real mount (verified with a privileged hostPID probe pod reading
`/proc/1/mounts` on that Talos node). It's stale mountd bookkeeping, unrelated to
the busy state. Red herring — don't chase it or restart NFS for it.

**3. TrueNAS altroot mountpoint gotcha.** Pool `main` has `altroot=/mnt`. Creating
a dataset with `zfs create -o mountpoint=/mnt/main/...` DOUBLE-prefixes →
`/mnt/mnt/main/...`, and any rsync to the intended path silently lands in a plain
dir on the PARENT dataset instead of the new dataset. **Set mountpoint WITHOUT the
`/mnt` prefix:** `zfs set mountpoint=/main/family/media/photos-staging-30d` →
resolves to `/mnt/main/family/...`. Move staged data aside first so the (re)mount
doesn't shadow it.

**How to apply:** before any hestia `zfs destroy`, expect a container/app to hold
the dataset via a parent bind-mount; restart the holders (cheap, low-risk) rather
than restarting NFS (blips prod). Verify redundancy with a path+size manifest diff
(`find -printf "%P\t%s\n" | sort` + `comm -23`) before destroying. Context:
[[project_immich_photos_images_to_media]], [[project_truenas_api]],
[[feedback_bash_arrays_in_harness]].

---

## truenas apps

**For hestia-resident services, prefer TrueNAS Custom App (paste compose into SCALE UI) over raw docker-compose + systemd**

For services running on TrueNAS hestia (`truenas_admin@10.42.2.10`), prefer **TrueNAS Custom Apps** over raw `docker compose` invocation, even though it adds a manual copy/paste step.

**Why:** The user values TrueNAS's managed-lifecycle features — auto-restart on reboot, status/log surface in the SCALE UI, ZFS snapshot integration, and built-in app upgrade flow — more than fully-automated git-driven sync. The trade-off (operator copies the compose YAML from the homelab repo into the SCALE UI Custom App wizard when changes happen) is acceptable.

**How to apply:** When proposing a TrueNAS-resident service, default the deployment surface to **TrueNAS Custom App**. The compose YAML in the homelab repo (under `hosts/hestia/<app>/`) is the canonical source; operator pastes it into SCALE UI to deploy or update. Do not propose systemd units or raw `docker compose up -d` workflows for hestia services unless the user explicitly opts out of TrueNAS Apps.

**Gotchas to call out in plans:**
- TrueNAS Custom App does not support `build:` directives — images must be pre-built and pulled from a registry.
- Secrets/tokens belong in the SCALE UI's env-var section (masked input), not in the YAML pasted into git.
- TrueNAS prefixes container names with `ix-<app-name>-`. Compose YAML stays clean; expect the prefix when running `docker ps` / `docker inspect`.
- Custom volume host paths (e.g., `/mnt/tank/apps/<svc>/data`) are supported and recommended for data that should survive app deletion.
- Drift risk: someone could edit the compose in SCALE UI without updating git. Document the canonical-source rule in each `hosts/hestia/<app>/README.md`.

---

## synology 0700 default and group bit

**\"Synology DSM started saving new photo uploads with POSIX mode 0700 sometime around 2025-12-31. Files owned by user:users with mode `rwx**

---` look readable to a service account in the admin group, but they aren't: gid=100(users) matches the file group, and the group bit `---` denies BEFORE Unix falls through to 'other'. Fix is `chmod g+rX,o+rX`, not just `o+rX`."
metadata:
  node_type: memory
  type: feedback
---

### Rule

When a Synology DSM share rsync silently misses files dated after ~2026-01-01:

1. The Synology Photos / DSM Drive default file-mode template flipped from `0644` (rw-r--r--) to `0700` (rwx------) around 2025-12-31. New uploads since then are owner-only.
2. Adding `chmod o+rX` alone does NOT fix it for a service account that's in the **owning file's group** (typically `users` / gid=100). POSIX checks the group bit before "other"; if you're in the file's group and the group bit denies, you never reach the "other" bit.
3. The fix is `chmod -R g+rX,o+rX` (or just `chmod -R go+rX`).

### Why

Symptom this presents as: rsync says `success` with `Number of regular files transferred: 0` for files that obviously aren't on the destination. Or rsync exits 23 with `send_files failed to open ... Permission denied (13)` despite the share-level ACL granting Read access.

Reproduced 2026-06-02 on alcatraz (Synology DSM):
- `truenas-backup` user (uid 1031, **gid 100 = users**, also in admin group 101)
- Files `-rwx------ 1 mara users IMG_XXXX.HEIC` (mode 0700, gid 100 = `users`)
- truenas-backup IS in `users`, so group check applies → denied → never reaches "other"
- `chmod o+rX` made files `0705` (`-rwx---r-x`), but truenas-backup still got denied (group still `---`)
- `chmod g+rX,o+rX` made files `0755` (`-rwxr-xr-x`), truenas-backup reads fine

This is a classic Unix gotcha (group-bit-evaluated-before-other) showing up in a homelab-rsync context. Cost ~30 min before noticing the obvious mode `705` was missing the `g+r`.

### How to apply

When debugging "rsync silently skips Synology-owned files":
- `ssh truenas-backup@<alcatraz> 'stat -c "%a %U:%G" /volume1/homes/<user>/Photos/<recent-file>'`
- If the mode is `700` or any pattern where `group` is more restrictive than `other`, and the service account is in the file's group:
  - `ssh <dsm_admin>@<alcatraz>; sudo chmod -R g+rX,o+rX /volume1/homes/<user>/Photos/...`

For the homelab Synology specifically, this is alcatraz at `10.42.2.11`. DSM admin user that can sudo: **`manager`** (confirmed 2026-06-09).

### Recurrence log

- 2026-06-02 — first surfaced; manual chmod applied to existing files. Long-term hardening deferred.
- 2026-06-09 — recurred exactly as predicted. New uploads since 2026-06-02 went straight to mode 0700 again. Sync failed for all of george's 2026/06 files; mara unaffected (no new uploads).
- **2026-06-09 — durable fix landed**: PR #881 added `--rsync-path="sudo -n /bin/chmod -R g+rX,o+rX /volume1/homes/<user>/Photos && rsync"` to the immich-photos-backup script. Image `2026-06-09@sha256:93a52aa2…`, deployed via PR #882. Operator installed `/etc/sudoers.d/immich-photos-backup` on alcatraz (NOPASSWD for `truenas-backup` on the exact chmod commands). Validated end-to-end: catch-up run pulled 88M of new uploads cleanly. PR #885 follow-up replaces the original `visudo -cf` pre-validation gate (DSM doesn't ship visudo) with `sudo -n true` + `sudo -l -U` post-install sanity.

Next failure mode to watch for (not yet observed):
- DSM upgrade wipes `/etc/sudoers.d/` — the chmod fails → rsync exits 12 → cron logs "FAILED" loudly → metric stays stale → `ImmichPhotoBackupStale` alert fires after 36h. Re-add the sudoers file from README §3a.
- DSM upgrade sets `Defaults requiretty` — NOPASSWD+`-n` rejected on non-tty ssh; same loud failure mode.

### Long-term hardening

`chmod -R` is one-shot — it doesn't cover **future** uploads, which Synology will keep writing with mode 0700. Options for a permanent fix (not done as of 2026-06-02):

1. Cron job on alcatraz that runs `chmod -R g+rX,o+rX /volume1/homes/{george,mara}/Photos` nightly BEFORE the hestia 04:00 rsync pulls.
2. Add the chmod as a remote `--rsync-path` shim in the immich-photos-backup script (runs on alcatraz before each rsync invocation).
3. Find and revert the Synology Photos / DSM Drive setting that flipped the default to 0700 (operator detective work — DSM Photos Settings? Mobile app upload settings? Synology Knowledge Base?).
4. Have hestia rsync as a user that's NOT in the `users` group, so "other" bit applies. Would require a new account on alcatraz with no group membership in `users`.

### Related

- [[synology-per-user-photo-symlinks]] — sibling Synology permission trap on `family/images/photos/<user>` symlinks. Different mechanism (symlink ACL), same outcome (silent rsync skip).
- [[rsync-verify-destination]] — generic rule: always verify destination after a low-bytes rsync; both Synology traps masquerade as "clean" rsync runs.
- [[hestia-photos-sot]] — the migration that surfaced this trap. Caused Immich to silently miss all 2026 photos until 2026-06-02.

---

## synology per user photo symlinks

**On Synology DSM, /volume1/family/images/photos/<user>/ paths are symlinks into /volume1/homes/<user>/Photos/ (Synology Photos personal space). The family-side path has per-file ACLs from each user's account that deny non-owners file open, even when a shared service user has share-level Read. Sync from the homes path.**

### Rule

When rsync'ing photos off a Synology DSM box as a service user (e.g. `truenas-backup`), read from `/volume1/homes/<user>/Photos/` directly — never from `/volume1/family/images/photos/<user>/`.

### Why

On Synology Photos (and the older Photo Station), each user's personal photo collection lives in their home folder. The "family" share's `images/photos/<user>/` paths are symlinks (or bind mounts that look like symlinks to userspace tools) into each user's home. The file bytes are real; the apparent location under family is a view.

Per-file ACLs on those files come from the original uploader's Synology account, and Synology's filesystem ACL layer applies them at file open regardless of the path used to traverse. A service user (`truenas-backup`) granted Read on the `family` share can:
- List directories under `family/images/photos/<user>/` (directory ACL allows traversal)
- See file names and sizes (stat works via the parent dir's ACL)
- **NOT open the files** (file-level ACL only includes the owner)

Rsync's failure mode is specifically `send_files failed to open "...": Permission denied (13)` with exit code 23. The DSM "Apply to this folder, sub-folders and files" recursive-ACL apply on the `family` share **does not traverse into the symlinked targets** — you have to apply it on the underlying `homes` share, and even then file-level ACLs from the user's account can override.

The reliable workaround is to source from the canonical path: `truenas-backup@host:/volume1/homes/<user>/Photos/`. As long as `truenas-backup` has Read on the `homes` share, file ACLs from the user account include the share-level grant via the standard inheritance, and file open succeeds.

### How to apply

When writing or modifying any rsync that pulls per-user photos off Synology:

1. Source: `truenas-backup@<host>:/volume1/homes/<user>/Photos/` (loop over users)
2. Destination: whatever the canonical layout is on the receiver (e.g. `/mnt/main/family/images/photos/<user>/` on hestia per the 2026-06-01 SOT plan)
3. Never source from `/volume1/family/images/photos/<user>/` — even if it "feels" like the canonical path, it's a symlink with ACL traps

The `immich-photos-backup` script (`images/immich-photos-backup/immich-photos-backup.sh` in gjcourt/homelab) is the reference implementation. Loops over `USERS=(george mara)` and runs one rsync per user.

### Diagnostic signature

If rsync hits this and you forget the rule:
- exit code 23
- stderr full of `send_files failed to open "/volume1/family/images/photos/<user>/<YYYY>/<MM>/...": Permission denied (13)` for files older than a certain date AND newest files
- `du -sh` as the service user shows real sizes on the family path (directory ACL works)
- BUT `cat` on any individual file returns Permission denied

Reproduced 2026-06-01 in the hestia-SOT bulk seed (Phase 3 of [[hestia-photos-sot]]). Wasted ~2 hours of retries before the user surfaced the symlink fact.

### Related

- [[immich-photo-backup-setup]] — uses this pattern in production
- [[hestia-photos-sot]] — the plan that hit this trap during Phase 3 bulk seed

---

## rsync verify destination

**Don't trust rsync's 'low bytes sent/received + high speedup' summary as proof the sync worked. A high speedup means rsync DECIDED to skip files (either matched or perm-denied with --ignore-errors); empty destination + same summary means everything failed silently. Always verify destination has actual content.**

### Rule

After any rsync, verify the destination directly. Don't read success from rsync's summary line alone — verify with `du -sh <dest>`, `ls -la <dest>`, or `find <dest> | wc -l`. Especially when `--ignore-errors` or `--partial` is in play.

### Why

Rsync's final summary looks the same whether:
- (a) all source files already matched destination → no transfer needed → success
- (b) all source files were perm-denied at open and skipped under `--ignore-errors` → silent failure

Both produce: `sent 196 bytes received 2.01K bytes; total size is X; speedup is Y` with no `rsync error:` suffix. Exit code may still be 0 with `--ignore-errors`. The difference only shows up when you look at the destination.

Burned 2026-06-01 on hestia-SOT Phase 3 Stream C: `/volume1/homes/` → `/mnt/main/homes/`. Summary line matched the "already in place" pattern; declared clean. User later asked "where does that data live?" → discovered `/mnt/main/homes/` was 205 bytes (empty ZFS dataset metadata only). Truenas-backup hit the same per-user ACL trap as photos (see [[synology-per-user-photo-symlinks]]), and `--ignore-errors` swallowed every Permission denied silently.

### How to apply

After every non-trivial rsync, run at minimum:
```bash
du -sh <dest>
ls -la <dest> | head
```

Compare to expected order of magnitude. If destination size ≈ "few KB ZFS metadata" or "0M" when source was supposed to land hundreds of MB / GB, something failed silently. Read the stderr log.

When using `--ignore-errors` or `--partial`, always pipe stderr to a file (`2>/tmp/rsync-errs.log`) and check `wc -l` after — even if exit was 0, a long log means files were skipped.

### Related

- [[synology-per-user-photo-symlinks]] — the specific ACL trap that produced the silent skip in the hestia-SOT case
- [[hestia-photos-sot]] — the plan where this misread happened

---
