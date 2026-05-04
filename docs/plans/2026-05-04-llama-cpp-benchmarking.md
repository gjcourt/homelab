---
status: planned
last_modified: 2026-05-04
---

# Systematic llama.cpp benchmarking on hestia (RTX 4090)

## Context

PR #434 changed six llama.cpp flags simultaneously and tanked inference perf so badly that hermes-bot interactions became "nearly unusable." We reverted (PR #441, merged 2026-05-04). The reverted state — `--ctx-size 409600`, `--cache-type-k/v q8_0`, `--parallel 1`, `--threads 8`, no `--flash-attn` — is the working baseline.

The lesson: **changing six knobs at once with no measurement leaves the regression unbisectable.** Reverting was easy because there was a single offending PR; if a regression had crept in across multiple changes, recovery would have been painful. Future tuning needs to land one flag at a time, with measured before/after numbers.

This plan defines the methodology and the per-flag sequence. It does not attempt any tuning; that's the execution phases below.

## Goals

1. **Establish a recorded baseline of the current config** so future regressions are bisectable, even if no further tuning happens.
2. **Define a measurement methodology** — what tools, what workload, what metrics, what counts as a win.
3. **Re-evaluate each #434 flag individually** against the baseline.
4. **Land winners**, record losers in the research log with the trade-off.

## Measurement approach

### `llama-bench` — kernel-level synthetic perf

Hermetic in-container benchmark. Doesn't go through HTTP. Reports prefill TPS, decode TPS, model load time across context lengths.

```bash
ssh truenas_admin@10.42.2.10 'docker exec llama llama-bench \
  -m /models/Qwen3.6-35B-A3B-UD-IQ4_NL.gguf \
  -p 256 -n 128 \
  --n-gpu-layers 99 \
  -r 5'
```

To verify: `docker exec llama llama-bench --help`. Likely present in `ghcr.io/ggml-org/llama.cpp` (CUDA variant ships full `bin/`); confirm in Phase 0. If absent, drop this section and rely entirely on the curl harness.

### curl-based E2E harness — HTTP-layer perf

A small shell script (`scripts/llama-cpp-bench.sh`, added in Phase 0's PR) hits `http://10.42.2.10:8000/v1/chat/completions` with a fixed prompt corpus and measures **time-to-first-token** + **sustained tokens/sec** via SSE streaming.

Workload corpus (lives in the script):

| Prompt | Prompt size | Response target | Measures |
|---|---|---|---|
| Short Q&A | ~50 tok | ~50 tok | TTFT |
| Medium reasoning | ~500 tok | ~500 tok | sustained decode TPS |
| Long synthesis | ~4000 tok | ~1000 tok | prefill bandwidth + KV memory |
| Concurrent stress | 3 × short | 3 × ~50 tok | only run when `--parallel > 1` |

5 runs per prompt; report mean + stddev; discard the first run (warm-up).

### VRAM peak

Sampled during the run:

```bash
ssh truenas_admin@10.42.2.10 \
  'while true; do nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits; sleep 1; done' | \
  awk 'NR==1 || $1>max{max=$1} END{print "peak="max" MiB"}'
```

Track the high-water mark across the workload, plus the post-run steady-state.

## Operational pattern

For each variant config:

1. Note the current production state: `ssh hestia 'docker ps --filter name=llama'`.
2. Stop the production llama container: `ssh hestia 'docker stop llama'` — ~30s outage to bots/CLI.
3. Launch a variant container with the proposed flag changed. Use a different `container_name` (`llama-bench`) so prod's data dir isn't disturbed and we can roll back instantly:
   ```bash
   ssh hestia 'docker run --rm --gpus all --name llama-bench \
     -v /mnt/main/ai/models/gguf:/models:ro \
     -p 8000:8080 \
     ghcr.io/ggml-org/llama.cpp@sha256:a8c5… \
     <flags-under-test>'
   ```
4. Run `llama-bench` (if available) + the curl harness + VRAM sampler.
5. Stop the variant. Restart prod: `ssh hestia 'docker start llama'`.
6. Append a new section to `docs/research/2026-05-04-llama-cpp-tuning-results.md` with: variant flags diff, raw `llama-bench` table, harness numbers (mean ± stddev per workload), VRAM peak, and a one-line verdict.
7. If the verdict is **keep**, open the phase's PR (compose change + research-log entry already in the doc). If **revert** or **further-tune**, document the why in the research log.

Total wall-clock per phase: ~10-15 min including the brief outage.

## Results tracking

`docs/research/2026-05-04-llama-cpp-tuning-results.md` (created in Phase 0). Each section's structure:

```markdown
## Phase N — <flag-changed>

**Variant flags vs baseline:**
   <diff snippet>

**llama-bench:** <table or "skipped, see open question">

**curl harness (mean ± stddev across 5 runs after warmup):**

| Workload | TTFT (s) | Sustained TPS | Total wall (s) |
|---|---|---|---|

**VRAM peak:** <MiB>

**Verdict:** keep / revert / further-tune — <one-line rationale>
```

## Test order (hypothesis-driven)

| Phase | Change | Hypothesis | Expected impact |
|---|---|---|---|
| **0** | None (baseline) | n/a | Reference numbers; no PR, just research-log entry |
| **1** | `--ctx-size 409600 → 32768` | KV cache @ q8_0 for 400K tokens consumes most of the 4090's 24GB, forcing CPU↔GPU paging during decode. Highest-probability lever. | Big VRAM drop, decode TPS up |
| **2** | `--flash-attn on` (additive) | Standard attention-kernel optimization on Ada (sm89). Should be additive. | Modest decode TPS up |
| **3** | `--threads 8 → 12 → 16` | Depends on hestia's actual physical core count (capture `nproc` in Phase 0). 16 may oversubscribe. | Find the sweet spot per hostware |
| **4** | `--cache-type-k/v q8_0 → f16` | Only if Phase 1 freed enough VRAM. Long-context recall quality may improve; perf impact mixed. | VRAM up, possibly TPS down |
| **5** | `--parallel 1 → N` | Only if multi-user demand emerges. Today with two hermes-bot personas, peak concurrency rarely > 1. | Throughput up under concurrency, single-stream TPS may drop |
| **6** | `--batch-size` / `--ubatch-size` | Effect depends on prior tuning; likely no-op on single-stream. | Last; only if motivated by a specific bottleneck Phase 5 surfaces |

Each phase is one PR.

## Pause / abort criteria

- If any phase causes **>10% regression in decode TPS** at the medium workload, revert that PR and flag in the research log. Don't stack changes on top of an unverified loss.
- If a phase causes a **VRAM regression that pushes peak >22 GB** (within 2 GB of the 24 GB ceiling), revert — too close to OOM under any concurrent pressure.
- If hermes-bot users report a noticeable UX change between phases, treat it as a regression even if synthetic numbers look fine; investigate before continuing.

## Per-phase PR template

- One-line compose change in `hosts/hestia/llms/docker-compose-llama-cpp.yml`.
- New section appended to `docs/research/2026-05-04-llama-cpp-tuning-results.md`.
- Commit message format: `feat(llama-cpp): <flag> <old-value> → <new-value> (<measured-delta>)`. Example: `feat(llama-cpp): ctx-size 409600 → 32768 (+38% decode TPS, -14GB VRAM peak)`.
- PR body cross-links the research-log section.

## Out of scope

- **Model swap** (different GGUF, different quant) — separate exercise.
- **Hardware changes.**
- **Revisiting vLLM** — operator-side decision; this plan stays scoped to llama.cpp.
- **Multi-host inference / cross-box batching.**
- **Quality / accuracy testing** — this plan measures performance only. If a config trade quality for speed, flag it qualitatively in the research log; rigorous quality comparison is a separate plan.

## Open questions for execution phase

- **Verify `llama-bench` is in the pinned image.** First action of Phase 0. If absent, drop the synthetic-bench section and rely on the curl harness alone; document the fallback inline.
- **Codify the latency budget.** Right now it's "user says it feels slow." Phase 0's measurements will let us pick concrete thresholds (e.g., TTFT < 2s, sustained decode > 30 t/s on the medium workload). Land the thresholds back into this plan after Phase 0.
- **Maintenance window vs ad-hoc.** Current decision: ad-hoc, accept ~30s prod outage per phase. Revisit if hermes-bot users complain.

## Cross-references

- The PR that prompted this: [#434](https://github.com/gjcourt/homelab/pull/434) (merged then reverted).
- The revert: [#441](https://github.com/gjcourt/homelab/pull/441).
- Compose: [`hosts/hestia/llms/docker-compose-llama-cpp.yml`](../../hosts/hestia/llms/docker-compose-llama-cpp.yml).
- Image source: [`ghcr.io/ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp/pkgs/container/llama.cpp).
