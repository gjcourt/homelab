---
status: in-progress
last_modified: 2026-05-07
---

# vLLM frontier-model experiments on hestia (2× 4090) — results log

Per-phase results for [`docs/plans/2026-05-07-vllm-frontier-model-experiments.md`](../plans/2026-05-07-vllm-frontier-model-experiments.md). Each phase appends a new section. Status flips to `complete` after the last must-do phase (phase 6 in the current plan).

## Phase 0 — Baseline & topology snapshot

**Date:** 2026-05-07.

### Host context (re-captured)

```
GPU0: NVIDIA GeForce RTX 4090, 24564 MiB, driver 590.44.01
GPU1: NVIDIA GeForce RTX 4090, 24564 MiB, driver 590.44.01
```

### `nvidia-smi topo --matrix`

```
        GPU0    GPU1    NIC0    NIC1    CPU Affinity    NUMA Affinity   GPU NUMA ID
GPU0     X      NODE    NODE    NODE    0-63            0               N/A
GPU1    NODE     X      NODE    NODE    0-63            0               N/A
NIC0    NODE    NODE     X      PIX
NIC1    NODE    NODE    PIX      X

Legend:
  NODE = Connection traversing PCIe as well as the interconnect between
         PCIe Host Bridges within a NUMA node
  PIX  = Connection traversing at most a single PCIe bridge
  NV#  = Connection traversing a bonded set of # NVLinks
```

**Interpretation:** The two 4090s are on separate PCIe host bridges within the same NUMA node, and there is no NVLink. This is the constraint that motivates the entire plan: vLLM's custom all-reduce is disabled by platform detection, and any TP=2 config pays a per-token all-reduce tax through host RAM.

### llama.cpp baseline (reference, not re-measured)

The current production llama.cpp config (Qwen3.6-35B-A3B-UD-IQ4_NL.gguf, see [`hosts/hestia/llms/docker-compose-llama-cpp.yml`](../../hosts/hestia/llms/docker-compose-llama-cpp.yml)) was characterized in [`docs/research/2026-05-04-llama-cpp-baseline.md`](2026-05-04-llama-cpp-baseline.md) (2026-05-04). Reference numbers used as the "must-beat" bar throughout this log:

| Workload | TTFT | Decode TPS | VRAM peak |
|---|---|---|---|
| short  | 0.081 ± 0.007 s | 176.6 ± 0.9 t/s | (single-stream) |
| medium | 0.071 ± 0.012 s | 173.9 ± 0.3 t/s | 22,845 MiB |
| long   | 0.078 ± 0.003 s | 172.9 ± 0.1 t/s | (steady) |

No re-run was performed at this time — the baseline doc is recent and the production config is unchanged. If a vLLM phase produces numbers that beg an apples-to-apples re-test on the same day, that re-run lives under that phase's section.

### Pre-Phase-0 vLLM measurement (informational, not gauntlet-validated)

Before this plan was written, an ad-hoc run of vLLM with `Qwen3.6-27B-FP8 + --tensor-parallel-size 2 --max-model-len 65536 --gpu-memory-utilization 0.90` produced (single-stream, no thinking, on-host benchmark):

| Workload | TTFT | Decode TPS |
|---|---|---|
| short  | 0.103 ± 0.003 s | 33.0 ± 0.0 t/s |
| medium | 0.105 ± 0.002 s | 32.4 ± 0.0 t/s |
| long   | 0.517 ± 0.002 s | 32.1 ± 0.1 t/s |

These numbers are **not** counted as a gauntlet pass — the soak and long-context-probe steps were never run. They are recorded here as the motivating data point for the rest of the plan. Phase 4 will re-run this configuration through the proper gauntlet.

### Open questions resolved

- **Will both GPUs be free for the rest of the plan?** Yes — at the end of Phase 0 the `vllm` SCALE app and the `llama-cpp` SCALE app are both `STOPPED`. Phases that need llama.cpp running for comparison will start it explicitly.
- **Does the existing benchmark harness (`scripts/llama-cpp-bench.py`) work against vLLM?** Yes, when run on-host (cross-LAN runs from macOS hit Python 3.14 chunked-encoding resets — known issue, reproducible). All phase benchmarks will be run from on-host via SSH.

### Verdict

Baseline established. Topology and constraints frozen. Move to Phase 1.
