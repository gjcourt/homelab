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
| Home (UCG-Fiber gateway) | 2026-11-08 00:21 (Sunday) | 5.1.34.34102 → 5.1.35.34107 | UniFi OS console auto-update (`autoUpdate.schedule` in `/data/unifi-core/config/firmware.yaml` on the gateway: weekly, day 0, hour 0) |
| Home (UCG-Fiber gateway) | 2026-11-15 00:53 (Sunday) | 5.1.35.34107 → 6.0.17.36539 | Same console schedule (after this plan was written; see below) |

The 2026-08-13 incident ("a two-minute link flap cost 14 hours") also started at **03:01**.
The device auto-update hour is 3. That incident predates unpoller version history, so this is
consistent with an update rather than proven.

**Current state (2026-11-14):** George turned off Network-app device auto-update
(`mgmt.auto_upgrade = false`). The console OS schedule above is still active. Both stay as
they are until phase 4.

**2026-11-15:** the console schedule updated the gateway to 6.0.17 at about 00:53. The
update-verifier (phase 3) caught it, and `UpdateVerificationFailed` paged from 01:10 to 03:54
with `failed_checks=loki_stale`, the expected failure while Talos logs aren't shipped yet. The
update also stopped FRR on the gateway (`frr.service` is `not-found` on 6.0.17), so the Cilium
BGP session from talos-kot-7x7 to 10.42.2.1 has been down since 00:54 (`CiliumBGPSessionDown`).
LoadBalancer IPs still answered from the LAN at 04:30. The verifier has no BGP check.

**2026-11-15, later:** the Talos log patch is applied on all four nodes, so the verifier's
Loki check now has data and the next update should get past `loki_stale`. Loki lines now
carry a `node` label and Talos's own timestamps, and `TalosNodeLogsSilent` catches a single
silent node (`docs/reference/kernel-log-shipping.md`).

**2026-11-15, evening:** phase 1 is live. Every node was drained in turn (14:20–15:00 PST),
and all 80 iSCSI sessions now report `Recovery Timeout: 600`. The drain needed pod deletes,
because the single-replica `maxUnavailable: 0` PDBs block eviction; the procedure in
`AGENTS.md` is updated.

**2026-11-15, game day (phase 4 step 1): pass.** George restarted the Rack Switch from the
UniFi app at about 18:09 PST. Hestia and the nodes were unreachable from 18:09:06 to
18:11:56 (about 2m50s, from a 5 s ping recorder). Results against the test above:

- Hestia: `conn_rsp_timer_fn` closed 6 connections, then fresh logins; no `TSIH` or
  `Requested` rejects; SCST timers read `30 30 90`. Session count 80 after (81 before;
  the 81st was not a cluster session, since the cluster has 80).
- Nodes: every one of the 80 live sessions logged `detected conn error (1022)` and then
  `operational after recovery` (~56 attempts). No `session recovery timed out`,
  `rejecting I/O`, `Buffer I/O error` or `EXT4-fs error`.
- Writes: all 92 probed volumes writable; direct write exec passed on all 12 CNPG
  primaries, Prometheus and Loki.
- Verifier: `homelab_update_verification{device="Rack Switch",result="pass"}` at 982 s
  uptime, no email.
- Also confirmed: iscsid `noop_out_interval`/`noop_out_timeout` are 5/5, and the Talos
  kernel has no `hung_task_*` sysctls (no hung-task detector, so no panic risk).

Side effects, all self-recovered: controllers that lost the API restarted (democratic-csi
controller sidecars, the CNPG operator, which crash-looped ~3 min until its barman-cloud
plugin came back, and several system pods). One pvc-writeprobe sweep ran during the
outage, listed 0 volumes and published no series instead of `unknown`. That is a probe
blind spot worth fixing. Next: phase 4 step 2, device auto-update back on.

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
- **Resolved (from source; not yet tested live): the target side.** The 08-13 log shows SCST
  `conn_rsp_timer_fn: Timeout 30 sec ... closing connection`. Short answer: hestia closes the
  connection and **throws the whole session away** within about 60–90 s. That does not stop
  recovery. The initiator never resumes the old session: it logs in to a fresh one and
  re-sends its own queued I/O. Nothing on the target side limits how late that login can
  arrive, so the only deadline is the initiator's `replacement_timeout`. **No TrueNAS setting
  needs changing.** Details below.

  *What the 30 s timer is.* `conn_rsp_timer_fn` (`iscsi-scst/kernel/conn.c`) watches the
  oldest PDU still waiting on the initiator. When that PDU passes its deadline, it logs this
  line and calls `mark_conn_closed()`. The deadline comes from `iscsi_get_timeout()`
  (`kernel/iscsi.h`): `NopInTimeout` if the PDU is the target's own NOP-In ping, and
  `RspTimeout` for everything else. With SCST's defaults (`include/iscsi_scst.h`:
  `DEFAULT_NOP_IN_INTERVAL 30`, `DEFAULT_NOP_IN_TIMEOUT 30`, `DEFAULT_RSP_TIMEOUT 90`), a
  logged "30 sec" therefore means a NOP-In that got no reply. That is consistent with 08-13: the link
  dropped at 03:01:10, the connection then sat idle for the 30 s `NopInInterval`, a ping was
  sent, and it timed out after another 30 s, at 03:02:07. A connection with a command in
  flight would wait the 90 s `RspTimeout` instead. Either way, a 3–6 minute outage always
  outlasts the target's patience, so for this plan **the old session is always gone by the
  time the switch comes back**.

  *What the target does with commands and session state when it closes.* `close_conn()`
  (`kernel/nthread.c`) sends `SCST_NEXUS_LOSS_SESS` to the SCST core.
  `scst_abort_all_nexus_loss_sess()` (`scst/src/scst_targ.c`) runs `__scst_abort_task_set()`
  on every LUN in the session and clears any ACA. Outstanding commands are **aborted, not
  kept**: their responses are discarded, and backend I/O already handed to ZFS is waited out
  before the abort finishes. The connection-close event then reaches the user-space daemon.
  In `usr/event.c` (`case E_CONN_CLOSE`), `if (list_empty(&session->conn_list))
  session_free(session);` runs. That is the `session_free: Freeing session` line on 08-13.
  Freeing the session's per-LUN state also drops any SCSI-2 `RESERVE` it held
  (`scst_clear_reservation()` in `scst_lib.c`). How persistent reservations behave was not
  traced in source. Nothing in this stack is known to use either kind; that is unverified,
  but a reservation conflict would show up as an I/O error, which the test below fails on.
  The target keeps no state to come back to anyway: it fixes `ErrorRecoveryLevel` at 0,
  `MaxConnections` at 1 and `DefaultTime2Retain` at 0 (min = max in `usr/param.c`). So there
  is no connection-level or task-level recovery on this target, ever.

  *Why re-login still works.* open-iscsi always sends `login_hdr->tsih = 0`
  (`usr/login.c`, `iscsi_make_login_pdu()`), together with the node's unchanged ISID. In
  `login_check_reinstatement()` (`usr/iscsid.c`), TSIH 0 with no matching session means
  "new session", which is accepted. TSIH 0 *with* a matching session means "session
  reinstatement": the kernel aborts the old session first, so it can't run commands after
  the new one does (comment in `iscsi_task_mgmt_affected_cmds_done()`). The only login it
  rejects is a non-zero TSIH for a session that is gone, and open-iscsi never sends that.
  The new session gets SCST's standard "power on / reset" Unit Attention (`29/00`, set by
  `scst_alloc_set_UA()` when per-LUN state is created). For a non-removable disk, Linux
  answers that with `ACTION_RETRY` (`scsi_io_completion_action()` in
  `drivers/scsi/scsi_lib.c`).

  *What happens to queued I/O on the node.* From Linux v6.18 source (the nodes' exact kernel
  version wasn't checked): when the node detects the dead
  connection (open-iscsi default `noop_out_interval`/`noop_out_timeout` 5/5 s), libiscsi
  fails the in-flight commands with `DID_TRANSPORT_DISRUPTED`
  (`iscsi_start_session_recovery()` → `fail_scsi_tasks()`). `scsi_decide_disposition()`
  sends that to `maybe_retry`, so each command is requeued behind the blocked session; each
  requeue uses up one of the command's retries. While the session is not logged in,
  `iscsi_eh_cmd_timed_out()` returns `SCSI_EH_RESET_TIMER`, so the 30 s SCSI command timeout
  can't escalate during recovery. Only `session_recovery_timedout()`
  (`scsi_transport_iscsi.c`) fails I/O, by setting devices `SDEV_TRANSPORT_OFFLINE`, and
  that is the `replacement_timeout` firing. If the login succeeds first, the queue unblocks
  and the requeued commands go out on the new session. **ext4 sees only latency.** A write
  the target finished but never acknowledged is sent again, which is safe for plain
  `WRITE`s to the same blocks.

  *Target settings: what matters and what doesn't.*

  | Setting | Effect here |
  | --- | --- |
  | SCST `NopInInterval` / `NopInTimeout` / `RspTimeout` (30/30/90 s) | Only sets how fast hestia drops a dead session. It does **not** limit how late the initiator can come back, so leave it alone. Dropping fast is better: the old session is already gone at re-login, so the reinstatement path never runs. TrueNAS can't change these anyway. The `scst.conf.mako` template only passes per-target parameters listed in `ISCSI_TARGET_PARAMETERS = ['QueuedCommands']` (`plugins/iscsi_/utils.py`), and GitHub code search finds no `NopInInterval`, `NopInTimeout` or `RspTimeout` in `truenas/middleware`. A hand edit in sysfs would be lost the next time middleware regenerates the config. |
  | Target `QueuedCommands` (32/128) | Queue depth only. Doesn't matter here. |
  | Global: Base Name, ISNS Servers, Pool Available Space Threshold, Listen Port, ALUA (HA only), iSER | None of them touch session or connection lifetime (fields in `plugins/iscsi_/global_.py`). Don't matter here. |
  | Portal, initiator group, CHAP | They only matter if they change during the outage: a re-login is a full login and must pass them again. |
  | `scst` service restart on hestia | Not part of a switch reboot. A restart during the window still ends the same way: a fresh session, inside 600 s. |

  **Target-side change for George: none.**

  *Still unverified, and outside the target:* whether Talos's iscsi-tools `iscsid.conf` keeps
  the 5/5 noop defaults (if detection were slower than 30 s, the SCSI error handler could
  start aborts first); whether `kernel.hung_task_panic` is 0 on the nodes (writers blocked
  over 120 s will log `blocked for more than 120 seconds`, which should only be a warning);
  and how apps and probes behave during a several-minute freeze. Nothing here has run
  against hestia's live target. Source versions read: `truenas/scst` `stable/26` @
  `fda79d4` (2026-08-19). The same seven `iscsi-scst` files are byte-identical on
  `release/26.0.0-BETA.1` @ `0fc6a9f`, the version last recorded for hestia in
  `2026-09-04-hestia-silent-hang-diagnosis.md`. Also `truenas/middleware` `stable/26` @
  `59e6372`, `open-iscsi/open-iscsi` `master` @ `a65ce4b`, and Linux `v6.18`. SCST's own
  documentation is `iscsi-scst/README`, which describes `NopInInterval`, `NopInTimeout` and
  `RspTimeout`.

  *Test for the phase 4 game day.* Run this before the Rack Switch restart, after phase 1
  shows `Recovery Timeout: 600` everywhere.
  1. On hestia, as George: `cat /sys/kernel/scst_tgt/targets/iscsi/<iqn>/{NopInInterval,NopInTimeout,RspTimeout}`
     for one democratic-csi target. Expect `30 30 90`. The attribute path comes from the
     README and hasn't been checked on hestia.
     Record `ls -d /sys/kernel/scst_tgt/targets/iscsi/*/sessions/* | wc -l` as the baseline.
  2. Start `sudo dmesg -wT | grep -E 'iscsi-scst|igb'` on hestia, and on one node
     `talosctl -n <node> dmesg -f | grep -iE 'iscsi|session|I/O error|EXT4-fs'` (kernel).
     Also `talosctl -n <node> logs ext-iscsid -f` for the `iscsid` daemon (the extension
     service is named `iscsid`; on Talos it shows up as `ext-iscsid`).
  3. Restart the switch. Write down when the link goes down and comes up (hestia's `igb ...
     Link is Down/Up`), so you know the actual outage against the 600 s window.
  4. Pass if:
     - hestia logs `conn_rsp_timer_fn ... closing connection` and `session_free` (expected:
       this is the target giving up);
     - after link-up, each node's `ext-iscsid` log shows `connection<N>:0 is operational
       after recovery (<n> attempts)`;
     - the hestia session count returns to the baseline;
     - every `homelab_pvc_writable == 1`, and the per-pod write exec from `AGENTS.md`
       succeeds.
  5. Fail if any node logs `session recovery timed out after 600 secs`, `rejecting I/O to
     offline device`, `Buffer I/O error` or `EXT4-fs error`, or if any login is rejected on
     hestia (`Requested TSIH not 0`, or `TSIH for existing session ... doesn't match`).
     Any of these means the reasoning above is wrong. Stop at phase 4 step 1.

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

A syslog source on the existing Vector DaemonSet (UDP 5514 on the host network, like the Talos
receiver; no NodePort), with UniFi remote logging
pointed at it. In Network 10.6 that setting is **Settings → CyberSecure → Traffic Logging →
Activity Logging (Syslog)**. It's currently **Internally Stored**; switch it to **SIEM Server**
with the Vector address once the receiver exists. Its "All UniFi Device Management" flow
(currently off) is the one that carries device events. That would give each update a
detailed record (download, flash, reboot, port up) next to the iSCSI and kernel logs. Not needed for phases 1–4.

**2026-11-15:** receiver added (`unifi_syslog` in `infra/controllers/vector/values.yaml`,
Loki `job="unifi-syslog"`), plus a LoadBalancer IP for it, `10.42.2.47`. Point **SIEM Server**
at `10.42.2.47:5514` (the field takes one address, so a node IP has no failover). Send device
management and threat (IPS) events only, not per-flow traffic logs: those would flood Loki.
Syslog over UDP is lost while the Rack Switch itself is down; events before and after arrive.

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
