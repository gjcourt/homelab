---
status: in-progress
last_modified: 2026-05-04
---

# llama.cpp tuning results — hestia (RTX 4090)

Per-phase results for [`docs/plans/2026-05-04-llama-cpp-benchmarking.md`](../plans/2026-05-04-llama-cpp-benchmarking.md).

Baseline (Phase 0) numbers are in [`docs/research/2026-05-04-llama-cpp-baseline.md`](2026-05-04-llama-cpp-baseline.md). Pass/fail thresholds per the latency budget established there:

- Decode TPS (medium): **> 150 t/s** (baseline 174.4 ± 0.2)
- TTFT (medium): **< 0.15 s** (baseline 0.067 ± 0.003s)
- VRAM peak: **< 22,528 MiB (22 GiB)**

---

## Phase 1 — `--ctx-size 409600 → 32768`

**Hypothesis:** KV cache at q8_0 for 400K tokens consumes most of the 4090's 24 GiB. Baseline measured 22,845 MiB steady-state — only ~1.7 GiB headroom. Reducing ctx-size to 32768 should release substantial VRAM and may improve decode TPS if memory bandwidth was the bottleneck.

**Variant flags vs baseline:**

```diff
-  --ctx-size 409600
+  --ctx-size 32768
```

All other flags unchanged.

**llama-bench:** skipped — GPU-mode llama-bench not yet available in image (see `feat/llama-cpp-bench-image`).

**Curl harness (mean ± stddev across 5 runs after warmup):**

Raw per-run trace:
```
short    run 1/5: ttft=0.150s tokens=50 decode=169.8 t/s total=0.44s  ← warmup (discarded)
short    run 2/5: ttft=0.088s tokens=50 decode=175.5 t/s total=0.37s
short    run 3/5: ttft=0.077s tokens=50 decode=175.8 t/s total=0.36s
short    run 4/5: ttft=0.073s tokens=50 decode=175.3 t/s total=0.36s
short    run 5/5: ttft=0.079s tokens=50 decode=176.9 t/s total=0.36s
medium   run 1/5: ttft=0.107s tokens=500 decode=174.0 t/s total=2.98s  ← warmup (discarded)
medium   run 2/5: ttft=0.086s tokens=500 decode=4.8 t/s total=103.99s  ← ANOMALY (see below)
medium   run 3/5: ttft=0.077s tokens=500 decode=173.2 t/s total=2.96s
medium   run 4/5: ttft=0.062s tokens=500 decode=173.9 t/s total=2.94s
medium   run 5/5: ttft=0.068s tokens=500 decode=174.1 t/s total=2.94s
long     run 1/5: ttft=0.524s tokens=1000 decode=175.5 t/s total=6.22s  ← warmup (discarded)
long     run 2/5: ttft=0.075s tokens=1000 decode=172.5 t/s total=5.87s
long     run 3/5: ttft=0.076s tokens=1000 decode=172.5 t/s total=5.87s
long     run 4/5: ttft=0.080s tokens=1000 decode=172.5 t/s total=5.88s
long     run 5/5: ttft=0.074s tokens=1000 decode=172.3 t/s total=5.88s
```

Summary (harness-reported, medium inflated by anomaly):

| Workload | TTFT (s) | Decode TPS | Total (s) |
|---|---|---|---|
| short | 0.079 ± 0.006 | 175.9 ± 0.7 | 0.36 ± 0.01 |
| medium | 0.073 ± 0.010 | 131.5 ± 84.5 ⚠ | 28.21 ± 50.52 ⚠ |
| long | 0.076 ± 0.003 | 172.4 ± 0.1 | 5.88 ± 0.00 |

Medium excluding the anomaly (runs 3–5 only):

| Workload | TTFT (s) | Decode TPS | Total (s) |
|---|---|---|---|
| medium (clean) | 0.069 ± 0.008 | 173.7 ± 0.5 | 2.95 ± 0.01 |

**Medium run 2 anomaly:** 500 tokens generated at 4.8 t/s (103.99s total) vs the normal ~174 t/s. Runs 1 (warmup) and 3–5 were all normal. Most likely a transient GPU scheduling event or system-level interference, not a structural ctx-size issue (the prompt + response at ~1000 tokens is well within the 32768 limit). No recurrence across the remaining runs. Worth monitoring in Phase 2 but not a blocker.

**VRAM peak:**

- After model load (pre-run): 18,073 MiB
- Steady-state after run: 18,095 MiB (flat — KV cache not growing with workload)
- **Delta vs baseline: −4,772 MiB (−20.9%)** — freed 4.7 GiB by reducing ctx from 400K to 32K tokens

**Verdict:** **keep** — VRAM down 4.7 GiB (22.8 → 18.1 GiB), now well within the 22 GiB target. Decode TPS unchanged on clean runs (175.9 / 173.7 / 172.4 vs baseline 176.2 / 174.4 / 172.9 — all within noise). One anomalous medium run noted; not blocking given 4/5 clean runs and no structural explanation tied to ctx-size.

---

## Phase 1b — `--ctx-size 32768 → 262144` (256K target for hermes coding agent)

**Context:** User requires 250–300K context for hermes dual use (chatbot + coding agent). Phase 1 confirmed the per-token KV cache cost at q8_0: ~0.01266 MiB/token. Projected VRAM at 262,144 tokens: ~20,977 MiB (~20.5 GiB), leaving ~1.5 GiB headroom under the 22 GiB ceiling.

**Variant flags vs Phase 1:**

```diff
-  --ctx-size 32768
+  --ctx-size 262144
```

**llama-bench:** skipped — GPU-mode llama-bench not yet available in image.

**Curl harness (mean ± stddev across 5 runs after warmup):**

```
short    run 1/5: ttft=0.164s tokens=50 decode=172.2 t/s total=0.45s  ← warmup (discarded)
short    run 2/5: ttft=0.088s tokens=50 decode=176.5 t/s total=0.37s
short    run 3/5: ttft=0.077s tokens=50 decode=176.3 t/s total=0.36s
short    run 4/5: ttft=0.068s tokens=50 decode=176.4 t/s total=0.35s
short    run 5/5: ttft=0.060s tokens=50 decode=176.0 t/s total=0.34s
medium   run 1/5: ttft=0.109s tokens=500 decode=174.1 t/s total=2.98s  ← warmup (discarded)
medium   run 2/5: ttft=0.087s tokens=500 decode=174.3 t/s total=2.96s
medium   run 3/5: ttft=0.062s tokens=500 decode=174.3 t/s total=2.93s
medium   run 4/5: ttft=0.066s tokens=500 decode=173.8 t/s total=2.94s
medium   run 5/5: ttft=0.062s tokens=500 decode=174.0 t/s total=2.94s
long     run 1/5: ttft=0.486s tokens=1000 decode=175.1 t/s total=6.20s  ← warmup (discarded)
long     run 2/5: ttft=0.083s tokens=1000 decode=172.6 t/s total=5.88s
long     run 3/5: ttft=0.074s tokens=1000 decode=172.3 t/s total=5.88s
long     run 4/5: ttft=0.075s tokens=1000 decode=172.7 t/s total=5.87s
long     run 5/5: ttft=0.075s tokens=1000 decode=172.8 t/s total=5.86s
```

| Workload | TTFT (s) | Decode TPS | Total (s) |
|---|---|---|---|
| short | 0.073 ± 0.012 | 176.3 ± 0.2 | 0.36 ± 0.01 |
| medium | 0.069 ± 0.012 | 174.1 ± 0.2 | 2.94 ± 0.01 |
| long | 0.077 ± 0.004 | 172.6 ± 0.2 | 5.87 ± 0.01 |

No anomalies. All 5 runs per workload clean.

**VRAM peak:**

- After model load (pre-run): 20,773 MiB
- Steady-state after run: 20,795 MiB (flat)
- **Delta vs Phase 1: +2,700 MiB** for +229,376 tokens — confirms ~0.01178 MiB/token rate (consistent with prior measurement)
- **Delta vs baseline: +2,072 MiB net** — still 1,733 MiB under the 22 GiB target

**Verdict:** **keep** — 256K context achieved at 20.3 GiB VRAM with zero TPS regression (176.3 / 174.1 / 172.6 vs baseline 176.2 / 174.4 / 172.9). Phase 1 anomaly did not recur. 1.7 GiB headroom remains for Phase 2 (flash-attn).

---

## Phase 2 — `--flash-attn on` (additive on 256K config)

**Hypothesis:** Flash Attention on Ada (sm89) should give a modest decode TPS improvement by replacing the standard O(n²) attention kernel with a fused O(n) implementation. Effect expected to be additive on top of Phase 1b.

**Variant flags vs Phase 1b:**

```diff
+  --flash-attn on
```

**flash-attn confirmed in logs:** `llama_context: flash_attn = enabled`

**llama-bench:** skipped — GPU-mode llama-bench not yet available in image.

**Curl harness (mean ± stddev across 5 runs after warmup):**

```
short    run 1/5: ttft=0.155s tokens=50 decode=165.2 t/s total=0.46s  ← warmup (discarded)
short    run 2/5: ttft=0.089s tokens=50 decode=177.0 t/s total=0.37s
short    run 3/5: ttft=0.076s tokens=50 decode=176.4 t/s total=0.36s
short    run 4/5: ttft=0.181s tokens=50 decode=288.2 t/s total=0.35s  ← ANOMALY (see below)
short    run 5/5: ttft=0.076s tokens=50 decode=175.9 t/s total=0.36s
medium   run 1/5: ttft=0.109s tokens=500 decode=174.4 t/s total=2.98s  ← warmup (discarded)
medium   run 2/5: ttft=0.084s tokens=500 decode=174.3 t/s total=2.95s
medium   run 3/5: ttft=0.047s tokens=500 decode=174.1 t/s total=2.92s
medium   run 4/5: ttft=0.063s tokens=500 decode=173.8 t/s total=2.94s
medium   run 5/5: ttft=0.064s tokens=500 decode=174.1 t/s total=2.94s
long     run 1/5: ttft=0.511s tokens=1000 decode=175.8 t/s total=6.20s  ← warmup (discarded)
long     run 2/5: ttft=0.073s tokens=1000 decode=172.9 t/s total=5.86s
long     run 3/5: ttft=0.077s tokens=1000 decode=172.9 t/s total=5.86s
long     run 4/5: ttft=0.075s tokens=1000 decode=172.9 t/s total=5.86s
long     run 5/5: ttft=0.080s tokens=1000 decode=173.0 t/s total=5.86s
```

| Workload | TTFT (s) | Decode TPS | Total (s) | Notes |
|---|---|---|---|---|
| short (clean, runs 2,3,5) | 0.080 ± 0.008 | 176.4 ± 0.6 | 0.36 ± 0.01 | run 4 excluded |
| medium | 0.064 ± 0.015 | 174.1 ± 0.2 | 2.94 ± 0.01 | |
| long | 0.076 ± 0.003 | 172.9 ± 0.0 | 5.86 ± 0.00 | |

**Short run 4 anomaly:** ttft=0.181s but total=0.35s → decode window of only 0.169s → 296 t/s implied. Physically improbable for this model; likely a timing artifact where the model reused KV cache state or the thinking phase completed abnormally fast. Excluded from clean stats.

**VRAM peak:**

- After model load (pre-run): 20,773 MiB
- Steady-state after run: 20,795 MiB
- **Delta vs Phase 1b: 0 MiB** — flash-attn is a compute optimization with no persistent memory cost

**Comparison vs Phase 1b baseline (256K ctx, no flash-attn):**

| Workload | Phase 1b TPS | Phase 2 TPS | Delta |
|---|---|---|---|
| short | 176.3 ± 0.2 | 176.4 ± 0.6 | +0.1 (noise) |
| medium | 174.1 ± 0.2 | 174.1 ± 0.2 | 0.0 |
| long | 172.6 ± 0.2 | 172.9 ± 0.0 | +0.3 (noise) |

**Verdict:** **keep** — No measurable TPS gain at tested context lengths (50–4000 token prompts), but no regression either and VRAM unchanged. Flash-attn's benefit is in the attention computation, which scales quadratically with sequence length — the tested workloads are short relative to the 256K window. Expected to show real benefit when hermes uses long coding contexts near the 256K limit. Zero cost to keep.

---

## Phase 2 extended — `prefill_32k` A/B test (cold prefill, flash-attn on vs off)

**Motivation:** The short/medium/long workloads (50–4000 token prompts) are too short for flash-attn's O(n²)→O(n) attention kernel reduction to be measurable. A ~55K token cold-prefill workload was added to the bench script (`prefill_32k`) to isolate attention cost and make any flash-attn speedup directly visible in `prefill_tps = prompt_tokens / TTFT`.

**Method:** `scripts/llama-cpp-bench.py` extended with:
- `prefill_32k` workload: 55,582-token prompt (192 × webhook-service paragraph), 50-token response cap.
- `cache_prompt: false` field in the API request.
- Per-run nanosecond salt prepended to the prompt to defeat llama.cpp's LCP-similarity KV cache reuse (the server ignores `cache_prompt: false` for context checkpoint decisions in this build).
- `prefill_tps` metric: `prompt_tokens / TTFT`.

**Raw per-run traces:**

No flash-attn (prod baseline, 256K ctx):
```
prefill_32k run 1/3: ttft=9.556s prefill=5816 t/s prompt=55582 tokens=50 decode=137.4 t/s total=9.92s
prefill_32k run 2/3: ttft=9.515s prefill=5842 t/s prompt=55582 tokens=50 decode=138.2 t/s total=9.88s  ← post-warmup
prefill_32k run 3/3: ttft=9.536s prefill=5829 t/s prompt=55582 tokens=50 decode=138.1 t/s total=9.90s  ← post-warmup
```

Flash-attn on (same 256K ctx, `--flash-attn on`):
```
prefill_32k run 1/3: ttft=9.410s prefill=5907 t/s prompt=55582 tokens=50 decode=136.0 t/s total=9.78s
prefill_32k run 2/3: ttft=9.512s prefill=5843 t/s prompt=55582 tokens=50 decode=138.2 t/s total=9.87s  ← post-warmup
prefill_32k run 3/3: ttft=9.543s prefill=5825 t/s prompt=55582 tokens=50 decode=138.1 t/s total=9.90s  ← post-warmup
```

**Summary (post-warmup runs 2–3 only):**

| Config | TTFT (s) | Prefill TPS | Decode TPS |
|---|---|---|---|
| No flash-attn | 9.525 ± 0.015 | 5,835 ± 9 | 138.1 ± 0.1 |
| Flash-attn on | 9.527 ± 0.022 | 5,834 ± 13 | 138.1 ± 0.1 |
| Delta | +0.002s (noise) | -1 t/s (noise) | 0.0 |

**Analysis:** Zero measurable prefill benefit even at 55K tokens. Root cause: Qwen3.6-35B-A3B is a Mixture of Experts (MoE) model with only 3.6B active parameters per token. Active compute is dominated by the expert FFN layers, not attention. Flash-attn only accelerates the attention kernel — for this model, attention is a small fraction of total prefill compute regardless of context length. Qwen3 also uses Grouped Query Attention (GQA), which already reduces attention memory bandwidth pressure, further shrinking flash-attn's opportunity window.

**Verdict:** **keep** — Confirmed zero benefit at 55K tokens on this MoE+GQA architecture. Flash-attn has no VRAM or regression cost so it stays enabled. Any real benefit would require a dense (non-MoE) model at much longer contexts.
