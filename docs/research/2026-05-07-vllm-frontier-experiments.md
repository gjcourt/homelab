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

## Phase 6 — `cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit` direct A/B with llama.cpp prod

**Date:** 2026-05-09. SCALE app: `vllm`. Variant compose: same file, content swapped.

This is the headline experiment of the plan: same model family as production llama.cpp (`Qwen3.6-35B-A3B`, MoE with ~3 B active params per token), different engine, different quant. The question it answers is "should hermes move off llama.cpp?"

### Iteration log

| Attempt | Change | Outcome |
|---|---|---|
| v0 (deferred — GGUF) | Pointed vLLM at the production `/mnt/main/ai/models/gguf/Qwen3.6-35B-A3B-UD-IQ4_NL.gguf` directly | **fail** — `ValueError: GGUF model with architecture qwen35moe is not supported yet`. vLLM's GGUF path doesn't have a kernel for the qwen35moe MoE architecture. Skipped to AWQ. |
| v1 (TP=1, GPU0 only) | Same flag template as Phase 1; `cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit` | **fail** — model loaded ~80% of safetensors then `CUDA out of memory. Tried to allocate 144 MiB. Of 23.52 GiB total, 76 MiB free.` AWQ variant is bigger than the GGUF (~22 GiB on disk vs ~18 GiB IQ4_NL). MoE retains all expert weights in VRAM; doesn't fit single-GPU. |
| **v2 (TP=2)** | Switched to `--tensor-parallel-size 2`, kept `--kv-cache-dtype fp8`, `--max-model-len 32768`, `--gpu-memory-utilization 0.90`, `count: all` for Docker GPUs | **pass** — both GPUs come up at ~21 GiB each (model split + KV cache). All gauntlet steps pass. |

### Final config under test

```
cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit
  --tensor-parallel-size 2
  --dtype auto
  --gpu-memory-utilization 0.90
  --max-model-len 32768
  --kv-cache-dtype fp8
  --enable-chunked-prefill
  --max-num-seqs 8
  --enable-auto-tool-choice
  --tool-call-parser hermes
  --trust-remote-code
deploy.resources.reservations.devices: count=all (both GPUs)
```

### Stability gauntlet

| Step | Probe | Outcome |
|---|---|---|
| 0 | Cold load → `/health` 200 | **pass** (~3 min after redeploy; ~21 GiB each on both GPUs) |
| 1 | Single-shot smoke (implicit in benchmark short workload) | **pass** |
| 2 | Streaming (benchmark uses `stream=true`) | **pass** |
| 3 | Long-prompt prefill (~4 K tok prompt → 1 K tok output, benchmark long workload) | **pass** — 5.33 s total |
| 4 | Concurrent baseline (8 parallel × 100 tok) | **pass** — 8 reqs in 1.28 s, 800 tok total, **625.8 tok/s aggregate**, 0 errors |
| 5 | 30-min soak | not run this session |
| 6 | Long-context probe at ~75% of 32 K | not run this session |

Steps 0–4 pass on first attempt of v2.

### Benchmark — `scripts/llama-cpp-bench.py`, 5 runs/workload, first discarded as warmup

```
endpoint     http://127.0.0.1:8000/v1
model        qwen3.6-35b-a3b-awq

workload   TTFT (s)               decode TPS             total (s)
---------- ---------------------- ---------------------- ----------------------
short       0.086 ± 0.000          197.8 ± 0.1             0.34 ± 0.00
medium      0.088 ± 0.000          194.5 ± 0.1             2.66 ± 0.00
long        0.167 ± 0.000          193.6 ± 0.1             5.33 ± 0.00
```

### VRAM

GPU0: ~21 GiB. GPU1: ~21 GiB. Both pegged near `--gpu-memory-utilization 0.90` cap.

### The headline comparison

| Setup | TTFT (medium) | Decode TPS (medium) | 8-concurrent agg. tok/s |
|---|---|---|---|
| llama.cpp / Qwen3.6-35B-A3B-UD-IQ4_NL.gguf / both GPUs (production) | 0.071 s | **173.9 t/s** | n/a (single-stream config) |
| **vLLM / Qwen3.6-35B-A3B-AWQ-4bit / TP=2 / both GPUs (Phase 6 v2)** | **0.088 s** | **194.5 t/s** | **625.8 t/s** |
| vLLM / Qwen3.6-27B-AWQ-INT4 / TP=1 / GPU0 (Phase 1) | 0.100 s | 49.7 t/s | 165.8 t/s |

**vLLM TP=2 beats llama.cpp on the same MoE model by ~12 % on single-stream decode**, with TTFT essentially identical (0.088 vs 0.071 s). And under concurrent load vLLM's continuous batching produces 626 t/s aggregate vs llama.cpp's effectively-single-stream throughput.

### Why TP=2 worked here when Phase 1 / Phase 4 expectations said it wouldn't

The Phase 4 pre-plan TP=2 number (FP8, ~32 t/s) led the parent plan to mark TP=2 as expected-bad on this NODE-topology hardware. Phase 6 v2 says that conclusion was right *for dense models* and wrong *for low-active-param MoEs*. The reason:

- Tensor-parallel all-reduce volume per token in the FFN layers is proportional to **active** parameters, not total. Qwen3.6-35B-A3B activates ~3 B params per token (top-2 of N experts). The all-reduce traffic per token is ~3/35 × that of a 35 B dense model.
- AWQ-INT4 uses Marlin/CompressedTensorsWNA16 kernels on Ada — comparable peak compute to Q4_K_M GGUF, with cleaner memory access patterns.
- FP8 KV cache halves the per-token memory bandwidth pressure on KV ops.
- vLLM's V1 engine continuous-batches across the gauntlet's concurrent load, where llama.cpp's `--parallel 1` config serializes.

The result: NCCL all-reduce overhead exists, but it's small enough relative to per-token compute that the gain from continuous batching, FP8 KV, and Marlin-kernel decode outweighs it.

### Verdict

**Pass — by a wide margin. This is the candidate that justifies switching hermes to vLLM**, conditional on:

- 30-min soak still owed (step 5 of gauntlet).
- Real hermes traffic shape may differ from synthetic short/medium/long workloads — confirm with one or two real coding sessions before committing.
- Operator must accept vLLM-on-SCALE's redeploy quirks (pre-Phase-1 we hit several `app.update`/`app.start` race conditions; `app.redeploy` is the more reliable verb).

### Open follow-ups

- Soak + long-context probe before promoting to prod.
- Re-run the same harness against the running config in two days to spot any drift.
- A/B against the soon-to-be Gemma 4 NVFP4 phase (Phase 7 in the new ordering): same hardware, a comparable-scale dense model, different family.

## Side-finding (Option C / Phase 6 v0): vLLM's GGUF path doesn't support the Qwen 3.5/3.6 family

Two attempts to point vLLM at the existing GGUF files in `/mnt/main/ai/models/gguf/` both failed with the same error class, just different architecture name:

| File | Architecture in error | Outcome |
|---|---|---|
| `Qwen3.6-35B-A3B-UD-IQ4_NL.gguf` (production llama.cpp model) | `qwen35moe` | `ValueError: GGUF model with architecture qwen35moe is not supported yet.` |
| `Qwen3.6-27B-Q4_K_M.gguf` | `qwen35` | `ValueError: GGUF model with architecture qwen35 is not supported yet.` |

vLLM v0.20.0 has GGUF kernels for many architectures (Llama, Mistral, Phi, etc.) but **not** for the Qwen 3.5/3.6 dense or MoE variants. Both attempts crash-loop in the SCALE app within seconds.

**Implication:** the cleanest possible engine A/B (same exact GGUF, same exact quant, llama.cpp vs vLLM) is not available on this model family until vLLM lands the kernel. The next-best A/B is what Phase 6 v2 already does — same model family, same active-param profile, at AWQ-INT4 instead of GGUF IQ4_NL. That's still useful, just not strictly identical bytes-on-disk.

**Cost of the side-experiment:** zero — the SCALE app simply restart-loops until manually stopped, no resource leak.

## Phase 7 — Gemma 4 31B (NVFP4) on TP=2

**Date:** 2026-05-09. SCALE app: `vllm`. Variant compose: same file, content swapped.

Cross-family sanity check from the parent plan, replacing Llama-4-Scout per operator request. NVIDIA published an official NVFP4 quant of `Gemma-4-31B-IT` (`nvidia/Gemma-4-31B-IT-NVFP4`) — the Ada-optimized 4-bit format. Frontier-class dense ~31 B model.

### Iteration log

| Attempt | Change | Outcome |
|---|---|---|
| v1 (TP=1, GPU0) | Phase 1's flag template | **fail** — `ValueError: Chunked MM input disabled but max_tokens_per_mm_item (2496) is larger than max_num_batched_tokens (2048).` Gemma 4 is multimodal; default batch budget is too small for one image item. |
| v2 (TP=1, GPU0, `--max-num-batched-tokens 4096`) | added MM-friendly batch budget | **fail** — `CUDA out of memory` during `gemma4.py:408` QKV layer construction. 23.41 GiB allocated of 23.52 GiB capacity. NVFP4 support is marked "experimental" by vLLM and appears to over-allocate transient buffers during init. Dense 31B doesn't fit single-GPU under v0.20.0's NVFP4 path. |
| **v3 (TP=2, both GPUs, kept v2's flags)** | Split across both 4090s | **pass** — ~17.9 GiB on each GPU after CUDA graph capture; rises to ~21.9 GiB during the long workload. |

### Final config under test

```
nvidia/Gemma-4-31B-IT-NVFP4
  --tensor-parallel-size 2
  --dtype auto
  --gpu-memory-utilization 0.90
  --max-model-len 16384
  --max-num-batched-tokens 4096
  --kv-cache-dtype fp8
  --enable-chunked-prefill
  --max-num-seqs 8
  --enable-auto-tool-choice
  --tool-call-parser hermes
  --trust-remote-code
deploy.resources.reservations.devices: count=all
```

Cached at `/mnt/main/ai/models/hub/models--nvidia--Gemma-4-31B-IT-NVFP4` (~31 GB on disk; the safetensors include FP16 vision-tower weights and embeddings on top of NVFP4 transformer blocks).

### Stability gauntlet

| Step | Probe | Outcome |
|---|---|---|
| 0 | Cold load → `/health` 200 | **pass** (~5 min, ~3 min download + ~2 min load) |
| 1 | Single-shot smoke | **pass** — 0.20 s for short response |
| 2 | Streaming | **pass** (benchmark uses streaming) |
| 3 | Long-prompt prefill | **pass** — 21 s / 1000 tokens |
| 4 | Concurrent baseline (8 × 100) | **pass** — 8 reqs in 2.66 s, 418 tokens (Gemma stopped early on most), 157.3 tok/s aggregate, 0 errors |
| 5/6 | Soak, long-context probe | not run |

### Benchmark — 5 runs/workload, first discarded

```
endpoint     http://127.0.0.1:8000/v1
model        gemma-4-31b-nvfp4

workload   TTFT (s)               decode TPS             total (s)
---------- ---------------------- ---------------------- ----------------------
short       0.059 ± 0.004           56.6 ± 0.2             0.20 ± 0.00
medium      0.033 ± 0.000           49.6 ± 0.0            10.12 ± 0.00
long        0.050 ± 0.001           48.4 ± 0.0            20.71 ± 0.00
```

Note: Gemma 4 produced only 8 tokens for the short workload prompt (model decided the answer was complete at "Paris."). Decode TPS for short is computed over a tiny window and is noisier; medium/long are the trustworthy readings.

### VRAM

GPU0 + GPU1: each ~21 GiB peak under load.

### What this confirms (and what it doesn't)

The cross-family dimension was the goal: Gemma-class architecture, official NVIDIA NVFP4 quant. Took two corrections to get it cold-loaded but ran cleanly afterward.

**Decode rate (49.6 t/s medium) is essentially identical to Phase 1's dense Qwen3.6-27B AWQ on a single GPU** (49.7 t/s) — and **dramatically lower than Phase 6 v2's MoE on the same TP=2 hardware** (194.5 t/s). This is the cleanest evidence so far for the underlying claim from Phase 6 v2:

> Tensor-parallel all-reduce cost is proportional to **active** params, not total. Dense 31B at TP=2 pays the full all-reduce per token; the MoE's ~3 B active per token does not.

So:
- **Dense + TP=2 on NODE topology: slow** (Gemma 4: 49.6 t/s; pre-plan Qwen3.6-27B-FP8 dense TP=2: 32.4 t/s).
- **MoE + TP=2 on NODE topology: fast** (Phase 6 v2: 194.5 t/s).

Gemma 4 NVFP4 *works* on this hardware but doesn't compete with Phase 6 v2 for hermes use. Worth noting: **TTFT (0.033 s) is the lowest in the entire experiment matrix** — if a workload is TTFT-sensitive (cron-driven autonomous tool calls, latency-critical UX), Gemma 4 has a real edge over the MoE.

### Verdict

**Pass — but secondary to Phase 6 v2 for the hermes use case.** Recorded as the cross-family data point: vLLM's NVFP4 path works on Ada once you give it TP=2 + adequate `max-num-batched-tokens`. NVFP4-status flag in the log says "experimental and could change in future" — re-test after vLLM upgrades.

### Open follow-ups

- Re-test on vLLM v0.20.1+ (current is v0.20.0). Patch release may include NVFP4 fixes that allow single-GPU.
- Investigate why Gemma 4 stops early on terse prompts — may interact with hermes' tool-calling expectations.
- A vision-input test (image describe) since this is an MM model — out of scope for the current text-decoded-rate gauntlet.

## Cumulative scoreboard (post-Phase-7)

| Phase | Setup | TTFT (medium) | Decode (medium) | 8-concurrent agg. | VRAM | Verdict |
|---|---|---|---|---|---|---|
| (ref) | llama.cpp / Qwen3.6-35B-A3B-UD-IQ4_NL.gguf / both GPUs | 0.071 s | 173.9 t/s | n/a | 22.3 GiB total | production baseline |
| 1 | vLLM / Qwen3.6-27B-AWQ-INT4 / TP=1 GPU0 | 0.100 s | 49.7 t/s | 165.8 t/s | 21.5 GiB GPU0 | pass — dense single-GPU floor |
| 6 v2 | vLLM / Qwen3.6-35B-A3B-AWQ-4bit / TP=2 | **0.088 s** | **194.5 t/s** | **625.8 t/s** | ~21 GiB each | **pass — beats baseline** |
| 7 v3 | vLLM / Gemma-4-31B-IT-NVFP4 / TP=2 | **0.033 s** | 49.6 t/s | 157.3 t/s | ~21 GiB each | pass — best TTFT, dense-tax decoded |
| (failed) | vLLM GGUF / qwen35moe (Phase 6 v0) | — | — | — | — | architecture unsupported |
| (failed) | vLLM GGUF / qwen35 dense (Option C) | — | — | — | — | architecture unsupported |
| (failed) | vLLM AWQ-INT4 / 35B-A3B / TP=1 (Phase 6 v1) | — | — | — | — | OOM, doesn't fit single-GPU |
| (failed) | vLLM NVFP4 / Gemma 4 31B / TP=1 (Phase 7 v2) | — | — | — | — | OOM in QKV-init under v0.20.0 NVFP4 path |
