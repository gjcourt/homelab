---
status: planned
last_modified: 2026-09-01
summary: "read-only iSCSI remounts have recurred six times and always need an operator; detection is now solved by pvc-writeprobe, so close the remaining gap with a recovery script that encodes the runbook's traps — and deliberately do NOT build a controller that suspends Flux or restarts Postgres"
---

# Plan: recovery for read-only iSCSI volumes

## Context

`AGENTS.md` calls this **"the most common failure mode in this cluster."** Every
StorageClass is `democratic-csi` over iSCSI to hestia, so any disruption on that
path causes I/O errors and Linux remounts the affected filesystems read-only.
**Nothing recovers from that automatically — not the kernel, not Kubernetes, not
CNPG, not Flux. It always needs an operator.**

Six recurrences on record:
[2026-02-08](../operations/incidents/2026-02-08-pv-recovery.md) ·
[2026-02-12](../operations/incidents/2026-02-12-iscsi-zombie-targets.md) ·
[2026-02-15](../operations/incidents/2026-02-15-iscsi-targets-disabled.md) ·
[2026-02-27](../operations/incidents/2026-02-27-homeassistant-staging-iscsi-io-error.md) ·
[2026-02-28](../operations/incidents/2026-02-28-iscsi-mass-readonly-cnpg-loki-immich.md) ·
[2026-08-13](../operations/incidents/2026-08-13-iscsi-readonly-remount-monitoring-blind.md).
Tracking issue [#1080](https://github.com/gjcourt/homelab/issues/1080).

**Bursty, not periodic** — five inside three weeks of February, then a 5.5-month
gap, then one in August. That shape matters for the Tier 2 gate below: a burst
is survivable manually, a steady drip is not.

## What is already solved — do not rebuild it

**Detection is done.** `infra/configs/pvc-writeprobe/` execs real bytes into every
read-write PVC mount and exports `homelab_pvc_writable{namespace,pvc,pod}`, with
`PvcNotWritable` (critical, `for: 10m`) plus `PvcWriteProbeUnknown` / `Stale` /
`Absent` so that the probe's own silence alerts. Verified live 2026-09-01: **75
PVCs probed, all writable.**

⚠️ **Detection-to-page is up to ~15 minutes, not seconds.** `INTERVAL_SECONDS:
300` — the probe sweeps every **5 minutes** — and `PvcNotWritable` has
`for: 10m` on top. [#1300](https://github.com/gjcourt/homelab/issues/1300) asks
for "an alert inside 5 minutes"; that criterion is **not currently met**.
Closing it requires either accepting ~15m or shortening one of the two
intervals. (An earlier draft of this plan said the sweep ran "every ~14s" —
that was a misread of `time() - homelab_pvc_writeprobe_last_run_seconds`, which
is the age of the last completed sweep, not the interval.)

⚠️ **Do not propose `node_filesystem_readonly` for this.** It cannot work.
Measured 2026-08-26: ext4's `emergency_ro` error mode fails every write with
`EROFS` while the mount still advertises `rw`, and node-exporter reports what the
mount claims. On 2026-08-13 two AdGuard volumes were hard-`EROFS` to a real write
while the gauge read `0` for 13 days. **No mount-flag scan can close this,
because the mount flag is the thing that is lying.** This was proposed again on
[#1300](https://github.com/gjcourt/homelab/issues/1300) on 2026-09-01 before the
runbook was read; recorded here so the next person does not spend the same hour.

**So the remaining gap is recovery, not detection.**

## Why a controller is the wrong shape

Recovery differs by workload, and two of the four paths are things a robot must
not do unsupervised.

| Workload | Action | Safe to automate |
|---|---|---|
| StatefulSet pod (Prometheus, Loki) | `kubectl delete pod` | ✅ recreated in place |
| CNPG **primary** | restart — never the PVC | ⚠️ this is a switchover |
| CNPG **replica** | delete pod; *sometimes* delete its PVC | ❌ a robot deleting PVCs |
| Deployment on RWO | suspend Flux → scale 0 → wait for `volumeattachment` empty → scale 1 → resume Flux | ❌ see below |

**The Deployment path disqualifies full automation.** It requires suspending
production Flux reconciliation. A controller that dies between suspend and resume
leaves the cluster with Flux off — a worse outage than the read-only volume it
was fixing. `AGENTS.md` already warns a *human* about exactly this: *"if you
abort partway, resume Flux first."* A process with no operator watching it has no
equivalent of that instruction.

**And mass failure inverts the value.** On 2026-08-13 roughly twenty volumes went
read-only at once, and **six CNPG clusters lost redundancy**, from a **two-minute
link flap** — two carrier drops across ~2m20s. An auto-healer firing twenty
remediations into an infrastructure event is the worst available response. **The
case that most needs a human is the case automation would handle most
aggressively.**

## Tier 1 — `scripts/recover-readonly-pvc.sh` (build this)

One command encoding the runbook, with a `trap` so Flux resumes on any exit path
including `Ctrl-C`.

It exists to carry the three traps already documented in `AGENTS.md`, each of
which has cost real time:

1. **`rollout restart` is not sufficient on RWO.** It creates the replacement pod
   before terminating the old one, so the new pod attaches to the same errored
   device and comes up read-only again.
2. **Pod termination is not proof the device was released.** `volumeattachment`
   reaching zero is. The script must poll for that, not for pod count alone.
3. **`wc -l` on BSD/macOS emits leading whitespace**, so a string comparison
   against `"0"` never matches and the wait loop hangs — *with production
   reconciliation suspended*. Use `-eq`, and pipe through `tr -d ' '`.

Requirements:

- `--dry-run` prints the plan and touches nothing.
- Refuses to run against a CNPG cluster; prints the CNPG section of the runbook
  and exits. That path needs judgement about replica readiness.
- Resumes Flux in a `trap ... EXIT INT TERM`, unconditionally, and verifies with
  `flux get kustomizations -A` before returning.
- Verifies writability after recovery with a real write, not pod status.

**This is most of the value.** It removes the failure modes, not the typing.

## Tier 2 — narrow controller, only if Tier 1 proves insufficient

`kubectl delete pod` on StatefulSet members whose PVC is unwritable. No Flux
suspend, no PVC deletion, no switchover, no Deployments. Covers Prometheus and
Loki — the workloads where the fix is a single safe verb.

**Gate:** only build this if the script is being run often enough to be a chore
after it exists. Six incidents in seven months does not obviously clear that bar.

## Tier 3 — circuit breaker, mandatory on anything in Tier 2

If more than `N` PVCs report unwritable simultaneously, **refuse to act and
page**. Mass failure is an infrastructure event, and the correct automated
response is to stop and escalate. Without this, Tier 2's blast radius on a repeat
of 2026-08-13 is every StatefulSet at once.

## Explicitly out of scope

- Anything that suspends Flux without a human watching.
- Anything that deletes a PVC.
- Anything that triggers a CNPG switchover.
- Anything that changes the *storage transport*. That is prevention, not
  recovery, and it now has a live candidate of its own — see below.

## ⚠️ Prevention: `replacement_timeout` is back on the table

An earlier revision of this plan ruled this out of scope on a **wrong number**.
It said the 2026-08-13 trigger was "a ~20-second LAN event", which is comfortably
inside the open-iscsi default of 120s, so the timeout could not be the
explanation.

**The actual outage was ~2m20s** — the incident's own headline is *"A two-minute
link flap cost 14 hours."* Two carrier drops, 03:01:10 to 03:03:31. Live sessions
report `Recovery Timeout: 120`.

**141 seconds is longer than 120.** Once `replacement_timeout` expires, the SCSI
layer stops queueing and starts failing I/O, and ext4 remounts read-only. That is
a coherent mechanism for the whole incident, and it is the one lever that would
have prevented it rather than detected it.

Raising it trades a longer I/O *hang* for avoiding a read-only *remount* — on a
homelab where the alternative has cost 14 hours twice, that trade looks
obviously right, but it is a real trade and wants deciding rather than assuming.

**This belongs in its own issue, not this plan.** Recorded here because the
dismissal was wrong and someone will otherwise re-derive it. Note the target-side
`conn_rsp_timer_fn: Timeout 30 sec ... closing connection` in the incident log is
a *different* timer (SCST closing the connection) and does not substitute for the
initiator-side setting.

## Game-day result — 2026-09-01

**`PvcNotWritable` fired for the first time.** Run against a throwaway PVC on
`truenas-iscsi-ephemeral` in namespace `readonly-gameday`, forced read-only with
`mount -o remount,ro /data`:

| Time (UTC) | Event |
| --- | --- |
| 18:24:22 | `homelab_pvc_writable` = 1 |
| — | `mount -o remount,ro /data`; writes return `EROFS` |
| 18:24:46 | metric flips to **0** (next sweep) |
| 18:34:36 | alert **firing**, `exported_namespace=readonly-gameday` |

**Last-good-to-page: ≤10m 14s.** Measured from the last known-good sample
(18:24:22), not from the remount itself — the remount was not timestamped, so
this is an upper bound with unknown slack. Record the remount time next run.
Procedure in `AGENTS.md` § *Testing the alert (game-day)*.

⚠️ **This run did not reproduce the failure the probe exists for.**
`mount -o remount,ro` sets the mount flag to `ro` — which is the one variant
`node_filesystem_readonly` *can* detect. The real 2026-08-13 failure was ext4's
`emergency_ro`, where every write returns `EROFS` while the mount **still
advertises `rw`**. So this game-day validated the probe's exec path, its metric,
and the alert plumbing — but it did **not** exercise the discriminating advantage
that justifies the probe existing at all. A stronger test needs a device-level
error (dm-error, or flapping an iSCSI target against a throwaway LUN).

**It found a bug on the first run.** Every annotation used `{{ $labels.namespace }}`
and `{{ $labels.pod }}`, which are the *probe's* labels, not the affected
workload's — the scrape collision prefixes the target's with `exported_`. A real
AdGuard failure would have paged `PVC monitoring/config-adguard-0`, sending the
operator to the wrong namespace. Fix proposed in
[#1389](https://github.com/gjcourt/homelab/pull/1389) (open at time of writing). The alert had never fired,
so its annotation had never been rendered — that is the argument for game-days.

## Acceptance

- A deliberate read-only remount on a test PVC is recovered by one invocation of
  the script, with Flux confirmed resumed afterwards. **Not yet done — the script
  does not exist.**
- Aborting the script mid-run with `Ctrl-C` leaves Flux reconciling. **Not yet
  done.**
- ✅ **The `PvcNotWritable` alert fires on a deliberate remount.** Done
  2026-09-01, measured at **≤10m 14s** last-good-to-page. See the game-day
  section above. This satisfies #1300's *intent*; it does **not** satisfy its
  stated "inside 5 minutes", which remains unmet at ~15–16 min worst case.
