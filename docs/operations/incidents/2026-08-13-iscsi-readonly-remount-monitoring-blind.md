# Incident: LAN blip remounted ~20 iSCSI volumes read-only; Prometheus went mute for 12 hours

**Date:** 2026-08-13
**Status:** **Resolved** — all workloads recovered, 12/13 CNPG clusters healthy (overture is deliberately scaled 0/0), alerts 21 → 5 (0 critical). Detection gap and recovery gotchas tracked as follow-ups; see [Prevent recurrence](#prevent-recurrence)
**Severity:** High — `immich-prod`'s database was unavailable for ~14h (every query `FATAL`), 6 CNPG clusters lost redundancy, and **monitoring was blind for 12 hours** while reporting a storm of false criticals
**Environments affected:** production and staging
**Authors:** George Courtsunis

---

> **A two-minute link flap cost 14 hours.** The network healed itself unaided. Every consequence that followed — a dead database, a monitoring blackout,
> nineteen critical alerts — persisted for twelve hours because nothing in the
> cluster can recover from a read-only remount on its own, and the one system that
> should have raised the alarm was itself the loudest casualty.

## Summary

At **10:01 UTC** hestia's Ethernet carrier dropped. The kernel log is unambiguous:

```
03:01:10 PDT  igb enp201s0: NIC Link is Down
03:03:16      NIC Link is Up 1000 Mbps Full Duplex
03:03:28      NIC Link is Down
03:03:31      NIC Link is Up
```

Two flaps across **~2m20s**. iSCSI sessions died inside that window:

```
03:02:07  iscsi-scst: conn_rsp_timer_fn: Timeout 30 sec ... closing connection
          iscsi-scst: session_free: Freeing session (SID 2f2300003d0200)
```

Linux did what it always does on a storage error: it **remounted the affected filesystems
read-only**. Roughly **20 volumes** across nearly every stateful workload flipped, and
stayed flipped — ext4 never remounts read-write by itself.

**This was a physical LAN event, not an upstream one.** hestia↔cluster iSCSI is
same-subnet `10.42.2.x` traffic that never touches the WAN, so neither a Comcast outage
nor a modem reboot can explain carrier loss on hestia's own NIC. The DNS timeouts to
`1.1.1.2` were a *symptom* of hestia being off the network, not the cause. A power event
is also excluded: nothing rebooted — all four Talos nodes have been `Ready` since
2026-06-21/07-01, and hestia has 36 days of uptime.

The `Down → Up → Down → Up` signature points at the **switch port, cable, or switch
itself** — port renegotiation, not a routing or upstream fault. Worth correlating against
the UniFi controller for a switch reboot or port-flap event at 03:01 PDT; a marginal cable
that flaps under thermal cycling would recur.

**The storage was never at fault.** Every component blamed by the alerts was healthy
throughout:

| Checked | State |
|---|---|
| ZFS pools (`main`, `boot-pool`) | `all pools are healthy`, no reboot, 36d uptime |
| SCST iSCSI target | `active`, running since **2026-07-08** — never restarted |
| hestia load | 2.29 / 2.16 / 2.10 — normal |
| Cilium BGP | re-established on its own roughly six minutes after dropping |
| Kubernetes | 4 nodes `Ready`, all `kube-system` pods up |

The incident surfaced at **22:37 UTC** as "a number of alerts firing" — twelve and a
half hours after it began.

### This is a recurrence

The same failure mode is documented five times already:
[2026-02-08](2026-02-08-pv-recovery.md) ·
[2026-02-12](2026-02-12-iscsi-zombie-targets.md) ·
[2026-02-15](2026-02-15-iscsi-targets-disabled.md) ·
[2026-02-27](2026-02-27-homeassistant-staging-iscsi-io-error.md) ·
[2026-02-28](2026-02-28-iscsi-mass-readonly-cnpg-loki-immich.md) — the last of which hit
the *same* CNPG + Loki + immich combination. What was new this time was the monitoring
blackout, not the storage failure. The recovery knowledge existed but was spread across
six documents nobody reads at 3am; it is now consolidated in `AGENTS.md`.

Note one case from 2026-02-28 that this incident did not reproduce: a read-only CNPG
*replica* PVC that came back **empty**, requiring `delete pvc` as well as `delete pod`.
Deleting a replica PVC is safe (CNPG re-clones from the primary); deleting a *primary's*
PVC destroys the data.

## Why 21 alerts fired, and why they were all wrong

Prometheus's own PVC was among the volumes that went read-only. That single fact
produced the entire storm:

```
Scrape commit failed  err="write to WAL: log samples: write /prometheus/wal/00002108: read-only file system"
Rule sample appending failed  err="write to WAL: ... read-only file system"
```

Prometheus kept **running and evaluating rules** but could not ingest a single sample.
Every `absent()`-based alert therefore fired — `KubeAPIDown`, `KubeletDown`,
`KubeControllerManagerDown`, `KubeSchedulerDown`, `CertManagerDown`, `LokiDown`,
`PromtailDown` — while the API server was demonstrably fine, being actively queried by
the operator throughout the investigation.

Worse, the alerts were **frozen**. With no ingestion, Prometheus could not record the
recovery either, so `CiliumBGPNoPeers` kept firing for **twelve hours after BGP came
back**. Measured mid-incident:

```
Local AS  Peer AS  Peer Address    Session      Uptime      Advertised
65010     65100    10.42.2.1:179   established  12h37m58s   8
```

The two replicas failed differently, and the difference matters:

- **`prometheus-1`** crashlooped **151 times** — a cold start must *create*
  `/prometheus/queries.active`, which panics on a read-only filesystem.
- **`prometheus-0`** stayed `Running` with **0 restarts** and looked perfectly healthy.
  It held its file handles open, so it never crashed — it simply ingested nothing.

A pod that looks fine and silently does nothing is worse than one that crashes.

## Timeline

All times UTC.

| Time | Event |
|---|---|
| 10:01:10 | **hestia `enp201s0` NIC Link is Down** — the root cause |
| 10:01:40 | hestia logs the first of 118 DNS timeouts (`10.42.2.1`, `1.1.1.2`) |
| 10:02:07 | iSCSI sessions time out and are freed; volumes begin remounting read-only |
| 10:03:16–10:03:31 | Link up, down, up — second flap; carrier stable thereafter |
| ~10:06 | BGP session actually drops (inferred: `CiliumBGPNoPeers` has `for: 5m`) |
| 10:11 | `CiliumBGPNoPeers` fires |
| ~10:12 | BGP re-establishes on its own (inferred from `established 12h37m58s` measured at ~22:50) |
| 10:15–10:16 | `MqttscopeMetricsAbsent`, `NetscopeMetricsAbsent`, `ModemscopeMetricsAbsent` |
| 10:21 | The wave: `KubeAPIDown`, `KubeletDown`, `KubeControllerManagerDown`, `KubeSchedulerDown`, `CertManagerDown`, `LokiDown`, `LokiCanaryDown`, `PromtailDown` |
| 11:05–11:06 | 8× `HomelabscopeJobMetricAbsent` |
| **10:21 → 22:37** | **Nothing. Monitoring blind, alerts frozen, no page.** |
| 22:37 | Operator notices "a number of alerts firing"; investigation starts |
| 22:41 | Root cause identified — `read-only file system` in the Prometheus panic |
| 22:43 | `prometheus-1` recovered by pod delete; volume writable |
| 22:45 | `prometheus-0` recovered — ingestion restored, 19 criticals begin clearing |
| 23:00–23:40 | Loki, Home Assistant ×2, zigbee2mqtt, mosquitto, audiobookshelf, snapcast, mealie ×2 recovered |
| 23:45–00:10 | `immich-prod` recovered (primary first, then both replicas); flashcards, golinks, linkding, memos, vitals replicas recovered |
| ~00:30 | `jellyfin-prod` recovered via scale-to-0 with Flux suspended; Flux resumed |

## Why immich died and the others only degraded

Five other CNPG clusters lost replicas but kept serving. `immich-prod` lost all three
instances. The reason is placement:

```
RO   immich-db-prod-cnpg-v3-5      talos-kot-7x7
RO   immich-db-prod-cnpg-v3-6      talos-ykb-uir
RO   immich-db-prod-cnpg-v3-7      talos-fpd-h0t

ok   golinks-db-production-cnpg-v1-6    talos-2mz-rfj   ← primary survived
ok   linkding-db-production-cnpg-v1-4   talos-2mz-rfj   ← primary survived
ok   vitals-db-production-cnpg-v1-6     talos-2mz-rfj   ← primary survived
```

**`talos-2mz-rfj` (.21) came through untouched**, and golinks, linkding and vitals each
happened to have their primary there. Immich had **nothing on .21** — its three
instances were spread across the three affected nodes.

Anti-affinity, which normally protects against correlated failure, is exactly what sank
it: a three-way spread guarantees exposure to a fault that hits three of four nodes.

Node placement is a strong correlate but not deterministic — `vitals-7` on `ykb-uir`
survived while `golinks-7` and `linkding-6` on the *same node* did not. The real
granularity is the **individual iSCSI session**, not the node.

Immich's symptoms:

```
# primary, reporting ready=True:
psql: FATAL:  could not open file "base/5/2601": Read-only file system

# both replicas, first line of every start attempt:
Error while checking if there is enough disk space for WALs, skipping
  while opening size probe file: open .../pg_wal/_cnpg_probe_sdG2: read-only file system
```

The application layer never fell over — `immich-server`, `immich-microservices`, four
`machine-learning` pods and `redis` were all `Running` with **0 restarts** and no errors.
A healthy app on a database that could not answer.

## Why nobody was warned

Three independent detection failures stacked.

**1. The deadman watches the wrong failure.** The healthchecks.io dead man's switch
([#1286](https://github.com/gjcourt/homelab/pull/1286)) fires when `Watchdog` *stops
arriving* — i.e. when Alertmanager dies or the notification path breaks. Alertmanager was
perfectly healthy here. Prometheus went **mute**: still up, still evaluating, ingesting
nothing. `Watchdog` kept flowing. **A mute Prometheus is invisible to a deadman built
around a live one.**

**2. Readiness probes cannot see a read-only volume.** `immich-db-prod-cnpg-v3-5`
reported `ready=True` while `psql` returned `FATAL` on every query. `prometheus-0`
reported healthy while ingesting nothing. TCP and process-liveness probes both pass
happily on a filesystem that has stopped accepting writes. This is the same gap flagged
while reviewing [#1262](https://github.com/gjcourt/homelab/pull/1262) — a TCP readiness
probe on an app whose SQLite lives on RWO iSCSI cannot detect the failure that matters.

**3. The correct alert already existed and still did not save us.** `NodeFilesystemReadOnly`
is defined in `infra/configs/alerts/prometheus-rules.yaml` at `severity: critical`, with a
description that names this exact scenario — "This is the iSCSI volume read-only failure
pattern." node-exporter is even deliberately configured to keep `/var/lib/kubelet` in scope
so PVC mounts are visible (`infra/controllers/kube-prometheus-stack/values.yaml`, citing
#1080).

It never fired, because **it is evaluated by the Prometheus whose own volume had gone
read-only**. This is the sharpest lesson available here: writing the right alert is not
enough when the evaluator shares a failure domain with the thing it watches.

The 19 criticals were, ironically, *evidence* of the outage — but they read as
"everything is broken", which is indistinguishable from "the monitoring is broken", and
they were delivered into a mailbox with no one watching at 03:00 local time.

## Fixes

Recovery is the same for every affected workload: **force an iSCSI detach and reattach**.
For most, deleting the pod suffices.

```bash
kubectl -n monitoring delete pod prometheus-kube-prometheus-stack-prometheus-1
kubectl -n monitoring delete pod prometheus-kube-prometheus-stack-prometheus-0
kubectl -n monitoring delete pod loki-0
kubectl -n <ns> rollout restart deploy/<app>
```

For CNPG, **restart the primary first** — replicas stream WAL from it and cannot sync
until it serves:

```bash
kubectl -n immich-prod delete pod immich-db-prod-cnpg-v3-5   # primary; wait for 2/2
kubectl -n immich-prod delete pod immich-db-prod-cnpg-v3-6
kubectl -n immich-prod delete pod immich-db-prod-cnpg-v3-7
```

There is no promotion risk while the replicas are unready, and no downside to restarting
a primary that is already answering nothing.

### Three recovery facts that cost time

**`rollout restart` does not always work.** It creates the new pod *before* terminating
the old, so on an RWO volume the replacement can attach to the same errored device and
come up read-only again. `jellyfin-prod` failed this way and needed the full cycle.

**Flux fights you.** Scaling a Deployment to 0 is reverted within the reconcile interval,
which silently undoes the recovery. Production reconciliation must be suspended for the
duration.

**Verify the detach.** `volumeattachment` count must reach zero before scaling back up —
that is the actual signal the device was released, not pod termination.

```bash
flux suspend kustomization apps-production -n flux-system
kubectl -n jellyfin-prod scale deploy/jellyfin --replicas=0
until [ "$(kubectl -n jellyfin-prod get pods --no-headers 2>/dev/null | wc -l | tr -d ' ')" -eq 0 ]; do sleep 5; done
kubectl get volumeattachment | grep <pv-name>          # must be empty
kubectl -n jellyfin-prod scale deploy/jellyfin --replicas=1
flux resume kustomization apps-production -n flux-system   # do NOT forget
```

### Verification

```
alerts:          21 → 5   (19 critical → 0)
unhealthy pods:  0
CNPG clusters:   12/13 healthy (overture excluded — scaled 0/0 deliberately)
flux:            6/6 reconciling, SUSPENDED=False
```

Writability confirmed per workload rather than inferred from pod status:

```bash
kubectl -n <ns> exec <pod> -c <ctr> -- sh -c 'touch /path/.wtest && rm -f /path/.wtest'
```

## Prevent recurrence

| Gap | Action |
|---|---|
| Prometheus can go mute undetected | Alert on ingestion stalling, and extend the deadman to cover a live-but-mute Prometheus rather than only a dead Alertmanager |
| Read-only remounts are invisible | Alert on `node_filesystem_readonly` and/or a write-canary probe across PVCs |
| Readiness probes pass on read-only volumes | For stateful workloads, make readiness actually touch the volume (`exec` write, or an HTTP `/healthz` that does a trivial DB write) |
| Recovery knowledge was scattered across six incident docs | Consolidated into `AGENTS.md` → "Recovering read-only iSCSI volumes", the first place an operator or agent looks |

The underlying single point of failure is unchanged: every StorageClass in this cluster is
`democratic-csi` against hestia over iSCSI, so a network event between the cluster and
hestia can read-only-remount most of the estate at once. That is a known, accepted
tradeoff — but it raises the value of detecting the condition quickly, because the
recovery is manual by nature.

## Lessons

**A monitoring system that fails loudly is safer than one that fails silently — but a
system that fails *noisily and wrongly* is the worst of the three.** Nineteen criticals
naming healthy components is not a signal, it is camouflage. The one alert that would
have been true and useful — "Prometheus has not ingested a sample in 5 minutes" — did not
exist.

**Liveness is not the same as usefulness.** Two of the three worst symptoms here were
processes that were `Running`, `ready=True`, and completely non-functional. Any health
check that does not exercise the resource it depends on will eventually certify a corpse.

**Self-healing infrastructure heals only what it is designed to.** BGP recovered in about
a minute with no help. The filesystems never recovered at all, because nothing in Linux,
Kubernetes, CNPG or Flux treats a read-only remount as a condition to reconcile away. The
gap between "the network is fine" and "the cluster is fine" was twelve hours wide.

**Anti-affinity is not a guarantee of survival.** Spreading three replicas across three
nodes protects against one node failing and guarantees exposure when three do. Immich was
the only cluster to lose everything precisely *because* it was the most evenly spread.
