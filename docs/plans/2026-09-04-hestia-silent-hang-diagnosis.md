---
status: in-progress
last_modified: 2026-09-05
summary: "hestia failed four times in ~29 hours after 57 days of uptime in TWO distinct modes — two kernel panics that self-rebooted in ~3 min and two true hard lockups that sat until reset — and left no record of either because the box had no console, no crashkernel and an ERST pstore that captured nothing; detectors are now armed and one suspect is stopped as a live experiment"
---

# hestia's silent hangs

Supersedes [#1400](https://github.com/gjcourt/homelab/pull/1400), which was
written mid-incident and argued a thermal / observability problem. **Its premise
was wrong on every count** — see [What #1400 got wrong](#what-1400-got-wrong).

## The shape of the failure

hestia **froze four times in ~29 hours** after running **57 days without a
reboot**. Each freeze:

- **Stopped logging mid-sentence.** Nothing was *written* — no panic trace, no
  oops, no OOM, no soft-lockup, no RCU stall, no hung-task warning, no MCE, no
  AER. The journal's last line is routine and there is nothing after it.
  ⚠️ **"Nothing logged" is not "nothing happened":** freezes 2 and 4 panicked and
  rebooted, and still left no record, because the box had no `console=`, no
  `crashkernel=`, and an ERST pstore that captured nothing.
- **Left the machine powered.** Fans spinning, `VCORE` at 0.90 V, BMC answering
  ICMP in 3.5 ms. **This is not a power event** — a PSU fault powers the box
  *off*.
- **Recovery time was not uniform**, and this is unresolved. Two freezes sat for
  hours; two came back in ~3 minutes:

  | Freeze | Froze | Next boot | Gap |
  | :--- | :--- | :--- | :--- |
  | 1 | Sep 3 11:17:06 | Sep 4 07:51:43 | ~20.5 h |
  | 2 | Sep 4 07:51:43 | Sep 4 07:54:45 | **3 min** |
  | 3 | Sep 4 12:06:53 | Sep 4 15:56:50 | ~3.8 h |
  | 4 | Sep 4 16:06:12 | Sep 4 16:09:30 | **3 min** |

  ✅ **RESOLVED 2026-09-05 — there are two distinct failure modes.** The
  operator confirms he reset the box only at the two long gaps (the `07:51` and
  `15:56` boots). **Nobody restarted it at `07:54` or `16:09`.** Everything else
  that could have is ruled out:

  | Candidate | State at the time | Verdict |
  | :--- | :--- | :--- |
  | Operator | confirmed did not touch it | ❌ |
  | IPMI watchdog | `Stopped`, `No action`, `Expiration Flags: None` | ❌ |
  | BMC power action | SEL cleared 15:46 and recording; **no entry at 16:06** | ❌ |
  | `ipmitool` from the operator's Mac | no power command issued after 15:52 | ❌ |

  **hestia rebooted itself**, and with `panic_on_oops=1` + `panic=10` already set
  that is a **kernel panic**. `~3 min` = the 10 s panic timeout plus POST (a BMC
  cold reset needed 120 s, so POST on this board is minutes). journald confirms
  the shutdown was unclean: *"File …/system.journal corrupted or uncleanly shut
  down"* on the 16:09 boot.

  | | Freezes 1 & 3 | Freezes 2 & 4 |
  | :--- | :--- | :--- |
  | Behaviour | sat until manual reset | **self-recovered in ~3 min** |
  | Mechanism | true hard lockup — nothing detected it | **panic → auto-reboot** |

  ⚠️ **The panic message did not survive.** `pstore` is mounted with the `erst`
  backend but is **empty** — ERST captured nothing. With no `console=` and no
  `crashkernel=` on that boot either, the panic rebooted the box without leaving
  a single byte anywhere.

  **This materially improves the outlook: the panic path demonstrably works on
  this machine.** `hardlockup_panic=1` (now set) should therefore convert the
  freeze-1/3 lockup mode into the same ~3-minute auto-reboot, and `crashkernel=`
  (armed) should capture what ERST could not.

```text
Jul 08 06:37  →  Sep 03 11:17     57 days, zero reboots
Sep 03 11:17     freeze 1  (~20 h before anyone noticed)
Sep 04 07:51     freeze 2  — died ONE SECOND into boot, before ZFS imported
Sep 04 12:06     freeze 3
Sep 04 16:06     freeze 4
```

Boot history is from `zpool history main | grep "zpool import"` — every boot
imports the pool, so it is a reliable reboot log long after journald has rotated.

⚠️ **Freeze 2 is the most informative event we have.** It died **one second into
boot**, during `systemd-journal-flush`, immediately after `zfs-volumes.target`
and *before the pool was imported*. No containers, no workload, no iSCSI
sessions, cold CPU. **That single fact eliminates load, thermal, ZFS-under-
pressure, and any application-level trigger.**

## Ruled out, with data

| Hypothesis | Evidence | Verdict |
| :--- | :--- | :--- |
| **CPU thermal** | flat **61–64 °C** through the crash window under load; EPYC 8324P Tjmax ≈ 95 °C | ❌ |
| **NVMe thermal** | peak **49.85–53.85 °C** across all 9 devices; throttle is ~70–80 °C | ❌ |
| **NVMe health** | `media_errors`, `error_log_entries`, `critical_warning`, `percentage_used` — **all zero, all 9 drives** | ❌ |
| **Memory / ECC** | zero MCE, zero EDAC errors across every boot (the 43 "matches" found were boot-time controller *enumeration*) | ❌ |
| **PCIe / AER** | `aer_dev_correctable/fatal/nonfatal` all zero on both NICs and both bridges | ❌ |
| **Power loss** | machine stayed **on** through every freeze — fans, CPU rails, BMC | ❌ |
| **Software crash (oops/panic)** | `panic_on_oops=1` + `panic=10` were already set. Freezes 1 and 3 sat for hours, so those were **not** panics. ✅ **Freezes 2 and 4 self-rebooted in ~3 min with every other cause excluded — those WERE panics** | ✅ Confirmed for 2 & 4 |
| **Application load** | freeze 2 happened 1 s into boot with nothing running | ❌ |

**For freezes 1 and 3, what is left is a total system freeze on healthy
hardware** — every CPU stops servicing interrupts at once, which is why nothing
can be written. **Freezes 2 and 4 were panics** (resolved 2026-09-05, above), so
the box has shown *two* failure modes and any theory has to account for both —
or explain why one fault sometimes trips a detectable path and sometimes wedges
everything.

## Why there is no evidence — the actual finding

**The kernel was configured to hang silently.** Four settings, all on the running
kernel at the time:

```text
kernel.hardlockup_panic = 0     detector fires, declines to panic -> hangs forever
kernel.softlockup_panic = 0     same
/proc/cmdline: no console=      kernel writes nothing to serial; SOL is blank
/proc/cmdline: no crashkernel=  kdump never configured; no dumps
```

⚠️ **The NMI watchdog was enabled and still printed nothing.** A *detected* hard
lockup logs `watchdog: Hard LOCKUP` even with `hardlockup_panic=0`. It did not,
which means the freeze was complete enough that the NMI handler could not run.

A second gap compounded it: **the BMC's SEL had been full since 2026-05-13** —
3,602 `PCI PERR` events filled it and it silently discarded everything after. It
recorded **neither** of the crashes it was supposed to be the record of. It has
since been dumped (3,639 entries preserved) and cleared.

> **The instrumentation gap is the finding.** Root cause is still unknown, and
> the reason is that every channel that could have captured it was off.

## Done on 2026-09-04

| Change | State | Effect |
| :--- | :--- | :--- |
| `kernel.hardlockup_panic=1` | **live** + persisted as a TrueNAS SYSCTL tunable | next hard lockup **panics and auto-reboots in 10 s** (`panic=10`) instead of hanging for hours |
| `console=tty1 console=ttyS0,115200` | in grub.cfg, **arms next boot** | kernel output reaches BMC SOL, capturable off-host with `ipmitool … sol activate \| tee` |
| `crashkernel=412M` + `kdump-tools` | in grub.cfg, **arms next boot** | crash dump on panic |
| SEL dumped and cleared | done | BMC can record events again after 114 days deaf |
| `gha-runner` stopped | done | see [the experiment](#the-running-experiment) |
| 10 GbE cutover | done | unrelated to the hangs; storage path is now 10 Gb |

⚠️ **`serialspeed` was 9600 while the BMC's SOL runs at 115.2 kbps.** Both are now
115200. Enabling the console without fixing that would have produced garbage.

**The single highest-value change is `hardlockup_panic=1`**, because it converts
the *outage* into a blip even if it never explains the *cause*: a multi-hour dead
box becomes a ~1-minute automatic reboot.

## The running experiment

**`gha-runner` is stopped, and hestia is being watched for hangs.**

It was in a permanent SIGABRT crash-loop, restarting at Docker's 60 s backoff cap
and tearing down a network namespace and veth pair each cycle — a known
kernel-hang class, and the only workload running continuously on a previously
stable box. Root cause of *that* loop is understood and documented
([gha-runner runbook §2.3](../operations/apps/gha-runner.md)); it is a failed
self-update, and the fix is verified but deliberately not applied yet.

**Check it with:**

```bash
ssh truenas_admin@10.42.2.10 'uptime; sudo zpool history main | grep "zpool import" | tail -3'
```

A climbing uptime with no new import means no hang. **Baseline: booted
2026-09-04 16:10.**

⚠️ **The experiment is weak and should not be over-read.**

- **The correlation is loose.** The runner broke **Sep 2 08:32** (from the
  `_update.sh` header); the first freeze was **Sep 3 11:17** — **27 hours of
  crash-looping with no hang** in between.
- **It is not single-variable.** A heavy OCR workload also stopped at the same
  time. Partial mitigation: freeze 1 happened with no OCR running.
- **Absence of a hang is not proof.** hestia went 57 days between events; a quiet
  week is suggestive, not conclusive.
- **Freeze 2 argues against it entirely** — one second into boot, before Docker
  or the runner existed.

**Decide by ~2026-09-11.** Either way `gha-runner` should be restarted after
that, because hestia GitOps is dead while it is down (every `hosts/hestia/**`
change silently queues).

## Remaining hypotheses

Ordered by what the evidence supports. **All are unproven.**

| Hypothesis | Why it is live | How to test |
| :--- | :--- | :--- |
| **Kernel / out-of-tree module deadlock** | ZFS 2.4.1 and `iscsi_scst` (187 refs) are out-of-tree on a **beta** kernel (`6.18.13-production+truenas`, TrueNAS **26.0.0-BETA.1**) | Next panic trace via SOL/kdump names the subsystem. Check upstream bug trackers for the exact versions |
| **Firmware / SMM** | An SMI storm freezes every core with the NMI watchdog unable to run — matches "watchdog enabled but silent" exactly | Compare BIOS/BMC versions (ASRock Rack SIENAD8-2L2T, BMC fw 2.05) against current; check errata |
| **CPU / platform fault** | A freeze this complete on healthy-looking hardware is consistent with a VRM or SoC fault | Hard to test without a spare; treat as residual after the above |
| **Something changed ~Sep 2–3** | 57 days stable then four freezes is a step change, not drift | ⚠️ **Unresolved.** Journal retention was ~2 days (the runner's 3,931 coredumps and 96 % of kernel-log volume rotated everything). Nothing on disk covers the transition |

⚠️ **The `PCI PERR` storm is NOT a live hypothesis.** 3,602 events, 04/27–05/13,
attributed here twice to a NIC — wrongly. The reporting device is **bus `c6`, the
ASPEED AST1150 bridge** (BMC video), not the X710 (`0d:00.0/.1`) and not the I210
(`c8`/`c9`). It predates the hangs by four months and stopped only because the
log filled. It is unexplained, but it is not evidence for this.

## Next steps

1. **Let the experiment run to ~2026-09-11**, then restart `gha-runner` regardless
   of outcome and record the result here.
2. ✅ **DONE 2026-09-05 — freezes 2 and 4 were panics.** See the gap table. The
   remaining question is *what* panicked, which needs the armed capture below.
   ⚠️ **Also worth fixing: `pstore`/ERST is mounted and captured nothing.** It
   should have caught these panics and did not — so kdump is the only capture path
   left, and it is unproven on this box.
3. **Reboot into the armed settings at the next convenient window** — `console=`
   and `crashkernel=` do nothing until then, and a reboot means another
   mass-read-only recovery, so it should be planned rather than incidental.
4. **Verify SOL actually carries the console after that reboot.** It was blank on
   09-04 because the kernel had no `console=`; that should now be fixed, but it is
   unverified.
5. **Fix log retention.** With the runner stopped the volume drops enormously, but
   journald should be sized so a 2-day window cannot happen again — it is why the
   Sep 2–3 transition is unrecoverable.
6. **Alert on hestia being unreachable.** Today the first signal was workloads
   going read-only ~15 minutes later via `PvcNotWritable`. A direct ICMP/`:3260`
   probe would fire in under a minute.
7. **Decide whether a beta OS belongs under all cluster storage.** TrueNAS
   26.0.0-BETA.1 has been stable for two months, so this is a risk-posture
   question, not a diagnosis.

## Blast radius — worth fixing regardless of cause

**Every StorageClass is `democratic-csi` over iSCSI to hestia.** One host freezing
took **59/63 volumes read-only** on 09-04. Recovery is manual and documented in
[AGENTS.md](../../AGENTS.md#recovering-read-only-iscsi-volumes-recurring); the
incident is
[2026-09-04-hestia-down-mass-readonly.md](../operations/incidents/2026-09-04-hestia-down-mass-readonly.md).

⚠️ **Bootstrap gap:** BMC credentials live in `family/admin` **on hestia** —
unreachable exactly when hestia is down, which is the only time they are needed.

## Success criteria

1. A future hang produces a **panic trace** (SOL or kdump) instead of silence.
2. That trace names a subsystem, **or** the hang stops recurring and the change
   that stopped it is recorded.
3. **Failing both**, `hardlockup_panic=1` holds and the failure mode is a
   ~1-minute reboot rather than a multi-hour outage — an acceptable floor.

## What #1400 got wrong

Recorded so the errors are not repeated, since each cost real time:

- **Built Phase 0 on the BMC SEL** as the record of both crashes. It had been
  full since May and contained neither.
- **Called thermal the leading hypothesis.** It was ruled out with data in the
  same session.
- **Claimed `ipmi_dcmi_power_consumption_watts` was broken** and needed fixing.
  hestia has an **ATX PSU with no PMBus** — there is no telemetry to fix, and the
  `No Reading` on every PSU sensor is expected, not a fault.
- **Attributed the `PCI PERR` storm to a dual-port NIC** and recommended swapping
  in a spare card. There is no add-in NIC in the chassis, and the reporting device
  is the ASPEED bridge.
- **Asserted the OS was alive and "only the NIC died"** from a painted console
  menu, without ever confirming the console echoed a keystroke.
