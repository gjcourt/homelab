---
status: planned
last_modified: 2026-05-07
---

# Frontier-model experiments on hestia (2× RTX 4090) — stability first, perf second

## Context

Hestia recently gained a second RTX 4090 (now 2× 24 GB = 48 GB total VRAM). The existing inference stack runs llama.cpp with `Qwen3.6-35B-A3B-UD-IQ4_NL.gguf` and is well-characterized: TTFT ~0.07–0.08 s, decode ~173–177 tok/s, 22.3 GiB peak (see `docs/research/2026-05-04-llama-cpp-baseline.md`). The vLLM SCALE app has historically been OOM-prone on a single 4090 and is currently flagged "Disabled" in `~/.claude/HOMELAB.md`.

A first attempt to revive vLLM with `Qwen3.6-27B-FP8 + --tensor-parallel-size 2` produced two findings worth recording before going further:

1. **PCIe topology is `NODE`, not `PIX`/`PHB`.** `nvidia-smi topo --matrix` shows the two 4090s sit behind separate PCIe host bridges within the same CPU. vLLM logs `Custom allreduce is disabled because your platform lacks GPU P2P capability or P2P test failed` — every TP all-reduce traverses host RAM.
2. **TP=2 perf is poor on this topology.** Single-stream decode under FP8 + TP=2 measured **32–33 tok/s** (5× slower than llama.cpp on the larger MoE model). Concurrent throughput batches OK (~250 tok/s aggregate at 8 parallel), but per-token latency is unacceptable for the hermes UX.

So the question is no longer "can we run vLLM at all" — it's "given hardware that punishes TP, what's the right stack of model + quant + parallelism + engine for each use case, and how do we make each candidate stable before we measure it." The user's stated priority is explicit: **stability first, benchmarking second.** This plan codifies that order.

The plan also introduces a survey of frontier-class open-weight models worth experimenting with on this hardware, and a matrix of GPU-utilization strategies (single-GPU, TP=2, PP=2, dual independent processes) to test against each candidate.

## Goals

1. **Establish a stability gauntlet** — a fixed sequence of probes that every candidate (model × quant × parallelism × engine) must clear before being benchmarked. Anything that fails the gauntlet is recorded and skipped, not silently re-tuned.
2. **Run a curated set of frontier-class candidates** through the gauntlet on this 2× 4090, no-P2P hardware. The candidate list is sized for "small enough to actually finish, broad enough to draw conclusions."
3. **Benchmark only the survivors** using the existing `scripts/llama-cpp-bench.py` harness so numbers are directly comparable to the llama.cpp baseline.
4. **Pick a recommended default** (or two — e.g., one for hermes coding, one for VLM/multimodal) at the end and either land it in `hosts/hestia/llms/` or document why the existing llama.cpp setup remains the best fit.

Explicitly **not** a goal: get vLLM-TP=2 to match llama.cpp throughput. The hardware doesn't allow it without NVLink. The plan accepts that constraint and instead asks "what's the best we can do on this hardware?"

## Hardware constraints (frozen — do not re-derive each phase)

- 2× RTX 4090 (Ada / SM89), 24 GB each, **no NVLink**.
- PCIe topology between GPU 0 and GPU 1: **`NODE`** (separate PCIe host bridges, same CPU NUMA node). No P2P. vLLM custom all-reduce is disabled by platform detection.
- Implication: every per-token TP all-reduce hits PCIe + host RAM. **TP=2 latency-tax on single-stream is large**; expect 0.7–0.8× per-card scaling under load (1.4–1.5× total throughput, not 2×).
- Practical consequence: prefer **single-GPU configurations** for latency-sensitive single-user workloads. Use the second GPU for a *separate* model (e.g., Whisper STT, Kokoro TTS, a second LLM instance), or for KV-cache headroom only.

## Candidate frontier models

Pulled from a HuggingFace survey, filtered to "fits in 24 GB at a sane quant, frontier-class as of 2025–2026, mature quant ecosystem." Already-cached models marked ✓ — the rest will need pull.

| Tier | Model | HF repo | Best quant for 24 GB | Approx VRAM | Cached? | Why interesting |
|---|---|---|---|---|---|---|
| **A: dense flagship** | Qwen3.6-27B (FP8) | `Qwen/Qwen3.6-27B-FP8` | FP8 | ~22 GB weights | ✓ | Beats 397B-class on coding; 256K native context; the obvious vLLM single-GPU baseline |
| **A** | Qwen3.6-27B (AWQ-INT4) | `cyankiwi/Qwen3.6-27B-AWQ-INT4` | AWQ-INT4 | ~14 GB weights | ✓ | Same model as above; AWQ leaves ~10 GB for KV → much longer context per single GPU |
| **A** | Gemma 4 31B (NVFP4 or AWQ) | `google/gemma-4-31B-it` (community quants) | NVFP4 / AWQ-INT4 | ~18–20 GB | ✗ | Different family, tests vLLM cross-arch stability; 128K context |
| **B: MoE** | Qwen3.6-35B-A3B (INT4) | `Qwen/Qwen3.6-35B-A3B` (community INT4) | INT4 | ~20 GB | ✓ (BF16 + GGUF) | Same model llama.cpp runs in production — direct A/B for vLLM vs llama.cpp |
| **B** | Llama-4-Scout-17B-16E | `meta-llama/Llama-4-Scout-17B-16E-Instruct` | FP8 / Q4 | 10–16 GB | ✗ | Smallest frontier MoE; 32K native, vLLM well-tested |
| **C: reasoning specialist** | DeepSeek-R1-Distill-32B | `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B` | AWQ-INT4 / FP8 | 18–26 GB | ✗ | Reasoning distill; test of CoT-heavy decode patterns on this stack |
| **D: vision-language** | Qwen2.5-VL-7B-Instruct | `Qwen/Qwen2.5-VL-7B-Instruct` | BF16 or FP8 | ~9 GB + KV | ✗ | Comfortable on a single GPU alongside another tenant; tests vLLM multimodal pipeline |

**Skipped on purpose** (oversized at any quant for this hardware): DeepSeek-V4 / V4-Flash, Llama-4-Maverick (400B), Mistral-Medium-3.5 (128B). 70B-dense at INT4 (~40 GB) is forced TP=2 and will recapitulate the all-reduce bottleneck — skipped unless a phase deliberately characterizes that.

The list intentionally stops at 7 candidates. Better to finish the gauntlet on a small set than to half-test fifteen.

## Engines under test

| Engine | Why include |
|---|---|
| **vLLM** (`vllm/vllm-openai:latest`) | Default. Already deployed via SCALE Custom App. OpenAI-compatible API; mature quant support. |
| **llama.cpp** (existing prod) | Reference baseline; already characterized. Used as the "must beat this for vLLM to be worth deploying" bar. |
| **SGLang** (`lmsysorg/sglang:latest`) | Held in reserve. If vLLM single-GPU stability + perf is acceptable, skip. If TP=2 instability blocks Tier A, swap engine on one candidate as a control to see whether the bottleneck is engine-specific. |

ExLlamaV3, TensorRT-LLM, Aphrodite are deliberately out-of-scope for this round (cost/benefit poor versus the time it takes to validate them).

## GPU-utilization strategies (test matrix axes)

For each candidate the gauntlet will be run in up to four configurations, in this priority order:

1. **TP=1 / GPU0 only.** Single-stream baseline. Simplest, most likely to be stable. Sets the floor for what's achievable on this hardware.
2. **TP=1 / GPU0 only, with a second model on GPU1.** "Two tenants" — e.g., Qwen3.6-27B on GPU0 and Whisper Turbo on GPU1. Tests whether running two independent vLLM (or other) processes coexists without cross-GPU OOM / scheduler interference.
3. **TP=2.** Only attempted if the model genuinely doesn't fit at any quant on a single 24 GB card (e.g., Qwen3.6-27B-FP8 at full 65K context — see KV cache math in execution). Result: numbers for the record; expectation is "slow but functional."
4. **PP=2 (`--pipeline-parallel-size 2`).** Pipeline parallelism — avoids per-token all-reduce, but introduces pipeline bubbles at batch=1. Worth one test on the dense Qwen3.6-27B-FP8 to see if it's a meaningful alternative to TP=2 on no-P2P hardware.

Not every candidate runs in every configuration. Selection matrix lives in the execution table below.

## Stability gauntlet (the "is this config even usable?" check)

Every candidate config must pass these in order before being benchmarked. Failing any step = config is recorded as failed-stability and dropped, not re-tuned beyond the obvious knob mentioned in the failure mode.

| Step | Probe | Pass criterion | Typical failure mode → first fix |
|---|---|---|---|
| 0. Cold load | `vllm serve …` (or engine equivalent) → wait for `/health` to return 200 | Healthy in < 5 min, no CUDA OOM in log | OOM at CUDA-graph capture → drop `--gpu-memory-utilization` to 0.78, set `--enforce-eager` |
| 1. Single-shot smoke | `curl /v1/chat/completions` with a 5-token prompt, max_tokens=20 | Returns valid completion, no NCCL hang | NCCL timeout → check `--ipc=host`, `--shm-size=16gb`, `NCCL_SHM_DISABLE` |
| 2. Streaming | Same prompt with `stream: true` | Tokens stream as SSE; no chunked-encoding reset | Connection reset → benchmark from on-host, not cross-LAN |
| 3. Long-prompt prefill | 8K-token prompt, max_tokens=128 | Completes; VRAM peak < 23 GB | KV blow-up → reduce `--max-model-len` |
| 4. Concurrent baseline | 8 parallel single-shot requests | All complete in < 30 s; no scheduler errors | Crash under concurrency → likely CUDA-graph race; force `--enforce-eager` |
| 5. 30-min soak | 1 req/sec for 30 min, mixed prompt sizes | No memory creep > 0.5 GB/hr; no crashes | Memory creep → file engine bug, mark unstable |
| 6. Long-context probe | Single request at ~75% of declared `--max-model-len` | Completes; output coherent | Garbled output → KV cache spec mismatch, drop quant or reduce context |

A config has to clear **steps 0–4** to qualify for benchmarking. Steps 5 & 6 are recorded for the survivors but not gating.

Per-step "first fix" is allowed — anything beyond is a new variant and a new gauntlet run, not silent re-tuning. This is the same discipline as the llama.cpp tuning plan: change one knob, record the result.

## Operational pattern

Borrowed verbatim from `docs/plans/2026-05-04-llama-cpp-benchmarking.md` and adapted:

For each candidate × configuration:

1. Stop competing GPU users: `ssh truenas_admin@10.42.2.10 'midclt call app.stop llama-cpp'` (and any other vLLM instances). Confirm with `nvidia-smi`.
2. Update `vllm` SCALE app config via `midclt call app.update vllm '{...}'` with the variant compose YAML. Keep the canonical YAML in `hosts/hestia/llms/docker-compose-vllm-experiment.yml` (new file, not the prod one) so each variant is reviewable.
3. Start: `midclt call app.start vllm`. Wait for `/health` 200.
4. Run the stability gauntlet (steps 0–6). Record each step's outcome in the research log.
5. If gauntlet passes: run `BASE_URL=http://10.42.2.10:8000/v1 MODEL=<served-name> RUNS=5 python3 scripts/llama-cpp-bench.py --jsonl /tmp/<variant>.jsonl` from on-host (`scp` the script to `/tmp/llama-cpp-bench.py` on hestia first — running cross-LAN hits Python 3.14 chunked-encoding resets).
6. Sample VRAM peak in a parallel terminal: `while true; do nvidia-smi --query-gpu=memory.used --format=csv,noheader; sleep 1; done | tee /tmp/vram-<variant>.txt`.
7. Stop variant. Restart prod llama.cpp.
8. Append a new section to `docs/research/2026-05-07-vllm-frontier-experiments.md` (created in Phase 0) with: variant identity, gauntlet outcomes, harness numbers (mean ± stddev), VRAM peak, and a one-line verdict (pass / fail-stability / fail-perf).

Total wall-clock per variant: ~30–45 min when the gauntlet passes; ~10 min when it fails early.

## Execution phases

Each phase = one candidate + configuration. Each phase opens a PR (compose change + research-log entry).

| # | Candidate | Config | Goal | Stop condition |
|---|---|---|---|---|
| 0 | (no model) | n/a | Baseline gathering: confirm both GPUs free, `nvidia-smi topo --matrix` snapshot, current llama.cpp prod numbers re-captured for sanity. Create `docs/research/2026-05-07-vllm-frontier-experiments.md`. | n/a |
| 1 | Qwen3.6-27B-AWQ-INT4 | TP=1, GPU0 | Smallest single-GPU candidate. Highest probability of passing the gauntlet cleanly; sets the "best vLLM single-stream on this hardware" reference point. | Gauntlet passes → benchmark. Fails at step 0–2 → drop AWQ for this model, try FP8. |
| 2 | Qwen3.6-27B-FP8 | TP=1, GPU0 | Same model, native FP8 weights. Tests Ada FP8 path. May not fit single-GPU at 65K context — if not, reduce `--max-model-len` rather than escalating to TP=2. | Pass → benchmark. Fail OOM with `--max-model-len 8192` → escalate to TP=2 in Phase 4. |
| 3 | Qwen3.6-27B-AWQ-INT4 + Qwen2.5-VL-7B | dual independent procs (GPU0 + GPU1) | Tests two-tenant scenario. Two vLLM instances on different ports, different `CUDA_VISIBLE_DEVICES`. | Both pass smoke → mark "dual tenancy works." VLM stability separately recorded. |
| 4 | Qwen3.6-27B-FP8 | TP=2 | Already attempted; recapture with stability gauntlet for the record (and as a control vs PP=2). | Numbers in the log; not expected to win. |
| 5 | Qwen3.6-27B-FP8 | PP=2 | Pipeline parallelism alternative to TP=2. Single most interesting hypothesis test of this plan. | Pass + decode TPS > 60 t/s → meaningfully better than TP=2; promote to a real candidate. Otherwise: record and move on. |
| 6 | Qwen3.6-35B-A3B (INT4 community quant) | TP=1, GPU0 | Direct vLLM-vs-llama.cpp A/B on the model already in prod. Most useful single data point for "should we switch?" | Pass + decode TPS within 20% of llama.cpp's 173 t/s → switch is viable. Otherwise: llama.cpp stays. |
| 7 | Llama-4-Scout-17B-16E (FP8) | TP=1, GPU0 | Cross-family sanity check. Different MoE architecture, tests vLLM beyond the Qwen3 family. | Stability data point; perf data point. |
| 8 | DeepSeek-R1-Distill-Qwen-32B (AWQ-INT4) | TP=1, GPU0 | Reasoning distill; relevant to a possible hermes-bot tier-up. | Same; record. |
| 9 (opt) | One of {1,2,6} | SGLang engine, same config as the corresponding vLLM phase | Engine-comparison control. Only run if any of phases 1/2/6 fails stability or shows surprising perf — to determine "is this vLLM-specific or engine-agnostic?" | n/a — diagnostic only. |

Phases 1, 2, 6 are the **must-do** phases. Phases 3–5, 7–8 are conditional — reorder freely as findings change. Phase 9 is held in reserve.

## Pause / abort criteria

- **Repeated cold-load OOM** across 3+ candidates with `--gpu-memory-utilization` already at 0.75 → likely a host-level driver / VRAM-fragmentation issue. Stop, capture `dmesg`, file as a separate troubleshooting task.
- **Gauntlet step 5 (soak) finds memory creep > 1 GB/hr** → engine bug. Don't promote anything in that engine version; pin a known-good version in the compose file.
- **Two consecutive phases produce TPS < 30 t/s on a single-GPU dense model** → vLLM on this hardware is fundamentally not viable; close out the plan with a "stay on llama.cpp" recommendation rather than continuing through the matrix.
- **hermes-bot user reports increased latency during a phase's brief outage window** → switch the operational pattern from "stop prod, run variant" to "spin variant on a different port, leave prod up."

## Per-phase PR template

- Variant compose YAML lands at `hosts/hestia/llms/docker-compose-vllm-experiment.yml` (a single canonical file, content swapped per phase). Don't touch `docker-compose-vllm.yml` (the prod-marked-disabled file) until a phase has a clear "promote this" verdict.
- New section appended to `docs/research/2026-05-07-vllm-frontier-experiments.md`, structured per the operational pattern above.
- Commit message: `feat(vllm): phase N — <model>/<quant>/<config> — <verdict>`. Example: `feat(vllm): phase 1 — Qwen3.6-27B-AWQ-INT4/TP=1 — pass, decode 95 t/s`.
- PR body cross-links the research-log section.

## Out of scope

- **NVLink / hardware change** — not happening in this plan.
- **Patching NVIDIA driver to enable P2P on consumer GPUs** (smcleod approach). Recorded as a follow-up if PP=2 also disappoints; not attempted here because it's a separate risk surface.
- **Multi-host inference.**
- **TTS / STT integration** — referenced in candidate selection (the "two-tenant" config) but not the focus.
- **Quality / accuracy benchmarks.** This plan measures perf and stability only. If a config trades quality for speed, flag qualitatively in the research log; rigorous quality is a separate plan.
- **hermes-bot rewiring** — keep `hermes-config-yaml` pointed at the existing endpoint until the plan produces a "promote" verdict; the model-name mismatch errors observed during phase 4 of this conversation are pre-existing config drift and don't block this work.

## Critical files

- `hosts/hestia/llms/docker-compose-vllm-experiment.yml` — **new**, single file rewritten per phase, `vllm` SCALE app's `custom_compose_config` mirrors it.
- `hosts/hestia/llms/docker-compose-vllm.yml` — existing prod-disabled config; do not modify until a phase produces a promote verdict.
- `hosts/hestia/llms/docker-compose-llama-cpp.yml` — production reference; not modified by this plan.
- `scripts/llama-cpp-bench.py` — **reused as-is**. Endpoint-agnostic. Already detects Qwen3 `reasoning_content` for accurate TTFT.
- `docs/research/2026-05-07-vllm-frontier-experiments.md` — **new**, created in Phase 0; per-phase results appended.
- `docs/plans/2026-05-07-vllm-frontier-model-experiments.md` — **this plan**, copy of this file.

## Verification (how to know the plan is doing its job)

- After each phase, the research log has a self-contained section with: variant compose snippet, gauntlet step-by-step outcomes, harness numbers, VRAM peak, verdict. Anyone reading just that section can re-run it without context.
- After phase 6, the research log answers: "is vLLM viable as a hermes backend on this hardware, yes or no?"
- After all must-do phases, this plan's `status:` flips from `planned` → `in-progress` → `complete`. The `complete` verdict comes with a recommendation: either a PR proposing the new prod config in `hosts/hestia/llms/`, or a paragraph closing out the question with "llama.cpp wins; here's why."

## Cross-references

- Predecessor plan: [`docs/plans/2026-05-04-llama-cpp-benchmarking.md`](2026-05-04-llama-cpp-benchmarking.md) — same methodology, different engine.
- llama.cpp baseline: [`docs/research/2026-05-04-llama-cpp-baseline.md`](../research/2026-05-04-llama-cpp-baseline.md) — reference numbers (TTFT ~0.07 s, decode ~173 t/s, 22.3 GiB peak).
- llama.cpp tuning results: [`docs/research/2026-05-04-llama-cpp-tuning-results.md`](../research/2026-05-04-llama-cpp-tuning-results.md) — Phases 1–6 already done.
- Existing prod compose: [`hosts/hestia/llms/docker-compose-llama-cpp.yml`](../../hosts/hestia/llms/docker-compose-llama-cpp.yml).
- vLLM (disabled) compose: [`hosts/hestia/llms/docker-compose-vllm.yml`](../../hosts/hestia/llms/docker-compose-vllm.yml).
- Hardware notes: `~/.claude/HOMELAB.md` (operator-private; will need an update post-plan to remove the "vLLM disabled" annotation if a phase produces a promote verdict).

## Delivery

This plan is delivered via PR to the `homelab` repo:

1. Branch `feat/vllm-frontier-experiments-plan` off `origin/master`.
2. Add `docs/plans/2026-05-07-vllm-frontier-model-experiments.md` (copy of this file, frontmatter `status: planned`).
3. Update `docs/plans/README.md` — add an index row for the new plan.
4. Open PR titled `docs(plans): vLLM frontier-model experiments on hestia (2× 4090)`. PR body summarizes context (P2P NODE topology, prior 33 t/s TP=2 result, stability-first methodology) and links the predecessor llama.cpp plan.

The plan PR contains no compose or script changes — those land in the per-phase PRs that follow.
