---
status: in-progress
last_modified: 2026-05-15
summary: "STREAM + Intel MLC bandwidth benchmark: 6-DIMM baseline vs 8-DIMM comparison"
---

# Hestia memory bandwidth benchmark — 6 DIMM vs 8 DIMM

## Context

Hestia is the TrueNAS GPU server (ASRock Rack **SIENAD8-2L2T**, **EPYC 8004 / Siena**, single socket, **6-channel DDR5**, board exposes **8 DIMM slots**). The "8 slots over 6 channels" layout means populating 8 DIMMs forces at least two channels into **2DPC**, and the platform's JEDEC table derates 2DPC channels to a lower speed grade (typically DDR5-3600 or DDR5-4000 vs DDR5-4800 at 1DPC). The whole bus usually clocks to the slowest channel.

Goal: an **objective bandwidth + latency measurement** to quantify the tradeoff.

- **Hypothesis A**: 8 DIMM ≈ 17–25% bandwidth loss vs 6 DIMM (the speed-grade ratio), plus a measurable loaded-latency penalty.
- **Hypothesis B**: 8 DIMM may also raise idle latency from the extra rank pressure (longer tRC).
- **Decision this enables**: keep the 33% extra capacity (8 DIMM) or take the bandwidth back (6 DIMM)?

Current state: hestia is populated with **6 DIMMs (1DPC × 6, full speed)**. First benchmark run is the baseline; operator physically adds 2 DIMMs for the second run.

No existing memory benchmark on hestia — clean slate. Reusing methodology patterns from [`scripts/llama-cpp-bench.py`](../../scripts/llama-cpp-bench.py) (stdlib-only, N runs/workload, mean±stddev, JSONL output) and the containerization pattern from [`2026-05-04-llama-cpp-benchmarking.md`](2026-05-04-llama-cpp-benchmarking.md).

## Approach

**Two benchmarks, containerized, run via Docker on hestia's TrueNAS host:**

| Tool | Why | Output |
|---|---|---|
| **STREAM** (McCalpin) | Canonical sustained-bandwidth benchmark. Copy / Scale / Add / Triad kernels. ~500 LOC of C, compiled from source in the Dockerfile. | Per-kernel MB/s, best run of N |
| **Intel MLC** | Loaded-latency curve, idle latency, peak BW with different access patterns. Binary-only (Intel license); operator pre-downloads the tarball into the build context. | Latency-vs-bandwidth curves, idle latency, NUMA-aware reads |

### Methodology controls

Applied identically to both runs:

- CPU governor → `performance` (kill scaling mid-run)
- C-states locked (`processor.max_cstate=1` via kernel cmdline; alternatively `idle=poll` for the benchmark window)
- Transparent hugepages → `always`
- **NPS mode → NPS1** for both runs (only mode guaranteed to enumerate cleanly with both 6 and 8 DIMM)
- Thread pinning to physical cores only, no SMT siblings: `OMP_PROC_BIND=close OMP_PLACES=cores`
- `drop_caches=3` before each run
- STREAM array size ≥4× LLC (200M doubles = 1.6 GB/array, ~5 GB for Triad — safely past Siena's max LLC of 128 MB)
- 5 runs per kernel per config, report **median + stddev** (matches the llama-cpp-bench pattern)
- vLLM **stopped** for the entire benchmark window: `midclt call app.stop vllm`. `nvtop` and the GHA runner can stay (read-only, negligible RAM traffic).

### Out of scope

- Multi-NPS comparison (NPS2 / NPS4 won't enumerate symmetrically with 8 DIMM asymmetric — apples-to-oranges)
- AVX-512 vectorized kernels via likwid-bench
- GPU memory benchmarks (orthogonal — RTX 4090 GDDR6X is unaffected by host DIMM config)
- BIOS-tunable knobs beyond what's already set (Above 4G, ReBAR, IOMMU=on — see [`2026-05-07-hestia-p2p-enablement.md`](2026-05-07-hestia-p2p-enablement.md))

## Artifacts

All paths relative to repo root.

| Path | Purpose |
|---|---|
| [`hosts/hestia/bench/memory/Dockerfile`](../../hosts/hestia/bench/memory/Dockerfile) | Debian base + build-essential + numactl. Compiles STREAM from McCalpin source in-image. Expects `mlc_v3.12.tgz` in build context. |
| [`hosts/hestia/bench/memory/entrypoint.sh`](../../hosts/hestia/bench/memory/entrypoint.sh) | In-container runner. Executes STREAM N=5, then MLC's sub-benchmarks. Emits JSONL. |
| [`hosts/hestia/bench/memory/run.sh`](../../hosts/hestia/bench/memory/run.sh) | Host wrapper. Pre-flight (governor, hugepages, drop_caches, vLLM-stop check, dmidecode snapshot), then `docker run`. |
| [`hosts/hestia/bench/memory/aggregate.py`](../../hosts/hestia/bench/memory/aggregate.py) | Compares two JSONL files, emits markdown table (BW, latency, deltas, % change). Adapted from `scripts/llama-cpp-bench.py`. |
| [`hosts/hestia/bench/memory/README.md`](../../hosts/hestia/bench/memory/README.md) | Operator-facing runbook: prerequisites, how to invoke, how to swap DIMMs, how to read results. |
| `docs/research/2026-05-15-hestia-memory-bandwidth.md` | Results writeup. **Written after both runs** by piping `aggregate.py` to file. Includes raw JSONL paths, comparison table, MLC loaded-latency curves, verdict. |

## Execution procedure

1. **Pre-flight** (one-time): operator downloads Intel MLC `mlc_v3.12.tgz` from [Intel's MLC page](https://www.intel.com/content/www/us/en/developer/articles/tool/intelr-memory-latency-checker.html) (license acceptance required) and places it in `hosts/hestia/bench/memory/`. Verify SHA matches the download.
2. **Build the image** on hestia: `docker build -t hestia-memory-bench:1 hosts/hestia/bench/memory/`.
3. **Capture pre-state**: `dmidecode -t memory`, `lscpu`, BIOS-reported memory speed. `run.sh` writes this to `<RESULTS_DIR>/<label>-<ts>.preflight.json`.
4. **Stop vLLM**: `midclt call app.stop vllm`. Wait for graceful shutdown, verify GPUs idle via `nvidia-smi`.
5. **Run 6-DIMM benchmark**: `sudo ./run.sh 6dimm`. Wall time ~45 min (STREAM 5 runs + MLC full curve).
6. **Power down**. Operator physically adds 2 DIMMs to the 2DPC-eligible slots per the SIENAD8-2L2T manual (page 12–13).
7. **Boot to BIOS**, verify memory enumerated correctly, capture the new memory speed grade. If it didn't derate as expected (rare but possible), note in the writeup.
8. **Re-verify methodology controls** persist across reboot: governor, C-state, NPS mode, hugepages — all settings should persist via kernel cmdline / Tunables.
9. **Run 8-DIMM benchmark**: `sudo ./run.sh 8dimm`.
10. **Aggregate**: `python3 hosts/hestia/bench/memory/aggregate.py <results-6dimm>.jsonl <results-8dimm>.jsonl --out docs/research/2026-05-15-hestia-memory-bandwidth.md`.
11. **Restart vLLM**: `midclt call app.start vllm`. Verify model loads, smoke-test `curl http://10.42.2.10:8000/v1/models`.
12. **Commit** scripts + plan + results writeup as a PR against `gjcourt/homelab` master.

## Verification

Five concrete checks after both runs:

1. **STREAM Triad — 6 DIMM ≥ 8 DIMM by ≥10%**. Anything less than 10% means either the platform isn't derating as documented, or the methodology is leaking noise (governor, thermals).
2. **MLC idle latency — 8 DIMM marginally higher** (extra ranks → slightly longer tRC). Expect 2–5 ns delta.
3. **MLC loaded-latency curve — 8 DIMM saturates earlier and at lower bandwidth**. The knee of the curve moves left.
4. **Run-to-run stddev < 2% of mean** for both configs. If higher, a control is leaking (most likely C-state or governor).
5. **BIOS-reported speed grade matches the JEDEC table** for the platform. If 8 DIMM stays at 4800 MT/s, something interesting is happening — write it up.

The aggregator output leads with the headline number: `% bandwidth delta on STREAM Triad`. Everything else is supporting evidence.
