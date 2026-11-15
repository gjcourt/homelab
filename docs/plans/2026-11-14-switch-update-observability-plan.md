---
status: planned
last_modified: 2026-11-14
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
| Home (UCG-Fiber gateway) | 2026-11-08 00:21 (Sunday) | — | UniFi OS console auto-update (`firmware.yaml` `autoUpdate.schedule`: weekly, day 0, hour 0) |

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
  controller that suspends Flux or restarts Postgres is ruled out. This
  plan keeps that. It **prevents and verifies**; it does not auto-remediate.

## Phase 1 — prevention: raise `replacement_timeout` (prerequisite)

The read-only recovery plan already found the mechanism: live sessions report
`Recovery Timeout: 120`, and once the initiator's `replacement_timeout` expires the SCSI layer
fails I/O and ext4 remounts read-only. A switch reboot is roughly 3–5 minutes
(`upgrade_duration` for the Rack Switch reads 320 s), so with 120 s **every** switch update
can remount volumes read-only. No amount of observation fixes that, so this goes first.

- Set `node.session.timeo.replacement_timeout = 600` on every node's initiator.
- **Open question 1: where it lives.** Talos runs `iscsid` from the iscsi-tools extension;
  democratic-csi's node plugin runs `iscsiadm` via nsenter
  (`infra/controllers/democratic-csi/values.yaml`). Find out whether the value can come from
  democratic-csi's iSCSI session options at login, or must be set in the extension's
  `iscsid.conf` via Talos machine config. Prefer the first, since it stays in this repo's
  Helm values.
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
start time, `time() - unpoller_device_uptime_seconds`, rounded to the minute:

1. Wait until uptime ≥ 15 min, so sessions have had the 600 s window to come back.
2. Run checks through the Prometheus and Loki APIs (no node access needed):
   - every `homelab_pvc_writable == 1`, and no `PvcNotWritable` firing or pending;
   - all nodes `Ready`, and the etcd member count is 3 with a leader;
   - Flux Kustomizations and HelmReleases are Ready (no new failures since boot);
   - no iSCSI error lines in Loki since boot + 10 min (the reconnect itself logs some).
3. Export `homelab_update_verification{device, boot, from_version, to_version, result}` and
   write one structured log line to Loki.

Outcomes:

- **Pass:** recorded in Grafana. No email.
- **Fail:** one **critical** email naming the device, versions and the failed checks, linking
  the recovery runbook (`AGENTS.md`, "Recovering read-only iSCSI volumes"), and
  `scripts/recover-readonly-pvc.sh` once the 2026-09-01 plan builds it. This is the only time George is pulled in.
- **Deadman:** `UpdateVerificationMissing` fires (critical) if a watched device's uptime is
  between 60 and 180 min with no `homelab_update_verification` for that boot. The upper bound
  stops it firing forever, and the window is wide enough to outlast one missed scrape or
  verifier restart. Silence then reliably
  means "verified fine".

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
