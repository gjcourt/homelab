---
title: Talos bench diagnostics — .22 (talos-v2l-hng) & .24 (talos-18u-ski)
status: Stable
created: 2026-07-08
updated: 2026-07-08
updated_by: gjcourt
tags: [operations, talos, hardware, memtest, nvme, thermal, bench]
---

# Talos Bench Diagnostics — `.22` (talos-v2l-hng) & `.24` (talos-18u-ski)

Bench runbook for **two decommissioned Talos boxes**, worked **by hand at the bench**
off a single SystemRescue USB stick. Repair one, re-qualify the other, and — only if
they pass — reprovision both as **plain workers** and burn them in before returning
them to the cluster.

> **Read this first — no cluster interaction here.** Both boxes are **off the rack,
> pulled, and independently powered on the bench.** They are **not** cluster members
> right now: `.22`'s etcd member was removed during the `.23`→control-plane promotion
> and `.24` was deleted as a stale node object. So **none** of the etcd-quorum /
> "power off the neighbors" / drain-and-cordon caveats from
> [`2026-06-17-talos-node-maintenance.md`](2026-06-17-talos-node-maintenance.md)
> apply. **Do not run any `talosctl`/`kubectl` against these boxes on the bench** —
> there is nothing live to protect and no quorum to tip. The only live-cluster step is
> the final re-integration (§4), and it treats each box as a brand-new worker.

## The two boxes

| Box | Node name | Was | Fault on record | Bench objective |
|---|---|---|---|---|
| `.22` | `talos-v2l-hng` | control-plane / etcd member | **Confirmed bad DIMM** — reported **30.6 GiB vs ~62 GiB** nominal (a stick died/unseated). Decommissioned 2026-06-19. CPU-heat is **suspected but UNCONFIRMED** (no thermal data on record; metrics aged out). | **Repair-and-prove:** re-paste CPU, swap in good RAM, then run the full validation battery. |
| `.24` | `talos-18u-ski` | worker | **None.** Was healthy (full 62 GiB; the box thermalscope was hardware-verified on 2026-06-16). Powered off only as a co-located neighbor for `.22`'s maintenance, deleted as a stale node object during the `.23`→CP promotion, physically pulled, never re-racked. | **Re-qualify-and-return:** straight to the validation battery. Expected to pass. |

Both are **AMD** boxes (they carry the `amdgpu` Talos extension), so CPU temperature
comes from the **`k10temp`** sensor — watch **`Tctl`**; the throttle limit (`Tjmax`)
is ≈ **95 °C**. Install / boot disk on both is **`/dev/nvme0n1`** (~62 GiB nominal RAM).

## What you need

- **One USB stick: [SystemRescue](https://www.system-rescue.org/)** (`systemrescue.org`).
  It boots **Memtest86+** straight from its menu, and a Linux environment with
  **`nvme-cli`**, **`smartctl`** (smartmontools), **`stress-ng`**, and **`lm-sensors`**
  all preinstalled. Everything in this runbook runs off that one stick — no network,
  no second tool.
- Thermal paste + isopropyl (≥90 %) + lint-free wipe, for `.22`.
- The known-good spare RAM stick(s), for `.22`.
- A notepad (or the record sheet in §5) to log pass/fail per check per box.

---

## 0. Prep — write the USB and boot each box

1. **Write the SystemRescue USB** (on this Mac or any workstation). Download the ISO
   from `systemrescue.org`, verify its checksum, and write it to the stick. Example
   (macOS — **double-check `diskN` with `diskutil list`; writing the wrong disk
   destroys it**):
   ```
   diskutil list                         # identify the USB, e.g. /dev/disk4
   diskutil unmountDisk /dev/diskN
   sudo dd if=systemrescue-*.iso of=/dev/rdiskN bs=4m status=progress
   sync
   ```
2. **Boot the target box from it.** Insert the stick, power on, and open the **UEFI
   boot menu** (typically `F11` / `F12` / `Del` on this hardware — mash it at POST).
   Pick the USB stick as a **UEFI** boot device.
3. From the SystemRescue boot menu you can launch **Memtest86+** directly (§2.1), or
   boot the **SystemRescue Linux** environment for the disk / thermal / log checks
   (§2.2–§2.4).

Do the boxes **one at a time** — one stick, one bench.

---

## 1. Per-box procedure — which path

### `.22` (talos-v2l-hng) — repair first, then validate

Open the case, then:

1. **Fresh thermal paste.** Remove the heatsink, clean old paste off the CPU IHS and
   cooler cold-plate with isopropyl + lint-free wipe, apply a fresh pea-sized dot,
   re-seat the cooler evenly.
2. **Swap in the good RAM.** Remove the dead/suspect stick, install the known-good
   spare, seat it fully in the correct channel slot (match the board's DIMM layout).
3. Run the **full validation battery** (§2) and apply the **verdict logic** (§3).

> **On the CPU-heat question (unconfirmed):** if you want a real before/after paste
> delta, run the thermal test (§2.3) **once before** touching the cooler and **again
> after** re-pasting, and compare the load `Tctl`. Since you're re-pasting anyway, the
> **minimum bar is the post-paste run** — that's the one the verdict uses. A large
> idle→load drop after pasting is the paste-quality signal.

### `.24` (talos-18u-ski) — straight to validation

No RAM swap, no re-paste up front (no fault on record). Run the **full validation
battery** (§2). Only open it for RAM/paste work if a check actually **fails** — then
fix and re-run the failed check.

---

## 2. Validation battery (both boxes) — run in this order

Run every check on **each** box. Record the result. Order matters: RAM first (a bad
stick corrupts everything downstream), then disk, then thermal-under-load, then a log
sweep for anything the earlier steps kicked up.

### 2.1 RAM — Memtest86+

Boot **Memtest86+** from the SystemRescue menu. Let it run **at least 2–4 full passes**
(a full pass covers all test patterns across all installed RAM; several hours on 62 GiB
— run it overnight if you can).

- **Red flag: ANY error address reported.** A single failing address = fail. Memtest
  errors are never "noise."
- **`.22`:** this is the box whose whole reason for being here is a bad DIMM — Memtest
  must come back **100 % clean on the swapped-in good RAM** before you trust it.
- **`.24`:** expected clean. If it errors, `.24` has a newly-discovered fault — stop
  and treat it like `.22` (RAM swap).

Record: passes completed, and **clean / errored (+ first error address)**.

### 2.2 NVMe SMART health

Boot the **SystemRescue Linux** environment. Confirm the disk is the boot/install NVMe
first, then pull both the NVMe-native log and the smartmontools view:

```
nvme list                          # confirm /dev/nvme0n1 is the ~62 GiB install disk
nvme smart-log /dev/nvme0n1
smartctl -a /dev/nvme0n1
```

Read these fields (thresholds in the verdict table, §3):

| Field (`nvme smart-log`) | Red flag |
|---|---|
| `critical_warning` | **≠ 0** (any bit set) → **FAIL** |
| `media_and_data_integrity_errors` | **> 0** → **FAIL** (uncorrectable data errors) |
| `available_spare` vs `available_spare_threshold` | `available_spare` **<** `available_spare_threshold` → **FAIL** |
| `percentage_used` | **≈ 100 % or above** → wear-out, **FAIL** (it can exceed 100) |
| `num_err_log_entries` | **rising / large** → investigate (soft flag) |
| `unsafe_shutdowns` | **informational only** — high count is expected here (these boxes were pulled without graceful shutdown). Do **not** fail on it. |

Record the five verdict fields verbatim.

### 2.3 CPU thermal under load

Still in SystemRescue Linux. Load `k10temp` and stress all cores while watching the
temperature:

```
modprobe k10temp 2>/dev/null; sensors-detect --auto >/dev/null 2>&1 || true
sensors | grep -iE "k10temp|Tctl"           # note the IDLE Tctl first
```

Then, in **two panes / two TTYs** (SystemRescue has `tmux`):

```
# pane 1 — load all logical CPUs for 10 minutes
stress-ng --cpu $(nproc) --timeout 600s

# pane 2 — watch Tctl once a second for the whole run
watch -n1 sensors
```

- Note **idle `Tctl`** (before load) and **sustained load `Tctl`** (steady-state near
  the end of the 600 s run).
- **Red flag: sustained `Tctl` near ~95 °C, or any thermal throttling** (clocks
  dropping, `dmesg` thermal events — see §2.4). Healthy desktop-class AMD parts under
  an all-core load sit well below Tjmax with a good cooler.
- The **idle→load delta** is the **paste-quality signal for `.22`.** A well-pasted,
  well-seated cooler shows a controlled rise and a steady-state comfortably under
  Tjmax. A small idle temp but a fast runaway toward 95 °C = bad mount / bad paste →
  re-seat and re-run.

Record: **idle `Tctl`**, **sustained load `Tctl`**, and **throttle? yes/no**.

### 2.4 Stability / log sweep

After the battery (especially right after the thermal run), scan the kernel log for
machine-check, ECC/EDAC, NVMe, and thermal events:

```
dmesg | grep -iE "mce|edac|nvme|thermal"
```

- **Red flag:** any **`mce`** (machine-check exception), **`edac`** memory-controller
  error, NVMe controller reset/error, or **thermal throttle** event.
- A **clean / empty** result here is the "nothing latent" confirmation. Note that on a
  fresh boot the log is short — this is a point-in-time check, so run it **after** the
  stress + Memtest work has had a chance to provoke anything.

Record: **clean / events found (paste the lines)**.

---

## 3. Verdict logic + thresholds

Apply per box, per check. **Any FAIL on a check = that component fails** → replace the
flagged part (RAM stick / NVMe) or RMA the box; do **not** reprovision a box with an
open FAIL. All four PASS → the box is cleared for reprovision (§4).

| Check | PASS | FAIL → action |
|---|---|---|
| **RAM (Memtest86+)** | ≥ 2–4 full passes, **zero** error addresses | **Any** error address → replace that DIMM, re-run Memtest from scratch |
| **NVMe `critical_warning`** | `0` | ≠ 0 → replace the NVMe (SSD flagging itself) |
| **NVMe `media_and_data_integrity_errors`** | `0` | > 0 → replace the NVMe (uncorrectable data corruption) |
| **NVMe `available_spare`** | ≥ `available_spare_threshold` | < threshold → replace the NVMe (spare blocks exhausted) |
| **NVMe `percentage_used`** | comfortably < 100 % | ≈ 100 %+ → replace the NVMe (worn out) |
| **NVMe `num_err_log_entries`** | low / not climbing | large or rising across reads → investigate before trusting |
| **CPU thermal** | sustained load `Tctl` clear of ~95 °C, **no** throttle | sustained ~95 °C or any throttle → re-seat/re-paste and re-run; if it persists, the cooler/CPU is the fault |
| **dmesg sweep** | no `mce` / `edac` / nvme-error / `thermal` lines | any such line → the named component is the fault, investigate before proceeding |

Per-box expectation:

- **`.22`** should PASS **only after** the RAM swap (and post-paste thermal). If
  Memtest still errors on the good stick, suspect the **slot / memory controller**, not
  just the DIMM — try the spare in a different slot before condemning the board.
- **`.24`** is expected to PASS clean with no intervention. A FAIL here means the box
  picked up (or was hiding) a real fault — handle it like `.22`.

---

## 4. Reprovision + re-integrate (both boxes, only if they PASS)

> **Disk note (intentional wipe):** re-imaging Talos **erases `/dev/nvme0n1`.** That is
> **fine and expected** — nothing on these boxes is preserved. They were pulled from the
> cluster; all cluster storage is network-attached iSCSI on hestia/TrueNAS (see
> [`2026-06-17-talos-node-maintenance.md`](2026-06-17-talos-node-maintenance.md)), so
> there is **no local data to save.** Just confirm you're wiping **`nvme0n1`** (the
> ~62 GiB install disk), not a stray USB/other disk — re-run `nvme list` if unsure.

Re-image each cleared box as a **plain Talos worker**:

- Machine config install disk: **`machine.install.disk: /dev/nvme0n1`**.
- Bring it up with the **worker** config (config source: `~/src/melodic-muse/` —
  `worker.yaml` / `patch.yaml` / `talosconfig`; same tree used in the
  [control-plane promotion runbook](2026-06-19-talos-controlplane-promotion.md)).

> ### ⛔ Do NOT re-add either box as control-plane / etcd
> Reprovision **both as workers only.** A box that just came off the bench under
> suspicion **must not touch etcd quorum** — a flapping suspect member can tip the
> control plane. This applies **especially to `.22`** (the ex-CP with the confirmed
> hardware fault): it rejoins as a **worker**, never as a control-plane / etcd member.
> The cluster already restored a fault-tolerant 3-member etcd via the `.23` promotion;
> leave that alone. (Only ever promote a worker to CP later via the deliberate
> [promotion runbook](2026-06-19-talos-controlplane-promotion.md), and only a box with
> a clean track record.)

**Burn-in before trusting it with real work** — join it cordoned and tainted so nothing
schedules onto it until you've watched it under the cluster's own monitoring:

```
# after the node registers and goes Ready:
kubectl cordon <node>
kubectl taint nodes <node> node.homelab/bench-burnin=true:NoSchedule
```

- **Leave it UNLABELED** — do **not** apply `cpu-tier=high` (or any workload-steering
  label). It carries no targeted workload during burn-in.
- Let it sit Ready + cordoned + tainted and **watch it under monitoring** (node stays
  Ready, no MCE/EDAC in node logs, temps sane under whatever ambient load, no NVMe
  SMART regressions) for a burn-in window — an hour-plus of stable idle, longer if you
  can.
- When it's proven stable, **release it:**
  ```
  kubectl taint nodes <node> node.homelab/bench-burnin=true:NoSchedule-   # remove taint
  kubectl uncordon <node>
  ```
  Now normal workloads can schedule. Re-apply any intended workload label only after
  it's carrying general load cleanly.

**Context / related runbooks** (this bench procedure is standalone; these are for the
cluster-side story):

- [`2026-06-17-talos-node-maintenance.md`](2026-06-17-talos-node-maintenance.md) — safe
  power-down / restore of *live* nodes and the iSCSI storage model.
- [`2026-06-19-talos-controlplane-promotion.md`](2026-06-19-talos-controlplane-promotion.md)
  — the CP promotion that decommissioned `.22`'s etcd member and deleted `.24`; the
  **only** sanctioned path to re-add a control-plane member later.

---

## 5. Quick record sheet

Fill one per box.

```
Box: ______ (.22 talos-v2l-hng / .24 talos-18u-ski)     Date: __________

.22 only — repair done?   paste: [ ]    good RAM installed: [ ]

2.1 RAM (Memtest86+)      passes: ____   result: [ ] clean  [ ] ERROR @ ________
2.2 NVMe SMART            critical_warning: ____   media_errors: ____
                         available_spare: ____ / threshold ____   pct_used: ____%
                         num_err_log_entries: ____   -> [ ] PASS  [ ] FAIL
2.3 CPU thermal          idle Tctl: ____°C   load Tctl: ____°C   throttle: [ ]y [ ]n
2.4 dmesg sweep          [ ] clean   [ ] events: ____________________________

VERDICT: [ ] PASS -> reprovision as WORKER (cordon+taint, burn in, then uncordon)
         [ ] FAIL -> replace/RMA: ____________  (do NOT reprovision)

Reprovision:  worker config [ ]   install disk /dev/nvme0n1 confirmed [ ]
              NOT re-added as control-plane/etcd [ ]   left unlabeled [ ]
```
