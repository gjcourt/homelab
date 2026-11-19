---
status: complete
last_modified: 2026-09-11
summary: "immich prod was completely down for ~16 hours and the operator found it before the monitoring did — not because detection failed, but because a broken volume that crashloops its pod downgrades from a critical page to a filtered warning; the worse outcome gets the quieter alert"
---

# Crashlooping workloads get the quieter alert

## What happened

On 2026-09-05 the operator reported *"immich isn't loading on prod"*. It had been
completely down for roughly **16 hours** — `immich-server` and
`immich-microservices` in `CrashLoopBackOff` at **248 restarts** each, on a
read-only `immich-upload-pvc`:

```text
ImmichStartupError: Failed to write "<UPLOAD_LOCATION>/encoded-video/.immich"
  at StorageService.verifyWriteAccess
```

**A human noticed before the alerting escalated.** That is the failure this plan
is about.

## ⚠️ Detection was not the problem

The first theory was that `pvc-writeprobe` is blind to crashlooping pods, because
it execs into running pods to test a write. **That is wrong**, and it is worth
recording because it is the intuitive answer:

| Signal | Did it fire? | Evidence |
| :--- | :--- | :--- |
| `homelab_pvc_probe_unknown` | ✅ **yes**, for both immich namespaces and `prometheus-1` | the probe emits this precisely when it *cannot* classify a volume |
| `PvcWriteProbeUnknown` | ✅ **reached `firing`** | 769 / 822 / 634 / 479 samples (immich-prod), 1811 / 967 (immich-stage) |
| `KubePodCrashLooping` | ✅ **fired** | 959 samples immich-prod, 968 immich-stage, 969 monitoring |
| `PvcNotWritable` | ❌ **could not fire** | see below |

**The monitoring worked.** Three independent signals fired, for hours.

## The actual gap: severity is inverted relative to impact

```text
severity = critical  ->  gjcourt+critical@gmail.com   (pages)
severity = warning   ->  gjcourt+alerts@gmail.com     (filtered)
```

Every signal that fired was **`severity: warning`** — `PvcWriteProbeUnknown`,
`KubePodCrashLooping` (`warning`, `for: 15m`), and `KubePodNotReady` (`warning`,
`for: 15m`). All three went to the filtered mailbox. Nothing paged.

The only rule that pages is `PvcNotWritable` (`severity: critical`), and its
expression is `homelab_pvc_writable == 0` — **which requires a pod healthy enough
to exec into.** A pod whose volume is so broken that the app cannot start never
produces that series at all.

> **A volume breaks. If the app survives it, you get paged. If the app dies of
> it, you get a filtered warning.** The worse outcome is the quieter one.

`PvcWriteProbeUnknown` also carries `for: 45m`, deliberately, so a rolling restart
stays quiet — reasonable on its own, but it adds 45 minutes to the wrong branch of
that fork.

## Why the current design is defensible, and where it breaks

The rule's own comment is honest about the trade-off:

> *"A sustained 'unknown' is not the same as a failure, but it IS a blind spot …
> Warning, not critical — it does not mean data is at risk, it means we cannot
> see."*

**That reasoning is sound in general and must not simply be inverted.** ⚠️
`probe_unknown` has a **benign steady-state baseline** — as of 2026-09-06 it is
`1` for `immich-stage/immich-photos-pvc` on pods that are `Running` and healthy.
Promoting `probe_unknown` to `critical` wholesale would page on volumes that are
fine.

**The discriminator is not "can we see the volume" but "is the workload up".**

## Proposed fix

Add a **compound** rule: unknown writability **and** the pod is not ready. That is
the case where the app is actually down, and it should page.

✅ **SHIPPED 2026-09-11** as `PvcWorkloadDownUnknownVolume` in
`infra/configs/pvc-writeprobe/prometheus-rule.yaml`.

⚠️ **The expression originally proposed here was WRONG, and the reason is worth
keeping.** This plan (2026-09-06) claimed the obvious `on(namespace, pod)` join
returns zero and prescribed a `label_replace` off `exported_namespace` instead.
**That is backwards.** `honorLabels: true` had already landed in
[#1389](https://github.com/gjcourt/homelab/pull/1389), so the probe's own
`namespace`/`pod` labels win and there is no `exported_` prefix at all.

**The 2026-09-06 measurement was taken against retained series from before
#1389**, which still carried the old label shape. Testing "against live
Prometheus" is not the same as testing against *current* series — old series
stay queryable until they age out, and a query written against them can look
correct and then match nothing.

The shipped expression is the simple one:

```yaml
expr: |
  homelab_pvc_probe_unknown == 1
    and on(namespace, pod)
  (kube_pod_status_ready{condition="true"} == 0)
for: 10m
labels:
  severity: critical
```

Verified 2026-09-11 against current series:

| Test | Result |
| :--- | :--- |
| control, `ready == 1` | **2 series** — the join binds |
| alert, `ready == 0` | **0 series** — correctly quiet while healthy |
| cross-check on `homelab_pvc_writable` | **75 of 75** join to `ready == 1` |

**The lesson the 09-06 version got right and then failed to apply to itself:** a
rule returning zero is indistinguishable from a rule that works. Flip the
comparison and confirm it returns something. That check was written into the plan
and still missed the error, because the control direction *also* returned a
plausible-looking non-zero — off stale series.

### Alternatives considered

| Option | Why not (yet) |
| :--- | :--- |
| Promote `PvcWriteProbeUnknown` to `critical` | Pages on the benign baseline above |
| Shorten its `for: 45m` | Helps latency, not routing; still a warning, still filtered |
| Promote `KubePodCrashLooping` to `critical` | Pages for every crashlooping workload cluster-wide, including ones with no persistence and no user impact. Too broad |
| Probe from the PV side instead of the pod | Genuinely better — it would not depend on a healthy pod at all — but it is a rewrite of the probe, not a rule change. Worth considering separately |

## Success criteria

1. A workload that is **down** because its volume is unwritable produces a
   **critical** page within ~15 minutes.
2. The benign `probe_unknown` baseline does **not** page.
3. Verified by an actual test, not by reading the expression.

### How to test it

⚠️ **Do not try to reproduce a read-only volume for this.** `remount,ro` does not
reproduce the real failure (see the game-day section in
[AGENTS.md](../../AGENTS.md#recovering-read-only-iscsi-volumes-recurring)), and
this alert does not need it. The condition is *`probe_unknown` **and** pod not
ready* — so the cheap, honest way to trigger it is to make a PVC-mounting pod
crashloop for any reason:

```bash
# a staging workload that mounts a PVC; a bad tag is enough
kubectl -n memos-stage set image deploy/memos memos=does-not-exist:0
# pod goes not-ready; probe cannot classify its volume -> both halves true
```

Then check, in order:

| Stage | Expect |
| :--- | :--- |
| `homelab_pvc_probe_unknown` for that pvc | flips to `1` (next sweep, `INTERVAL_SECONDS=300`) |
| the alert expression | returns ≥1 series |
| `ALERTS{alertname="PvcWorkloadDownUnknownVolume"}` | `pending`, then `firing` after `for: 10m` |
| routing | lands in **`+critical@`**, not `+alerts@` — this is the whole point |

Roll back with `kubectl -n memos-stage rollout undo deploy/memos`.

⚠️ **Confirm it reaches the critical mailbox.** Everything else in this plan is
about routing, so a test that stops at "the rule fires" has not tested the thing
that actually failed on 2026-09-05.

## Open question worth resolving first

**Is `warning` → filtered mailbox the right routing at all?** Three separate
alerts fired for 16 hours and none reached the operator. Fixing this one rule
makes *this* failure page, but the same structure will hide the next one that
happens to land on `warning`. **Any rule change here is a patch on a routing
policy that may itself be the problem** — worth deciding deliberately rather than
by adding one more `critical`.

## Related

- Incident: [2026-09-04 hestia down, mass read-only](../operations/incidents/2026-09-04-hestia-down-mass-readonly.md)
- Recovery procedure: [AGENTS.md](../../AGENTS.md#recovering-read-only-iscsi-volumes-recurring)
- The probe and its rules: `infra/configs/pvc-writeprobe/`
- [#1300](https://github.com/gjcourt/homelab/issues/1300) — detection-to-page latency target of 5 minutes, currently ~15–16 min and not met
