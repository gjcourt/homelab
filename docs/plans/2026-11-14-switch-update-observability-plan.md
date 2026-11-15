---
status: in-progress
last_modified: 2026-11-15
summary: "Let UniFi auto-update the Rack Switch and gateway unattended: raise the iSCSI replacement_timeout so a reboot can't remount volumes read-only, then detect each reboot, verify recovery automatically, and email only on failure"
---

# Plan: unattended UniFi updates, observed and verified

## Decision

George, 2026-11-14: keep firmware updates, but the system should observe each update and
confirm it went smoothly by itself, without him in the loop. He hears about an update only if
it went wrong.

So the goal is not "never update the switch". It is to make an update **harmless**
(prevention), **visible** (detection), and **checked** (verification), with silence meaning
success and a deadman making sure silence can't also mean "the checker is broken".

## Context: updates are what has been rebooting the storage path

Every StorageClass is democratic-csi over iSCSI to hestia, through the **Rack Switch**
(USWED42). A switch reboot cuts every node's iSCSI sessions at once.

The gateway is **not** on the iSCSI path: the nodes (10.42.2.20–25) and hestia (10.42.2.10)
share a subnet, so iSCSI traffic stays on the switch. The gateway is watched here because it is
the other device that updates itself unattended, not because its reboot drops storage.

From unpoller (`unpoller_device_uptime_seconds`, `unpoller_device_info`), last 30 days:

| Device | Reboot (local) | Firmware | Cause |
| --- | --- | --- | --- |
| Rack Switch | 2026-11-13 03:12 | 7.5.15.17146 → 7.6.11.17272 | Network app device auto-update (`mgmt.auto_upgrade_hour = 3`) |
| Home (UCG-Fiber gateway) | 2026-11-08 00:21 (Sunday) | — | UniFi OS console auto-update (`autoUpdate.schedule` in `/data/unifi-core/config/firmware.yaml` on the gateway: weekly, day 0, hour 0) |

The 2026-08-13 incident ("a two-minute link flap cost 14 hours") also started at **03:01**.
The device auto-update hour is 3. That incident predates unpoller version history, so this is
consistent with an update rather than proven.

**Current state (2026-11-14):** George turned off Network-app device auto-update
(`mgmt.auto_upgrade = false`). The console OS schedule above is still active. Both stay as
they are until phase 4.

## What already exists (do not rebuild)

- **unpoller → Prometheus:** `unpoller_device_uptime_seconds`, `unpoller_device_info{version}`,
  `unpoller_device_upgradable`. Rules in `infra/configs/unpoller/prometheus-rule.yaml`.
- **pvc-writeprobe:** `homelab_pvc_writable{namespace,pvc,pod}` with real writes into every RW
  PVC every 5 min, plus `PvcNotWritable` and its self-silence alerts.
- **Talos kernel logs → Vector → Loki**, including the iSCSI error LogQL alert
  (`infra/controllers/loki/alerting-rules.yaml`).
- **Recovery is deliberately manual** (`2026-09-01-readonly-pvc-recovery.md`): Tier 1 is a
  recovery script (`scripts/recover-readonly-pvc.sh`, planned there, **not built yet**), and a
  controller that suspends Flux or restarts Postgres is ruled out.
  This plan keeps that. It **prevents and verifies**; it does not auto-remediate.

## Phase 1 — prevention: raise `replacement_timeout` (prerequisite)

The read-only recovery plan already found the mechanism: live sessions report
`Recovery Timeout: 120`, and once the initiator's `replacement_timeout` expires the SCSI layer
fails I/O and ext4 remounts read-only. A switch reboot is roughly 3–5 minutes
(`upgrade_duration` for the Rack Switch reads 320 s), so with 120 s **every** switch update
can remount volumes read-only. No amount of observation fixes that, so this goes first.

- Set `node.session.timeo.replacement_timeout = 600` on every node's initiator.
- **Open question 1: where it lives — resolved.** See *Resolved: open question 1* below.
- **Existing sessions keep 120.** The setting applies at login. Either `iscsiadm -m node
  -o update` plus re-login per volume, or let it roll in as pods reschedule. Verify with
  `iscsiadm -m session -P 3 | grep 'Recovery Timeout'` on every node: all 600.
- **The trade:** writes hang for up to 10 minutes instead of failing after 2. During a real
  hestia outage, apps freeze longer before erroring. Accepted, given that the read-only
  alternative has cost 14 hours twice.
- **Open question 2: the target side.** The 08-13 log shows SCST
  `conn_rsp_timer_fn: Timeout 30 sec ... closing connection`. That's a different timer.
  Confirm a closed connection on hestia's side still lets the initiator re-login within its
  600 s window, rather than failing the session.

### Resolved: open question 1 — where `replacement_timeout` lives

Researched 2026-11-15 by reading the source at the versions this repo pins (sources and
numbered references `[n]` at the end of this section). Cluster access was read-only: I could
read the `democratic-csi` namespace's DaemonSet and pods, but **not** StorageClasses, PVs,
Secrets or Nodes, and could not `exec`. Nothing below was run against a live iSCSI session.

**Recommendation: (a), democratic-csi node-stage secret.** Add one key to the
`node-stage-secret` of each of the six StorageClasses in
`infra/controllers/democratic-csi/values.yaml`. It stays in this repo's Helm values, needs no
Talos machine-config change and no iscsid restart, and leaves the StorageClass objects as
they are. (b) is also possible, though its mount is unverified (see below). It lives outside this repo and restarts `ext-iscsid` on every
node when applied.

#### (a) Can democratic-csi set node options at login? Yes.

- In `NodeStageVolume` for `node_attach_driver: iscsi`, the v1.9.5 driver builds a node-DB
  map that starts as `{"node.startup": "manual"}`. It then adds every **node-stage secret**
  key that starts with `node-db.`, with the prefix stripped [1]. `createNodeDBEntry` runs
  `iscsiadm -m node -T <iqn> -p <portal> -o new`, then
  `iscsiadm -m node -T <iqn> -p <portal> -o update --name <attr> --value <v>` for each entry
  [2]. Only after that does it run `iscsiadm ... -l` (login) [1].
- The secret comes from `call.request.secrets`, the CSI NodeStage secrets [3]. Upstream
  documents this as "any arbitrary iscsiadm entries can be add by creating keys starting
  with node-db.<entry.name>", using CHAP keys as the example [4][5].
- **Exact YAML key** (Helm values, under each `storageClasses[]` entry):
  `secrets.node-stage-secret."node-db.node.session.timeo.replacement_timeout": "600"`.
- **This repo's StorageClasses already reference a node-stage secret.** Every
  `storageClasses[]` entry in `values.yaml` has declared an empty `node-stage-secret:`
  since the first democratic-csi commit (`19b1adf3`, 2026-04-29). Chart 0.14.6 loops over
  `secrets` and emits `csi.storage.k8s.io/node-stage-secret-name/-namespace` parameters
  plus a Secret `node-stage-secret-<class>-democratic-csi`, even when the value is null [6].
  I rendered the chart locally (`helm template`, helm v3.16.4, chart 0.14.6) with the
  current values and with the diff below. The six **StorageClass objects are
  byte-identical** between the two renders. Only the six Secrets gain the key. That matters
  because StorageClass `parameters` are immutable. `kubectl kustomize` of a scratch copy of
  `infra/controllers/democratic-csi/` with the diff also succeeds.
- The node plugin's `iscsiadm` is the wrapper `docker/iscsiadm`. With
  `ISCSIADM_HOST_STRATEGY=nsenter` it runs `/usr/local/sbin/iscsiadm` in **iscsid's** mount
  and network namespaces [7]. The node records it writes are the ones iscsid reads, under
  `/var/lib/iscsi`, which the extension bind-mounts read-write [8].
- **Unverified (no PV read access):** whether every existing iSCSI PV has
  `spec.csi.nodeStageSecretRef` set. external-provisioner copies it into the PV from the
  StorageClass parameters at provisioning time. The SC parameters have existed since the
  first commit, and every static `kind: PersistentVolume` in `apps/` is NFS, so I expect
  all of them to have it. A PV without the ref gets no secrets and stays at 120. George can
  check with:
  `kubectl get pv -o jsonpath='{range .items[?(@.spec.csi.driver=="org.democratic-csi.truenas-iscsi")]}{.metadata.name} {.spec.csi.nodeStageSecretRef.name}{"\n"}{end}'`.
  Any line with no second field is a PV that will not pick the value up.
- The key lands in a plain, Helm-rendered `Secret` from `values.yaml`, not SOPS. That is
  acceptable because `600` is not a credential. The chart has no ConfigMap alternative for
  NodeStage parameters [6].

#### (b) The iscsi-tools `iscsid.conf` route via Talos machine config

- iscsi-tools v1.12.4 builds open-iscsi **2.1.11** with `-Dhomedir=/etc/iscsi` [9][10]. Its
  `iscsid` service bind-mounts the host's `/etc/iscsi` **read-only** into the
  `ext-iscsid` container [8]. The extension ships no `iscsid.conf`: the package deletes
  `/rootfs/etc` [10]. `iscsiadm` seeds every `-o new` record from
  `/etc/iscsi/iscsid.conf` and overwrites any existing record (`add_static_rec` →
  `idbm_node_setup_from_conf` → `idbm_add_node(rec, drec, 1)`) [11]. With no file it uses
  the compiled default `DEF_REPLACEMENT_TIMEO 120` [12], which matches the `Recovery
  Timeout: 120` already observed.
- Talos v1.12.4 accepts an `ExtensionServiceConfig` document. Each `configFiles[]`
  `content` is bind-mounted read-only at `mountPath` inside the extension's container [13][14]:

  ```yaml
  apiVersion: v1alpha1
  kind: ExtensionServiceConfig
  name: iscsid
  configFiles:
    - content: |
        node.session.timeo.replacement_timeout = 600
      mountPath: /etc/iscsi/iscsid.conf
  ```

  Apply it with `talosctl -n <node> patch mc --patch @iscsid-config.yaml` per node. This
  repo does not store Talos machine configs (no `ExtensionServiceConfig` or Talos patch
  anywhere in the tree), so this change would live outside GitOps.
- **No reboot.** A config change bumps `ExtensionServiceConfigStatus.SpecVersion`, and
  `ExtensionServiceController` calls `handleRestart` on `ext-iscsid` [15]. **iscsid restarts
  automatically on every node when the patch is applied.**
- **Unverified:** whether the bind-mount onto `/etc/iscsi/iscsid.conf` succeeds when that
  path doesn't exist on the host. The parent is a read-only bind, so the runtime may be
  unable to create the mount-point file. Test on one node before rolling out.

#### (c) How existing sessions pick it up, and verification

- Neither option changes a running session by itself. iscsid copies
  `rec->session.timeo.replacement_timeout` into the session when it is created [16], then
  pushes it to the kernel as `ISCSI_PARAM_SESS_RECOVERY_TMO` on each connection login
  [17][18].
- **Passive roll-in (recommended default):** each new `NodeStageVolume` writes 600 to the
  node record before login, so the value arrives as volumes are re-staged on a node. That
  happens when a pod lands on a node where the volume isn't staged yet (the
  `kubectl delete pod` → different-node pattern in AGENTS.md), and on node reboot.
  Volumes that never move keep 120 until then.
- **Active, no pod restart (from source; unverified on this cluster, game-day it first in
  `readonly-gameday`):** on each node, update all existing node records, then restart
  iscsid:

  ```bash
  POD=<democratic-csi-node pod on the node>
  kubectl -n democratic-csi exec "$POD" -c csi-driver -- \
    iscsiadm -m node -o update -n node.session.timeo.replacement_timeout -v 600
  talosctl -n <node-ip> service ext-iscsid restart   # unverified for extension services
  ```

  On startup iscsid runs `sync_session` for every kernel session. It re-reads the node
  record (`idbm_rec_read`) and calls `iscsi_sync_session`, which creates the session from
  that record and does `session_conn_reopen(..., STOP_CONN_RECOVER)` [19][20]. That
  re-login pushes the new `recovery_tmo`. The cost is a brief reconnect of every session
  on the node at once, while the old 120 s is still in force. Do it one node at a time,
  never during a switch update.
- **Verification**: `Recovery Timeout` in `iscsiadm -m session -P 3` is the kernel's live
  `recovery_tmo` from sysfs, not the node record [21]:

  ```bash
  for p in $(kubectl -n democratic-csi get pods -l app.kubernetes.io/csi-role=node -o name); do
    echo "== $p"
    kubectl -n democratic-csi exec "$p" -c csi-driver -- \
      iscsiadm -m session -P 3 | grep 'Recovery Timeout' | sort | uniq -c
  done
  ```

  Done when every node prints only `Recovery Timeout: 600`. To check the record that
  future logins will use:
  `iscsiadm -m node -T <iqn> -p <portal> | grep replacement_timeout`. I couldn't run
  either command here (no `pods/exec`). Pod label and container name are from the live
  DaemonSet.

#### Proposed infra diff (separate PR; not applied here)

```diff
--- a/infra/controllers/democratic-csi/values.yaml
+++ b/infra/controllers/democratic-csi/values.yaml
@@ -43,6 +43,10 @@
       provisioner-secret:
       controller-publish-secret:
       node-stage-secret:
+        # open-iscsi node-DB key; democratic-csi runs `iscsiadm -m node -o update`
+        # with it before login. 120 s (default) < a Rack Switch reboot. See
+        # docs/plans/2026-11-14-switch-update-observability-plan.md (Phase 1).
+        node-db.node.session.timeo.replacement_timeout: "600"
       node-publish-secret:
       controller-expand-secret:

@@ -58,6 +62,7 @@
       provisioner-secret:
       controller-publish-secret:
       node-stage-secret:
+        node-db.node.session.timeo.replacement_timeout: "600"
       node-publish-secret:
       controller-expand-secret:

@@ -73,6 +78,7 @@
       provisioner-secret:
       controller-publish-secret:
       node-stage-secret:
+        node-db.node.session.timeo.replacement_timeout: "600"
       node-publish-secret:
       controller-expand-secret:

@@ -98,6 +104,7 @@
       provisioner-secret:
       controller-publish-secret:
       node-stage-secret:
+        node-db.node.session.timeo.replacement_timeout: "600"
       node-publish-secret:
       controller-expand-secret:

@@ -113,6 +120,7 @@
       provisioner-secret:
       controller-publish-secret:
       node-stage-secret:
+        node-db.node.session.timeo.replacement_timeout: "600"
       node-publish-secret:
       controller-expand-secret:

@@ -128,6 +136,7 @@
       provisioner-secret:
       controller-publish-secret:
       node-stage-secret:
+        node-db.node.session.timeo.replacement_timeout: "600"
       node-publish-secret:
       controller-expand-secret:
```

After merge: Flux upgrades the HelmRelease, and only the six `node-stage-secret-*` Secrets
change. The DaemonSet doesn't roll, because the secret is read per NodeStage call [3].

#### Sources

Pinned versions: chart `democratic-csi` **0.14.6** (`release.yaml`), driver image
`democraticcsi/democratic-csi:v1.9.5` (`values.yaml`; the live DaemonSet runs the same
digest), Talos **v1.12.4** (AGENTS.md), siderolabs/extensions **v1.12.4** → open-iscsi
**2.1.11**. I could not read node objects, so I haven't confirmed that the nodes' installed
iscsi-tools is the v1.12.4 build.

1. democratic-csi v1.9.5 `src/driver/index.js` NodeStage node-DB and login — https://github.com/democratic-csi/democratic-csi/blob/v1.9.5/src/driver/index.js#L924-L955
2. democratic-csi v1.9.5 `src/utils/iscsi.js` `createNodeDBEntry` — https://github.com/democratic-csi/democratic-csi/blob/v1.9.5/src/utils/iscsi.js#L88-L150
3. democratic-csi v1.9.5 `src/driver/index.js` `normalizedSecrets` from `call.request.secrets` — https://github.com/democratic-csi/democratic-csi/blob/v1.9.5/src/driver/index.js#L769-L773 and `getNormalizedParameters` https://github.com/democratic-csi/democratic-csi/blob/v1.9.5/src/driver/index.js#L73-L102
4. democratic-csi v1.9.5 `examples/node-manual-iscsi-pv.yaml` — https://github.com/democratic-csi/democratic-csi/blob/v1.9.5/examples/node-manual-iscsi-pv.yaml#L19-L30
5. charts 0.14.6 `values.yaml` `node-stage-secret` `node-db.` comment — https://github.com/democratic-csi/charts/blob/democratic-csi-0.14.6/stable/democratic-csi/values.yaml#L300-L314
6. charts 0.14.6 `templates/storage-classes.yaml` — https://github.com/democratic-csi/charts/blob/democratic-csi-0.14.6/stable/democratic-csi/templates/storage-classes.yaml
7. democratic-csi v1.9.5 `docker/iscsiadm` nsenter strategy — https://github.com/democratic-csi/democratic-csi/blob/v1.9.5/docker/iscsiadm#L30-L37
8. extensions v1.12.4 `iscsid.yaml` mounts — https://github.com/siderolabs/extensions/blob/v1.12.4/storage/iscsi-tools/iscsid.yaml
9. extensions v1.12.4 `vars.yaml` (`OPEN_ISCSI_VERSION: 2.1.11`) — https://github.com/siderolabs/extensions/blob/v1.12.4/storage/iscsi-tools/vars.yaml
10. extensions v1.12.4 `open-iscsi/pkg.yaml` (`-Dhomedir=/etc/iscsi`, `rm -rf /rootfs/etc`) — https://github.com/siderolabs/extensions/blob/v1.12.4/storage/iscsi-tools/open-iscsi/pkg.yaml
11. open-iscsi 2.1.11 `usr/iscsiadm.c` `add_static_rec` — https://github.com/open-iscsi/open-iscsi/blob/2.1.11/usr/iscsiadm.c#L974-L1013
12. open-iscsi 2.1.11 `usr/iscsi_settings.h` — https://github.com/open-iscsi/open-iscsi/blob/2.1.11/usr/iscsi_settings.h#L10; `etc/iscsid.conf` default — https://github.com/open-iscsi/open-iscsi/blob/2.1.11/etc/iscsid.conf#L126
13. Talos v1.12.4 `ExtensionServiceConfig` type (`configFiles`, `mountPath`) — https://github.com/siderolabs/talos/blob/v1.12.4/pkg/machinery/config/types/runtime/extensions/service_config.go
14. Talos v1.12.4 extension runner bind-mounts config files `ro` at `mountPath` — https://github.com/siderolabs/talos/blob/v1.12.4/internal/app/machined/pkg/system/services/extension.go#L186-L197
15. Talos v1.12.4 restart on config change — https://github.com/siderolabs/talos/blob/v1.12.4/internal/app/machined/pkg/controllers/runtime/extension_service.go#L143-L170
16. open-iscsi 2.1.11 `usr/initiator.c` session alloc — https://github.com/open-iscsi/open-iscsi/blob/2.1.11/usr/initiator.c#L384-L385
17. open-iscsi 2.1.11 `usr/initiator_common.c` `ISCSI_PARAM_SESS_RECOVERY_TMO` — https://github.com/open-iscsi/open-iscsi/blob/2.1.11/usr/initiator_common.c#L508-L511
18. open-iscsi 2.1.11 `usr/initiator.c` `iscsi_session_set_params` at login — https://github.com/open-iscsi/open-iscsi/blob/2.1.11/usr/initiator.c#L1676
19. open-iscsi 2.1.11 `usr/iscsid.c` `sync_session` — https://github.com/open-iscsi/open-iscsi/blob/2.1.11/usr/iscsid.c#L192-L250 and https://github.com/open-iscsi/open-iscsi/blob/2.1.11/usr/iscsid.c#L598-L604
20. open-iscsi 2.1.11 `usr/initiator.c` `iscsi_sync_session` — https://github.com/open-iscsi/open-iscsi/blob/2.1.11/usr/initiator.c#L2069-L2104
21. open-iscsi 2.1.11 `usr/session_info.c` `Recovery Timeout` print — https://github.com/open-iscsi/open-iscsi/blob/2.1.11/usr/session_info.c#L360-L366

## Phase 2 — detection: know an update is coming and that it happened

New rules in `infra/configs/unpoller/prometheus-rule.yaml`, scoped to the watched devices
(`name=~"Rack Switch|Home"`). These are **record-only**: `severity: info`, which matches neither
the `critical` nor the `warning` email route and falls through to the default `'null'` receiver
(`infra/controllers/kube-prometheus-stack/values.yaml`). They show up in Grafana, not in
George's inbox.

| Rule | Expr (sketch) | Purpose |
| --- | --- | --- |
| `UniFiUpdatePending` | `unpoller_device_upgradable == 1` | An update is queued for this device |
| `UniFiDeviceRebooted` | `unpoller_device_uptime_seconds < 1800` | Opens a 30-min verification window |
| `UniFiFirmwareChanged` | `count by (name) (group by (name, version) (count_over_time(unpoller_device_info[2h]))) > 1` | Classifies the reboot as an update rather than a crash or power loss |

`unpoller_device_info` also carries `pod`, `instance` and `ip` (the gateway's `ip` is its WAN
address), so the `group by (name, version)` is load-bearing: counting raw series would fire on
every unpoller restart or WAN IP change.

Plus a Grafana annotation on each reboot (labelled with the old and new version) across the
storage and etcd dashboards, so a future latency spike lines up with "switch updated here".

## Phase 3 — verification: check recovery, then stay silent

A small **`update-verifier`** Deployment (monitoring namespace, a 5-min loop, `/metrics`
scraped by a ServiceMonitor). It's the same shape as pvc-writeprobe, which is also a
long-running Deployment. A CronJob can't be scraped, and the cluster has no Pushgateway. It needs
no Kubernetes RBAC, since it only queries Prometheus and Loki. It acts only when a watched
device's uptime is under 45 min and that boot has no result yet. A boot is identified by its
start time, `timestamp(unpoller_device_uptime_seconds) - unpoller_device_uptime_seconds`
rounded to the minute (sample time, not `time()`, so scrape lag doesn't shift it). "No result
yet" is read back from Prometheus, not held in memory, so a verifier restart neither loses a
result nor re-runs a finished boot:

1. Wait until uptime ≥ 15 min, so sessions have had the 600 s window to come back.
2. Run checks through the Prometheus and Loki APIs (no node access needed):
   - every `homelab_pvc_writable == 1`, and no `PvcNotWritable` firing or pending;
   - all nodes `Ready`, and the etcd member count is 3 with a leader;
   - Flux Kustomizations and HelmReleases are Ready (no new failures since boot);
   - no iSCSI error lines in Loki since boot + 10 min (the reconnect itself logs some).
3. Export `homelab_update_verification{device, boot, from_version, to_version, result,
   failed_checks}` (`failed_checks` empty on a pass) and write one structured log line to Loki.

Outcomes:

- **Pass:** recorded in Grafana. No email.
- **Fail:** a `severity: critical` alert on `result="fail"`, so one email naming the device,
  versions and `failed_checks`, linking the recovery runbook (`AGENTS.md`, "Recovering read-only iSCSI volumes"), and
  `scripts/recover-readonly-pvc.sh` once the 2026-09-01 plan builds it. This is the only
  time George is pulled in.
- **Deadman:** `UpdateVerificationMissing` fires (critical) if a watched device's uptime is
  between 60 and 180 min with no `homelab_update_verification` for that boot in
  `max_over_time(...[3h])`. The lookback matters: the gauge lives in the verifier's memory, so
  a restart after a pass would otherwise read as "never verified" and page falsely. The upper
  bound stops it firing forever. Silence then reliably means "verified fine".

**Open question 3:** whether a verifier is worth it over pure alert rules. Rules alone can cover
failure ("rebooted in the last hour AND `PvcNotWritable`"), but they can't positively record
"this update went fine" per boot, and the deadman needs that record. Lean verifier: a single
small script, same Deployment pattern as pvc-writeprobe.

## Phase 4 — turn updates back on, unattended

Only after phase 1 is verified on every node and phases 2–3 are live:

1. **Game day (George, once):** in a window he picks, restart the Rack Switch from the UniFi
   app (Devices → Rack Switch → Restart). Expect: no read-only remounts, the verifier records
   a pass, no email. If it emails, phase 1 is wrong. Stop and fix before step 2.
2. Re-enable Network-app device auto-update at a quiet hour (keep 03:00 or choose another).
3. Leave the console OS schedule as is (Sunday 00:00, set under **UCG Fiber → Updates**); the
   verifier covers the gateway too.
4. From then on, George is involved only on a failure email.

## Phase 5 (optional) — UniFi syslog to Loki

A syslog source on the existing Vector DaemonSet (UDP NodePort), with UniFi remote logging
pointed at it. In Network 10.6 that setting is **Settings → CyberSecure → Traffic Logging →
Activity Logging (Syslog)**. It's currently **Internally Stored**; switch it to **SIEM Server**
with the Vector address once the receiver exists. Its "All UniFi Device Management" flow
(currently off) is the one that carries device events. That would give each update a
detailed record (download, flash, reboot, port up) next to the iSCSI and kernel logs. Not needed for phases 1–4.

## Out of scope

- Auto-remediating read-only volumes. Ruled out by the 2026-09-01 plan; unchanged here.
- Removing the Rack Switch as a single point of failure (a second switch, or bonded NICs
  across two switches). That's real, but a separate hardware decision.
- Per-device auto-update toggles. The Network app's setting is site-wide
  (`mgmt.auto_upgrade`).

## Done when

- Every node reports `Recovery Timeout: 600` on every session.
- A deliberate Rack Switch restart produces a pass result and no email.
- Device auto-update is back on, and the next real update records a pass with nobody
  involved.
