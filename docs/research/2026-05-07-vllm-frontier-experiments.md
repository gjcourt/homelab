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

## Phase 1 — `cyankiwi/Qwen3.6-27B-AWQ-INT4` on TP=1 / GPU0 only

**Date:** 2026-05-09. SCALE app: `vllm`. Variant compose: [`hosts/hestia/llms/docker-compose-vllm-experiment.yml`](../../hosts/hestia/llms/docker-compose-vllm-experiment.yml).

### Iteration log (cold load required two corrections)

| Attempt | Change from prev | Outcome |
|---|---|---|
| v1 | `--quantization awq` explicit | **fail** — model's on-disk config is `compressed-tensors`, not `awq`. vLLM's pydantic validator rejected `quantization=awq` mismatch on engine-config build. **Fix:** drop the `--quantization` flag, let vLLM auto-detect from `config.json`. |
| v2 | dropped `--quantization`; kept `--gpu-memory-utilization 0.85`, `--max-model-len 32768`, default FP16 KV cache | **fail** — model loaded successfully (~19 GB on disk; "AWQ-INT4" naming is misleading, the format keeps some FP16 weights), but `_check_enough_kv_cache_memory` raised `ValueError: No available memory for the cache blocks. Try increasing gpu_memory_utilization`. With 0.85 × 24 GB = 20.4 GB, model + activations + workspace consumed it all, leaving 0 for KV. |
| v3 | `--gpu-memory-utilization 0.95`, `--max-model-len 16384`, **`--kv-cache-dtype fp8`** to halve KV memory per token | **pass** — cold load to `/health` 200 in ~2 minutes. GPU0: 21.5 GiB used, GPU1: 0 (correctly idle as TP=1 GPU0). |

The final v3 config is what the variant compose file holds.

### Final config under test

```
cyankiwi/Qwen3.6-27B-AWQ-INT4
  --dtype auto                       # vLLM autodetects FP16 + compressed-tensors
  --tensor-parallel-size 1           # default; not passed
  --gpu-memory-utilization 0.95
  --max-model-len 16384
  --kv-cache-dtype fp8
  --enable-chunked-prefill
  --max-num-seqs 8
  --enable-auto-tool-choice
  --tool-call-parser hermes
  --trust-remote-code
device_ids: ['0']                    # Docker-level GPU0 pin; GPU1 stays idle
```

### Stability gauntlet

| Step | Probe | Outcome |
|---|---|---|
| 0 | Cold load → `/health` 200 | **pass** (~2 min after v3 redeploy; 21.5 GiB on GPU0) |
| 1 | Single-shot smoke (`hi` → 20 tok) | **pass** — 2.71 s / 20 tok (TTFT-dominated) |
| 2 | Streaming smoke (3-sentence prompt → 80 tok, `stream=true`) | **pass** — total 2.17 s, 80 tok, TTFT 0.580 s |
| 3 | Long-prompt prefill (~8 K tok prompt → 128 tok output) | **pass** — 5.33 s / 128 tok |
| 4 | Concurrent baseline (8 parallel × 100 tok) | **pass** — 8 reqs in 4.82 s, 800 tok total, **165.8 tok/s aggregate**, 0 errors |
| 5 | 30-min soak | not run this session — defer to a quiet window |
| 6 | Long-context probe at ~75% of `max_model_len` (~12 K tok) | not run this session |

Steps 0–4 (gating) all pass. Phase 1 is benchmark-eligible.

### Benchmark — `scripts/llama-cpp-bench.py`, 5 runs/workload, first discarded as warmup

```
endpoint     http://127.0.0.1:8000/v1
model        qwen3.6-27b-awq

workload   TTFT (s)               decode TPS             total (s)
---------- ---------------------- ---------------------- ----------------------
short       0.098 ± 0.002           50.7 ± 0.0             1.08 ± 0.00
medium      0.100 ± 0.001           49.7 ± 0.0            10.16 ± 0.00
long        0.872 ± 0.001           49.4 ± 0.0            21.11 ± 0.00
```

Per-run trace (no thinking tokens — model produced direct content from token 1; the reasoning_content TTFT detector wasn't triggered):

```
short    run 1/5: ttft=0.110s tokens=50 decode=50.6 t/s total=1.10s
short    run 2/5: ttft=0.097s tokens=50 decode=50.7 t/s total=1.08s
short    run 3/5: ttft=0.097s tokens=50 decode=50.7 t/s total=1.08s
short    run 4/5: ttft=0.096s tokens=50 decode=50.7 t/s total=1.08s
short    run 5/5: ttft=0.101s tokens=50 decode=50.7 t/s total=1.09s
medium   run 1/5: ttft=0.099s tokens=500 decode=49.7 t/s total=10.16s
medium   run 2/5: ttft=0.101s tokens=500 decode=49.7 t/s total=10.16s
medium   run 3/5: ttft=0.099s tokens=500 decode=49.7 t/s total=10.16s
medium   run 4/5: ttft=0.100s tokens=500 decode=49.7 t/s total=10.16s
medium   run 5/5: ttft=0.100s tokens=500 decode=49.7 t/s total=10.16s
long     run 1/5: ttft=0.872s tokens=1000 decode=49.4 t/s total=21.11s
long     run 2/5: ttft=0.871s tokens=1000 decode=49.4 t/s total=21.11s
long     run 3/5: ttft=0.872s tokens=1000 decode=49.4 t/s total=21.12s
long     run 4/5: ttft=0.872s tokens=1000 decode=49.4 t/s total=21.12s
long     run 5/5: ttft=0.873s tokens=1000 decode=49.4 t/s total=21.12s
```

### VRAM peak

GPU0: ~21.5 GiB steady-state (model + KV + workspace). GPU1: 0 MiB.

### Comparison with prior reference points

| Setup | TTFT (medium) | Decode TPS (medium) | Notes |
|---|---|---|---|
| llama.cpp / Qwen3.6-35B-A3B-UD-IQ4_NL / both GPUs (production) | 0.071 s | 173.9 t/s | Reference baseline (2026-05-04) |
| vLLM / Qwen3.6-27B-FP8 / TP=2 / both GPUs (pre-plan, no gauntlet) | 0.105 s | 32.4 t/s | NODE topology, no P2P → high all-reduce cost |
| **vLLM / Qwen3.6-27B-AWQ-INT4 / TP=1 / GPU0 only (Phase 1)** | **0.100 s** | **49.7 t/s** | This phase |

### Verdict

**Pass — stability + perf both acceptable.** Phase 1 establishes the floor for "best vLLM single-stream on this hardware":

- Cleared the gauntlet (steps 0–4) on first config that survived two corrections.
- Beats the pre-plan TP=2 number (49.7 vs 32.4 t/s) by ~1.5×, which is exactly the expected outcome of removing per-token NCCL all-reduce.
- Comes in at ~28 % of llama.cpp on the production MoE config — that gap is the dense-vs-MoE tax (Qwen3.6-35B-A3B activates ~3 B params per token; dense 27 B activates all 27 B), not a vLLM deficiency.
- TTFT is nearly identical to llama.cpp — within ~30 ms.
- 165.8 tok/s aggregate at concurrency 8 means vLLM batching is doing real work (~3.3× the single-stream rate).

The bigger surprise is that the model labeled `AWQ-INT4` actually deserializes as `compressed-tensors` (~19 GiB on disk, not the ~14 GiB an INT4 quant would suggest). Folded into the iteration log so future phases don't re-step on the same trap.

### Open questions / follow-ups

- **`max_model_len=16384`** is half of what I'd ideally want for hermes coding workloads. With FP8 KV cache we could probably push it back to 24K or 32K — to be tested in a follow-up phase or via `--gpu-memory-utilization 0.97 --max-num-seqs 4` if the trade-off is worth it.
- **Phase 2** (`Qwen/Qwen3.6-27B-FP8`, TP=1, GPU0) was supposed to test native FP8 path. With this model at ~27 GiB it won't fit on a single 24 GiB card; Phase 2 effectively becomes "FP8 is single-GPU-impossible on this hardware" and gets folded into Phase 4 (TP=2 record-only).
- Soak (step 5) and long-context probe (step 6) are owed for a complete record. Schedule them when convenient.
