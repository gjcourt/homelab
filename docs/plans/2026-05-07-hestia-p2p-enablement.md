---
status: planned
last_modified: 2026-05-07
---

# Hestia P2P enablement (sub-plan to vLLM frontier experiments)

## Context

The vLLM frontier-model experiments plan (PR #558, file [`docs/plans/2026-05-07-vllm-frontier-model-experiments.md`](2026-05-07-vllm-frontier-model-experiments.md)) accepted hestia's `NODE` GPU topology as a frozen constraint and scoped GPU P2P enablement out. Follow-on investigation showed that constraint is *not* actually frozen — there's a path to same-Root-Complex topology on this board, and same-RC topology is the precondition for any meaningful P2P win.

The new evidence, from `lspci -tv` + `nvidia-smi --query-gpu=pci.bus_id`:

1. The Siena 8004 IOD on this board exposes **three PCIe Root Complexes**: `0000:00`, `0000:40`, `0000:c0`. Not one. Not two.
2. The two 4090s are currently split across RCs: GPU0 in PCIE5 (`pci.bus_id=00:01.1` → bus `01`, RC `00`), GPU1 in PCIE3 (`40:03.1` → bus `45`, RC `40`). That's the literal reason `nvidia-smi topo --matrix` says `NODE`.
3. The user has **two Crucial T710 4×NVMe bifurcation cards** populating PCIE1 + PCIE7. The kernel sees 8 NVMe drives split across two RCs: 4 at buses `41–44` (RC `0000:40`), 4 at buses `c1–c4` (RC `0000:c0`).
4. Therefore: **one of {PCIE1, PCIE7} is already on RC `0000:40` — the same RC as PCIE3 / GPU1.** That slot, paired with PCIE3, is a *same-RC* x16/x16 pair on this board. Moving one GPU into it should drop topology from `NODE` to `PHB`/`PXB`.

The path is: identify which slot's T710 set is on RC `0000:40`, move one GPU there (riser cable, since the T710 set has to come out and the GPU has to take its place), do the BIOS pass + NVIDIA driver patch, re-run the same vLLM TP=2 measurement that produced 33 t/s pre-plan. If the number comes up materially, fold a TP=2-with-P2P phase back into the main plan and recover the option of running models that don't fit single-GPU.

This sub-plan separates the question from the main vLLM plan because the win/lose outcome is binary and quick to test, and the main plan stays valid regardless.

## Goals

1. Identify which slot (PCIE1 or PCIE7) hosts the T710 set on RC `0000:40`. Cheapest method that works.
2. Reorganize storage so PCIE1 *or* PCIE7 (whichever is on RC `0000:40`) becomes free for a GPU. The displaced T710 set goes elsewhere — exact placement is a storage decision documented in this plan but executed within it.
3. Move one of the 4090s into that slot via riser cable so both GPUs land on RC `0000:40`.
4. Apply the BIOS pass (Above 4G + ReBAR + ACS off + IOMMU=pt + correct slot link widths).
5. Verify topology change: `nvidia-smi topo --matrix` shows `PHB` or `PXB` (not `NODE`).
6. Apply the patched NVIDIA driver (tinygrad `open-gpu-kernel-modules` fork). Stock GeForce driver disables P2P even when hardware allows it.
7. Verify P2P: `nvidia-smi topo -p2p rw` returns `OK` (not `N/A`); vLLM startup log no longer prints `Custom allreduce is disabled because your platform lacks GPU P2P capability`.
8. Re-run the original vLLM TP=2 measurement; record before/after delta.
9. **If P2P delivers ≥ 60 t/s decode at the medium workload**, open a follow-up PR amending the main vLLM plan to reinstate TP=2-with-P2P phases. Otherwise close out this sub-plan and stay single-GPU.

## Slot ↔ RC mapping (current evidence)

From `lspci -tv` plus the user-confirmed physical layout:

| Slot | Current occupant | Currently maps to bus | RC |
|---|---|---|---|
| PCIE7 | T710 set (4× NVMe) | one of `41–44` or `c1–c4` | one of `0000:40` or `0000:c0` |
| PCIE5 | GPU0 (RTX 4090) | `01` | `0000:00` |
| PCIE3 | GPU1 (RTX 4090) | `45` | `0000:40` |
| PCIE1 | T710 set (4× NVMe) | the other of `41–44` / `c1–c4` | the other of `0000:40` / `0000:c0` |

The PCIE1 ↔ PCIE7 disambiguation is the deciding question of the entire sub-plan. Everything downstream is mechanical once that's known.

Note that PCIE5 (RC `0000:00`) is a dead-end for this purpose — RC `0000:00` has no other x16 slot wired to it on this board; its non-GPU peers are X710 NIC, SDXI, FCH SATA, all internal/onboard. So GPU0 has to be the one that moves.

## Identification methods (cheapest first)

Run in order; stop at the first one that produces a clean answer.

1. **TrueNAS storage UI** — Storage → Disks lists each NVMe with its physical slot label (e.g., "Slot 1.1", "Slot 7.3"). Cross-reference with `lspci -nv | grep 5428` to map slot label → bus address.
2. **`sudo dmidecode --type 9`** via `ssh -t truenas_admin@10.42.2.10` (interactive sudo). SMBIOS Type 9 records typically include `Bus Address: 0000:XX:YY.Z` per slot.
3. **BMC web UI** (ASPEED iKVM at the IPMI IP) → System Inventory → PCIe Devices. Most ASPEED BMCs list slot-by-slot population.
4. **NVMe serial cross-reference** — `nvme list -o json` on hestia gives serials per `/dev/nvmeN`; physical inspection of the T710 cards gives serials per slot.
5. **Last resort: one-card riser experiment.** Power down. Pull the T710 set from PCIE1 (whichever you can get to easiest), riser it temporarily into a known-empty slot or leave it disconnected. Boot. The remaining T710 set's bus IDs will tell us which RC PCIE7 is on by elimination.

If methods 1–4 all fail, do method 5 in the same maintenance window as the actual GPU move so we only power-cycle once.

## Storage relocation plan

The displaced T710 set (4 NVMes) needs a home. Options, in preference order:

1. **Move both T710 sets to PCIE7 stacked** — only works if PCIE7 was the one on `0000:c0` and we're displacing the PCIE1 set. PCIE7 is already populated with one T710 set; you can't put two there. **Skip this option.**
2. **Move the displaced T710 set to MCIO1 + MCIO2 via MCIO-to-x4-NVMe breakout cables** — each MCIO is x8, can carry 2× x4 NVMe. Two MCIOs = 4 NVMes. Hardware exists; cables ~$30–60 each. This is the clean answer.
3. **Move the displaced T710 set to PCIE6 (x8) + PCIE3 (after GPU1 has moved)** — wait, PCIE3 is one of the two slots we want to leave free for the GPU. **Skip.**
4. **Temporarily disconnect the displaced T710 set entirely** — viable if it holds non-critical scratch data and the user can tolerate losing access to those 4 drives during the experiment window. This is the minimum-cost path if you just want to test whether P2P works before committing to MCIO breakouts.

**Recommended:** option 4 for the experiment (so we can test the hypothesis fast), option 2 if the experiment succeeds and the layout becomes permanent.

Storage layout decisions about which pool the displaced drives belonged to, what data they hold, and whether reslivering / restoring is needed are out of scope here — call them out before the maintenance window starts.

## BIOS pass (independent of slot decision)

To be done in the same boot as the GPU move:

| Setting | Path (typical ASRock Rack) | Value |
|---|---|---|
| Above 4G Decoding | Advanced → PCIe Configuration | **Enabled** |
| Re-Size BAR Support | Advanced → PCIe Configuration | **Enabled** |
| ACS (Access Control Services) | Advanced → AMD CBS → NBIO Common Options → ACS Enable | **Disabled** |
| IOMMU | Advanced → AMD CBS → NBIO Common Options → IOMMU | **Auto** (or "Passthrough" if explicit) |
| Target slot link width | Advanced → PCIe Configuration → PCIE{1,3,7} Link Width | **x16** |
| Target slot bifurcation | Advanced → PCIe Configuration → PCIE{1,3,7} Bifurcation | **Auto** (or `x16` non-bifurcated) |

Save, reboot. First post-reboot check is `nvidia-smi topo --matrix`. Expected: `NODE` → `PHB` (or `PXB` if there's a switch in between).

If still `NODE` after the BIOS pass, the most common silent culprit is ACS still on for the GPU's slot — CBS hides this in two layers ("ACS Enable" + per-slot ACS overrides). Walk the menus methodically.

Also verify kernel command line includes `iommu=pt amd_iommu=on` (TrueNAS default may or may not — check `cat /proc/cmdline`).

## Driver patch (only after BIOS+slot success)

Do not attempt before topology is verified `PHB`/`PXB`. The driver patch on its own can't fix `NODE` topology.

1. Stop inference apps: `midclt call app.stop vllm; midclt call app.stop llama-cpp`. Verify GPUs idle in `nvidia-smi`.
2. Note the running NVIDIA driver version: `nvidia-smi --query-gpu=driver_version --format=csv`. Currently `590.44.01`.
3. Build the tinygrad open-kernel-modules fork against TrueNAS' running kernel:
   - Reference: <https://github.com/tinygrad/open-gpu-kernel-modules>
   - Reference writeup: <https://smcleod.net/2026/02/patching-nvidias-driver-and-vllm-to-enable-p2p-on-consumer-gpus/>
   - TrueNAS-specific gotcha: kernel is locked-down; the patched modules must be signed with the same key chain as the running kernel or signature verification will reject them. May require disabling kernel module signature enforcement (security trade-off — record explicitly).
4. Replace the running driver modules with the patched build.
5. Reboot.
6. Verify:
   - `nvidia-smi topo -p2p r` → `OK` (was `N/A`)
   - `nvidia-smi topo -p2p w` → `OK`
   - `nvidia-smi topo --matrix` still `PHB`/`PXB`
   - vLLM startup log: the `Custom allreduce is disabled because your platform lacks GPU P2P capability` warning is gone, replaced by no warning or by an "enabled" line.

## Smoke test (after driver patch)

Run the on-host benchmark harness (`scripts/llama-cpp-bench.py` from the homelab repo, copied to `/tmp/llama-cpp-bench.py` per the parent plan's pattern), against vLLM with the same flags as the pre-plan ad-hoc TP=2 run:

```
Qwen/Qwen3.6-27B-FP8
  --tensor-parallel-size 2
  --max-model-len 65536
  --gpu-memory-utilization 0.90
  --enable-chunked-prefill
  --max-num-seqs 8
```

Comparison target: 33 t/s decode (medium workload, single-stream) — the pre-plan baseline.

| Result | Interpretation | Next step |
|---|---|---|
| ≤ 35 t/s | P2P enabled but no measurable benefit | Document; the plan failed to deliver. Close out, remain single-GPU. |
| 35–60 t/s | Marginal — P2P working but PCIe Gen5 P2P is not enough to make TP=2 competitive | Document; do not reinstate TP=2 phases. Single-GPU still wins. |
| 60–100 t/s | Real win. TP=2 is now viable for models that need it (e.g., Qwen3.6-27B at full 65K context where AWQ doesn't fit). | Open follow-up PR amending #558 to add TP=2-with-P2P phases. |
| > 100 t/s | Substantial win. TP=2 may approach or exceed single-GPU AWQ throughput on the medium workload. | Same as above; also revisit which model becomes the recommended hermes backend. |

## Decision tree

| Outcome | Next step |
|---|---|
| Slot↔RC ID inconclusive after methods 1–4 | Do method 5 (one-card riser) in the maintenance window. |
| BIOS pass done, topology stays `NODE` | Re-walk BIOS menus (ACS sub-options); if still no, file as findings, leave hardware as-is, close sub-plan. |
| Topology drops to PHB/PXB but driver patch fails on TrueNAS kernel | Document the kernel-module signing blocker as a separate troubleshooting item. Hold sub-plan. Do not roll back the BIOS/slot change — it costs nothing to leave in place. |
| Driver patch works; smoke test ≤ 35 t/s | Document the unexpected null result; close sub-plan; main plan unchanged. |
| Driver patch works; smoke test ≥ 60 t/s | Open the amending PR. Update `~/.claude/HOMELAB.md` to remove the "vLLM disabled — OOM" note. |

## Out of scope

- **NVLink hardware** — not happening on consumer 4090s.
- **NVIDIA-data-center-GPU upgrades** — different conversation.
- **Multi-RC P2P via the IOD fabric** — would require kernel work outside the homelab scope.
- **Replacing the T710 bifurcation cards with single x16 NVMes** — different storage layout decision; its own plan.
- **Any vLLM benchmarking beyond the single TP=2 smoke test** — that lives in the parent plan.

## Critical files

- `docs/plans/2026-05-07-vllm-frontier-model-experiments.md` — parent plan (in homelab repo via PR #558). **Referenced** but not modified by this sub-plan; if smoke test passes, a separate amending PR adds the TP=2-with-P2P phase.
- `docs/research/2026-05-07-vllm-frontier-experiments.md` — parent plan's results log (PR #559). Phase 0 already records the 33 t/s pre-plan number this sub-plan is trying to beat.
- `docs/plans/2026-05-07-hestia-p2p-enablement.md` — **new**, this plan's content, lands in the homelab repo via the delivery PR below.
- `~/Downloads/SIENAD8-2L2T.pdf` — board manual, slot ↔ port mapping reference.
- `~/.claude/HOMELAB.md` — operator notes; updated post-success.
- `scripts/llama-cpp-bench.py` — reused for smoke test, no modification.

## Verification (how to know the sub-plan worked)

End-state checklist:

- [ ] `nvidia-smi topo --matrix` between GPU0 and GPU1: `PHB` or `PXB` (not `NODE`).
- [ ] `nvidia-smi topo -p2p rw`: `OK` (not `N/A`).
- [ ] vLLM startup log: no `Custom allreduce is disabled because your platform lacks GPU P2P capability` warning.
- [ ] vLLM `Qwen/Qwen3.6-27B-FP8 + TP=2` decode TPS, medium workload, mean over 4 post-warmup runs: > 60 t/s (the threshold for "useful").
- [ ] All 9 NVMes still visible in TrueNAS storage view, no pool degraded.
- [ ] Research log appended with: BIOS settings used, driver version (stock vs patched), `topo --matrix` before/after, full benchmark numbers, before/after delta vs the 33 t/s baseline.

## Cross-references

- Parent plan: [`docs/plans/2026-05-07-vllm-frontier-model-experiments.md`](2026-05-07-vllm-frontier-model-experiments.md) (PR #558).
- Phase 0 results: [`docs/research/2026-05-07-vllm-frontier-experiments.md`](../research/2026-05-07-vllm-frontier-experiments.md) (PR #559).
- tinygrad patched driver: <https://github.com/tinygrad/open-gpu-kernel-modules>
- smcleod walkthrough: <https://smcleod.net/2026/02/patching-nvidias-driver-and-vllm-to-enable-p2p-on-consumer-gpus/>
- AMD EPYC Siena PPR / IOD reference: official AMD docs (look up the SP6 / 8004-series PPR for authoritative IOD topology — the Siena IOD is a distinct die from the Genoa-class IOD even though `lspci` reports both as "Genoa/Bergamo").
- Board manual: `~/Downloads/SIENAD8-2L2T.pdf` (block diagram pp. 12–13).

## Delivery

Same PR pattern as the parent plan:

1. Branch `feat/hestia-p2p-enablement-plan` off `origin/master`.
2. Add `docs/plans/2026-05-07-hestia-p2p-enablement.md` (this file's content, frontmatter `status: planned`).
3. Update `docs/plans/README.md` index.
4. PR title: `docs(plans): hestia P2P enablement (slot move + driver patch)`.
5. PR body summarizes the kernel-level evidence (3 RCs, current `NODE` is fixable on this board, smoke-test threshold for declaring success), and cross-links #558 + #559.

The plan PR contains no compose, script, or BIOS changes. The hardware/BIOS/driver work happens in a maintenance window after the plan PR merges, with results landing in PR-per-step:

- **Step PR 1**: slot identification (TrueNAS UI / dmidecode / BMC findings appended to a research log).
- **Step PR 2**: physical move + BIOS pass + new `nvidia-smi topo --matrix`.
- **Step PR 3**: driver patch + verification.
- **Step PR 4**: smoke test results + decision (open the amending PR for #558 if numbers warrant).
