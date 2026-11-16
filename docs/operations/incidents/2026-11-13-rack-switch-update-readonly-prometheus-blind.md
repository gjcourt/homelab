---
status: resolved
last_modified: 2026-11-15
summary: "The Rack Switch's unattended 03:12 firmware update outlasted the 120 s iSCSI replacement_timeout: 18 volumes went read-only, including Prometheus and Loki, so monitoring recorded nothing for 17h43m. Fixed by a 600 s timeout, an update-verifier, and a game day that passed."
---

# 2026-11-13: Rack Switch update, 18 volumes read-only, Prometheus blind for 17h43m

Another instance of the recurring iSCSI read-only failure (see `AGENTS.md`, "Recovering
read-only iSCSI volumes"). What set this one apart: the trigger was a **scheduled firmware
update**, and it took **Prometheus's own volume** with it, so the cluster could not report
its own outage.

All times are PST (UTC−8). Source for every number below: Prometheus, queried 2026-11-15
(retention still covered the day), unless marked otherwise.

## Timeline

| Time (11-13) | |
| :--- | :--- |
| 03:12 | Rack Switch (USWED42) reboots into firmware 7.6.11.17272 (from 7.5.15.17146). Cause: Network-app device auto-update, `mgmt.auto_upgrade_hour = 3` |
| 03:14 | Last Prometheus sample (`count(up)`). Nothing is recorded again until 20:57 |
| 03:14–20:54 | No record. Talos kernel logs were not reaching Loki either (Vector rendered no DaemonSet until #1932, 2026-11-15), so there is no kernel or iSCSI log for the reboot itself |
| 20:38–20:46 | CNPG pods in `plane-stage`, `plane-prod` and `flashcards-prod` are replaced (pod start times) |
| 20:54–20:55 | Both Prometheus replicas, Loki, and the pods holding the read-only volumes below are replaced within about a minute of each other |
| 20:57 | Prometheus ingests again |
| 20:58 | First write-probe sweep with data: **64 of 82 probed volumes writable, 18 read-only** (`homelab_pvc_writable == 0`), 12 unknown |
| 21:00 | 88 writable; no volume reports `0` again |
| 21:15 | `audiobookshelf-stage` and `homeassistant-stage` pods replaced |
| 21:20 | 91 writable; back to the pre-incident count (91 at 02:30) |

The 20:54–20:55 replacements were the recovery (the runbook's `kubectl delete pod`, one per
affected workload). The record doesn't show who noticed the outage or how.

## The 18 read-only volumes (20:58)

| Namespace | Volume | Kind |
| --- | --- | --- |
| monitoring | `prometheus-…-prometheus-0` | Prometheus TSDB |
| monitoring | `storage-loki-0` | Loki |
| flashcards-prod | `flashcards-db-production-cnpg-v1-2` | CNPG |
| golinks-prod | `golinks-db-production-cnpg-v1-6`, `-v1-7` | CNPG (2 of the cluster's instances) |
| linkding-prod | `linkding-db-production-cnpg-v1-5` | CNPG |
| memos-prod | `memos-db-production-cnpg-v1-5`, `-v1-6` | CNPG (2 instances) |
| homeassistant-prod, -stage | `homeassistant-config-pvc` | Deployment |
| audiobookshelf-prod, -stage | `audiobookshelf-meta-data-pvc` | Deployment |
| plane-prod, plane-stage | `pvc-plane-minio-vol-plane-minio-wl-0` | StatefulSet |
| snapcast-prod | `snapcast-music-assistant-data`, `snapcast-spotify-state` | Deployment |
| vibrato-stage | `vibrato-data` | Deployment |
| zigbee2mqtt | `zigbee2mqtt-data-pvc` | Deployment |

## Why it happened

A switch reboot cuts every node's iSCSI session to hestia at once. The initiators queued
I/O for `replacement_timeout` (open-iscsi default **120 s**, which every session was using),
then failed it, and ext4 remounted those filesystems read-only (`emergency_ro`). A Rack
Switch update takes longer than 120 s (`upgrade_duration` reads 320 s). Which volumes
broke depended on which ones had writes queued when the timer ran out, which is why 18
failed and not all of them.

The target side was not a factor: hestia closes a dead connection within 60–90 s
regardless and the initiator logs in to a fresh session, so the initiator's
`replacement_timeout` is the only deadline (worked through from SCST source in the plan
below).

## Why nobody was told for 17 hours

- **Prometheus's own volume went read-only.** Both replicas stopped ingesting at 03:14.
  A Prometheus that ingests nothing evaluates every rule against nothing, so no rule could
  fire for the outage it was in.
- **`PvcNotWritable` never fired.** Its `for: 10m` needed ten minutes of data; once data
  returned at 20:58 every volume had already been recovered by 21:00. In the first
  minutes, only `PvcWorkloadDownUnknownVolume` fired (up to 4 series between 20:58 and 21:38).
- **Loki was also read-only**, and Talos kernel logs weren't being shipped at all, so the
  iSCSI error alert had nothing to read.
- **The off-cluster deadman should have caught it.** `IngestionWatchdog` stops pinging
  healthchecks.io once samples stop, so an email to `+critical@` was due about 21 minutes
  after 03:14. **Not verified:** whether healthchecks.io sent that email can only be
  checked in its dashboard or the inbox.

## What changed

Plan: [unattended UniFi updates, observed and verified](../../plans/2026-11-14-switch-update-observability-plan.md).

| Change | PR | State |
| --- | --- | --- |
| `replacement_timeout` 600 s for every StorageClass | #1933 | Live: every node drained 2026-11-15, 80 of 80 sessions at 600 |
| Device auto-update turned off until the rest landed | (UniFi setting) | Back on after the game day |
| Record-only update detection (unpoller) | #1929 | Live |
| update-verifier: checks storage, nodes, etcd, Flux and Loki after each reboot, posts failures straight to Alertmanager (works with Prometheus blind) | #1931 | Live; caught the 11-15 gateway update |
| Talos kernel logs reach Loki (Vector role fix, host-network receiver, per-node label) | #1932, #1936, #1937 | Live |
| Drain runbook handles the `maxUnavailable: 0` PDBs | #1939 | Merged |
| Game day: George restarted the Rack Switch, 18:09 on 11-15 | — | **Pass**: down 2m50s, every session recovered, 92/92 volumes writable, verifier `pass`, no email |
| Write probe no longer publishes an empty sweep when the API is down (seen in the game day) | #1956 | Open |
| Verifier checks BGP (the 11-15 gateway update stopped FRR for 21 h, unnoticed) | #1955 | Open |

An outage longer than 600 s still fails I/O and remounts read-only, as before. 600 s
covers a switch update with room to spare; it does not cover hestia going down.

## Open

- Check whether healthchecks.io emailed `+critical@` around 03:35 on 11-13. If it didn't,
  the last line of defence for a blind Prometheus is broken.
- The 2026-08-13 incident also started at 03:01, the same auto-update hour. That one
  predates unpoller version history, so it fits an update but can't be proven.
