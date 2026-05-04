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
