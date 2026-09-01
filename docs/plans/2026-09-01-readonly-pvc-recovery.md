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

Roughly monthly.

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
read-only at once, including seven Postgres clusters, from a ~20-second LAN
event. An auto-healer firing twenty remediations into an infrastructure event is
the worst available response. **The case that most needs a human is the case
automation would handle most aggressively.**

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
- Raising `node.session.timeo.replacement_timeout`. Checked 2026-09-01: live
  sessions report `Recovery Timeout: 120`, the open-iscsi default, and BGP
  recovered in about a minute on 8/13. The timeout is not the explanation, and
  changing it would be guessing at a mechanism we cannot evidence.

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

**Remount to page: 10m 14s.** Procedure recorded in `AGENTS.md` §
*Testing the alert (game-day)*.

**It found a bug on the first run.** Every annotation used `{{ $labels.namespace }}`
and `{{ $labels.pod }}`, which are the *probe's* labels, not the affected
workload's — the scrape collision prefixes the target's with `exported_`. A real
AdGuard failure would have paged `PVC monitoring/config-adguard-0`, sending the
operator to the wrong namespace. Fixed in
[#1389](https://github.com/gjcourt/homelab/pull/1389). The alert had never fired,
so its annotation had never been rendered — that is the argument for game-days.

## Acceptance

- A deliberate read-only remount on a test PVC is recovered by one invocation of
  the script, with Flux confirmed resumed afterwards.
- Aborting the script mid-run with `Ctrl-C` leaves Flux reconciling.
- The `PvcNotWritable` alert fires on that deliberate remount inside 10 minutes —
  the game-day that [#1300](https://github.com/gjcourt/homelab/issues/1300)'s
  acceptance criteria asked for and that has never actually been exercised.
