---
status: planned
last_modified: 2026-09-04
summary: "Diagnose why hestia's OS dies while its BMC stays up — two crashes on 2026-09-04, each taking all cluster storage read-only. Fixes the observability gap that makes the crashes self-concealing."
---

# Diagnosing hestia's crashes

hestia went down **twice on 2026-09-04**. Each time the BMC stayed reachable while
the OS was gone, and each time **all cluster storage went read-only** (59/63
volumes on the first, climbing on the second) because every StorageClass is iSCSI
to this host.

This is the highest-severity open problem in the cluster: hestia is a single point
of failure for all persistent state, and it has now failed twice in one day with
no root cause.

## What is already ruled out

| Hypothesis | Evidence | Verdict |
| :--- | :--- | :--- |
| **Thermal shutdown** | `thermalscope_cpu_temperature_celsius` flat at **61–64 °C** through 19:11, including under load. EPYC 8324P Tjmax ≈ 95 °C | ❌ **Ruled out** |
| **Power loss / PDU** | BMC runs on PSU standby and stayed up (ping, `:443`, `:623`) across both crashes | ❌ **Ruled out** |
| **Switch / VLAN** | BMC is on the same switch port (48) and stayed reachable | ❌ **Ruled out** |
| **Pool corruption** | After hard reset: `main` ONLINE, **zero data errors**, no resilver | ❌ Not the cause |

**The fault is in the OS or a driver: the kernel stops servicing the network and
storage stacks while the hardware stays powered.** No self-recovery; both crashes
needed a hard reset.

## ⚠️ The blind spot that must be fixed first

**Prometheus stores its TSDB on iSCSI backed by hestia.** When hestia dies,
Prometheus' own volume goes read-only and it **stops recording** — verified at
19:19Z, `/prometheus` returned `READONLY` while the BMC was still answering.

Every metric series ends at **19:11Z**, minutes before the crash, including the
BMC-sourced IPMI series. Not because the BMC failed — because the recorder did.

> **hestia crashing destroys the evidence of hestia crashing.**

Nothing downstream of this matters until it is fixed: any instrumentation added
below will be equally blind if it writes to hestia-backed storage.

## Phase 0 — Preserve what already exists (do before the next reboot)

The BMC's own logs survive OS death and power cycles. They are the only
first-hand record of both crashes.

1. **Pull the IPMI System Event Log.** Contains thermal trips, power events,
   ECC/memory errors, watchdog expiry, and PSU faults with timestamps:
   ```bash
   ipmitool -I lanplus -H 10.42.2.13 -U <user> -P <pass> sel list
   ipmitool -I lanplus -H 10.42.2.13 -U <user> -P <pass> sel elist   # extended
   ```
   ⚠️ **Do not clear the SEL.** Export first.
2. **Capture BMC sensor state** — `sdr list full`, `chassis status`, `power status`.
3. **Pull on-disk kernel logs from the crashed boots.** These survive the reset:
   ```bash
   journalctl --list-boots                 # identify pre-crash boots
   journalctl -b -1 -k --no-pager | tail -500
   journalctl -b -2 -k --no-pager | tail -500
   ```
   Look for: `Call Trace`, `BUG:`, `Oops`, `general protection fault`,
   `watchdog: BUG: soft lockup`, `Out of memory`, `mce:`, `EDAC`, `nvme ... timeout`,
   `ixgbe`/`i40e`/`igc` resets, `zfs`/`spl` traces, `hung_task`.
4. **Note whether the SEL shows an OS watchdog expiry.** That distinguishes a
   kernel hang (watchdog fires) from a silent driver wedge (nothing logged).

**If the SEL and prior-boot kernel logs are empty, that is itself a finding**: it
points at a hard hang with no panic path, which narrows toward firmware, a
non-maskable hardware fault, or a driver deadlock rather than a normal OOPS.

## Phase 1 — Close the observability gap

**These are prerequisites, not nice-to-haves.** Ordered by value.

1. **Move Prometheus (or a second scraper) off hestia-backed storage.** Options,
   cheapest first:
   - A small **local-path / node-local PV** for a dedicated "infra health"
     Prometheus that scrapes only BMC + node exporters. It must survive hestia.
   - Remote-write the hestia-related series to somewhere off-cluster.
   - At minimum, ship the IPMI exporter's scrape to a target that does not
     depend on hestia.
2. **Enable persistent kernel logging that survives a hang.** Volatile journald
   loses the last seconds — exactly the interesting ones:
   - `kdump` / crash dump to a **local** disk (not iSCSI).
   - `netconsole` to another host — streams kernel messages over UDP as they
     happen, so a hang cannot swallow them. **This is the highest-value single
     addition** for a box that dies without logging.
   - Serial-over-LAN console capture via the BMC (`ipmitool ... sol activate`)
     logged to a file on another machine.
3. **Enable the IPMI watchdog** so the BMC reboots the host on a kernel hang
   instead of waiting for a human. Reduces a multi-hour outage to minutes — and
   the SEL then records the watchdog expiry, which is itself diagnostic.
4. **Fix `ipmi_dcmi_power_consumption_watts`** — currently reads **0**, so
   there is no power-draw telemetry at all. Either the BMC does not implement
   DCMI or the exporter is misconfigured; without it, a PSU/current hypothesis
   cannot be tested.

## Phase 2 — Reproduce under instrumentation

**Do not skip straight to load testing.** Phase 1 must be in place or a
reproduction produces the same blind spot.

**Working hypothesis to test first: sustained heavy parallel I/O + CPU.** Both
crashes occurred during a 155-file OCR extraction (ffmpeg + tesseract,
`-P 8`/`-P 10`) reading from a ZFS dataset. That is correlation from two events,
not causation — but it is the only shared antecedent identified so far.

⚠️ **Counter-evidence that must be explained:** CPU temperature was **flat at
62 °C** during the second run, which is not what a genuinely loaded 64-thread
EPYC looks like. Either the job never ramped, or the crash preceded the load.
Establish which before trusting the hypothesis.

Reproduction protocol:

1. Confirm netconsole/SOL capture is live and logging to another host.
2. Apply load in **increasing steps** — `-P 2`, `-P 4`, `-P 8` — with several
   minutes at each level, watching CPU temp, fan RPM, voltages, ZFS ARC, and
   dmesg.
3. Record the step at which it fails, if it does.
4. **If it does not reproduce, the load hypothesis is wrong** and the two crashes
   share a different cause. Say so and re-open the search.

## Phase 3 — Other hypotheses, in priority order

| Hypothesis | How to test | Notes |
| :--- | :--- | :--- |
| **Memory / ECC** | SEL for correctable/uncorrectable ECC; `edac-util -v`; `memtest86+` if a maintenance window allows | 6× DDR5. ECC errors under load are a classic "dies only when busy" cause |
| **NVMe / boot device** | `smartctl -a` on boot NVMe; `thermalscope_nvme_*` series; SEL drive-fault state | A failing boot device wedges the OS while the data pool stays healthy — matches the symptom |
| **Kernel / ZFS bug** | TrueNAS SCALE version vs upstream bug reports for the running kernel and OpenZFS release | Check whether a recent TrueNAS update preceded the first crash |
| **Firmware** | BIOS/BMC versions on the ASRock Rack SIENAD8-2L2T vs current | Board/CPU-specific errata are plausible on EPYC 8004 |
| **NIC driver** | Whether the host is fully dead or only network-dead — SOL console proves this | If SOL is responsive while the network is gone, it is a NIC/driver fault, not a kernel hang |

**The SOL test in the last row is worth doing early** — it cheaply distinguishes
"whole kernel is hung" from "networking died", which splits the hypothesis space
roughly in half.

## Phase 4 — Reduce blast radius regardless of cause

Even with a root cause, hestia will remain a single point of failure for all
cluster storage. Independently worth doing:

- **IPMI watchdog** (Phase 1.3) so a hang self-recovers in minutes.
- **Alert on hestia unreachable** — currently the first signal is workloads going
  read-only ~15 minutes later, via `PvcNotWritable`. A direct ICMP/`:3260` probe
  from the cluster would fire in under a minute.
- **Document the recovery** — done, see
  `docs/operations/incidents/2026-09-04-hestia-down-mass-readonly.md` and the
  corrected procedures in `AGENTS.md`.
- ⚠️ **Fix the credential bootstrap gap.** BMC credentials live in `family/admin`
  **on hestia** — unreachable exactly when hestia is down, which is the only time
  they are needed.

## Success criteria

1. The SEL and prior-boot kernel logs for both 2026-09-04 crashes are exported and read.
2. Kernel messages from a future crash are captured off-host (netconsole or SOL).
3. Either the crash reproduces under instrumented load, or the load hypothesis is
   explicitly falsified.
4. A root cause is identified — **or** the watchdog is enabled so the failure mode
   becomes a short automatic reboot rather than a multi-hour manual outage.

## Immediate next step

**Phase 0.1 — export the SEL.** It is the only first-hand record of both crashes,
it survives reboots, and it takes one command. Everything else is downstream of
what it says.
