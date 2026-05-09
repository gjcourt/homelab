---
status: planned
last_modified: 2026-05-09
---

# vLLM vision sidecar on hestia — add image-input capability without disturbing the text winner

## Context

The vLLM frontier-model experiments plan ([`2026-05-07-vllm-frontier-model-experiments.md`](2026-05-07-vllm-frontier-model-experiments.md), status `complete`) landed `cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit` on TP=2 / vLLM v0.20.1 / 160 K context as the production text endpoint. That model is text-only — `Qwen3_5MoeForConditionalGeneration` with no vision tower.

Operator wants vision (screenshot Q&A, diagrams, photos) added as a hermes capability. Two paths were surfaced during the experiments:

1. **Replace** the text winner with a 30B-class VL model (`cyankiwi/Qwen3-VL-30B-A3B-Instruct-AWQ-4bit`). Same MoE skeleton, ships a vision tower. Decode drops from 194 t/s to ~120–150 t/s due to vision-encoder overhead. **Trade-off: loses the text-only perf win we just earned.**
2. **Sidecar** a small dedicated VL on whatever VRAM headroom remains alongside the text winner. The 35B-A3B AWQ at TP=2 with `--gpu-memory-utilization 0.95` consumes ~22.5 GiB per GPU, leaving ~1.5 GiB free. Tight, but enough for a sub-2B-class model.

This plan is the sidecar route. The replacement route stays available as a fallback if the sidecar doesn't deliver acceptable vision quality.

## Goals

1. **Stand up a second vLLM container** on a different port (8001) serving a small VL model on a single GPU, without disturbing the running text endpoint on port 8000.
2. **Validate the coexistence** — text endpoint stays at its current 194 t/s; vision endpoint serves image+text turns at acceptable quality and latency for hermes.
3. **Wire hermes** to route image-bearing messages to the vision endpoint and text-only messages to the text endpoint. (May happen in a follow-up PR — this plan is scoped to the vLLM side.)
4. **Decide retention** after a real-traffic period: keep the sidecar if it's getting used and the text winner remains stable; rip it back out if it's adding nothing useful.

## Hardware budget — the constraint that forces a small sidecar

Phase 6 v2 prod config:

```
--gpu-memory-utilization 0.95     # vLLM reserves 22.8 GiB per 24 GiB GPU
--max-model-len 163840            # consumes the freed-up KV pool
--max-num-seqs 4                  # capped well below the pool's 10.87× headroom
```

Steady-state observation: **GPU0 22,507 MiB / GPU1 22,507 MiB used; ~1,557 MiB free per GPU**.

A vLLM sidecar must fit weights + activations + KV cache + CUDA workspace + graph buffers in that ~1.5 GiB envelope on one GPU. Realistic candidates:

| Tier | Model | Disk | Estimated VRAM @ FP8 / INT4 | Fits as-is? |
|---|---|---|---|---|
| Pure vision (no text gen) | `microsoft/Florence-2-large` | 0.8 GB | ~1.2 GiB BF16 | ✅ Yes — best for OCR/captioning/detection |
| Pure vision (no text gen) | `vikhyatk/moondream2` (INT4) | 0.4 GB | ~0.8 GiB | ✅ Yes — captioning + simple Q&A, no chat history |
| Tiny VL conversational | `Qwen/Qwen3-VL-2B-Instruct-FP8` | ~2.0 GB | ~2.4 GiB | ⚠️ Tight — needs Phase 6 v2 to release some headroom (see below) |
| Small VL conversational | `Qwen/Qwen3-VL-4B-Instruct-FP8` | ~3.5 GB | ~4 GiB | ❌ No — would need Phase 6 v2 to drop to ~0.85 utilization |

**Recommended path: `Qwen/Qwen3-VL-2B-Instruct-FP8`**, with a small Phase 6 v2 utilization tweak (0.95 → 0.92) to give it headroom.

Why this candidate:

- Same Qwen family as the text winner → same OpenAI multimodal content format → no hermes SDK changes.
- FP8 is well-tested in vLLM v0.20.x; not the experimental NVFP4 path that bit us in Phase 7 v2.
- 2B is the smallest *conversational* VL — Florence-2 / Moondream are smaller but don't carry a chat-tuned response style.
- `Qwen/Qwen3-VL-8B-Instruct-FP8` is a well-known, stable fallback if 2B quality is inadequate (would require dropping Phase 6 v2 to TP=1 to free a GPU for it — out of scope for this plan, see "Out of scope" below).

## The Phase 6 v2 utilization tweak

To make ~2.5 GiB of additional headroom available on **GPU1** (where the sidecar will pin), reduce Phase 6 v2's `--gpu-memory-utilization` from 0.95 to 0.92:

| Flag | Current | New |
|---|---|---|
| `--gpu-memory-utilization` | 0.95 | 0.92 |
| `--max-num-seqs` | 4 | 4 (unchanged) |
| `--max-model-len` | 163,840 | 163,840 (unchanged) |
| `--kv-cache-dtype` | fp8 | fp8 (unchanged) |

Memory math at 0.92:

- Per-GPU vLLM reservation: 22.08 GiB (down from 22.80 GiB → ~720 MiB freed per GPU).
- KV cache pool shrinks proportionally — was 1,780,261 tokens at 0.95; estimated ~1,540,000 tokens at 0.92.
- Max-concurrency at 160 K request size: ~9.4× (was 10.87×). Still well above `--max-num-seqs 4`. **No user-visible regression.**
- Per-GPU free VRAM after the tweak: ~2.3 GiB (was ~1.5 GiB).

The 2 B FP8 sidecar fits in that ~2.3 GiB on GPU1 with margin. The text winner's per-token decode rate is unchanged (KV pool size doesn't affect decode speed at low concurrency).

## Architecture

Two vLLM containers, one SCALE app each, one GPU each, two ports:

```
hestia (TrueNAS Custom Apps)
├── vllm           SCALE app — port 8000
│   image          vllm/vllm-openai:v0.20.1
│   model          cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit
│   pinning        device_ids: ['0','1']  (TP=2)
│   tweak          --gpu-memory-utilization 0.92  (was 0.95)
│
└── vllm-vision    SCALE app — port 8001 (NEW)
    image          vllm/vllm-openai:v0.20.1
    model          Qwen/Qwen3-VL-2B-Instruct-FP8
    pinning        device_ids: ['1']      (single GPU)
    --gpu-memory-utilization 0.10         (~2.4 GiB cap)
    --max-model-len 8192
```

GPU0 is the dedicated text GPU (no sidecar interference). GPU1 hosts both the text TP=2 share AND the small vision model. Two vLLM processes don't share a CUDA context — they coexist as standard multi-tenant GPU consumers.

## Stability gauntlet (sidecar-specific)

Phase 6 v2's gauntlet (steps 0–6) has already validated the text endpoint at its post-tweak config (the only change is the `--gpu-memory-utilization` 0.95 → 0.92, which the cache-pool math above shows is non-disruptive). The sidecar runs its own gauntlet:

| Step | Probe | Pass criterion |
|---|---|---|
| 0 | Cold load → port 8001 `/health` 200 | Healthy in < 5 min |
| 1 | Single-shot text smoke (no image) | Returns valid completion |
| 2 | Single-shot vision smoke (one small image) | Returns plausibly correct caption / answer |
| 3 | Multi-image turn (2-3 images in one message) | Handles batch of image content blocks |
| 4 | Concurrent baseline (4 parallel image-bearing requests) | All complete; no scheduler errors |
| 5 | 30-min soak — alternating text-only and image-bearing requests | No memory creep; text endpoint perf undisturbed |
| 6 | Long text context with image attached | Maintains text quality alongside vision |

Concurrent **co-tenancy** check is the unique-to-this-plan addition: while the sidecar runs, the text endpoint must continue to serve at its measured 194 t/s. A simultaneous benchmark of port 8000 during the sidecar's soak step is the gating metric.

## Operational pattern

1. Update Phase 6 v2's compose to the 0.92 utilization (one trivial PR; redeploy is ~5 min, ad-hoc outage acceptable).
2. Create the new `vllm-vision` SCALE Custom App with the sidecar compose. The compose YAML lives at `hosts/hestia/llms/docker-compose-vllm-vision.yml` (new file in this plan's first execution PR).
3. Cold-load + smoke-test the sidecar.
4. Run the gauntlet (steps 0–6).
5. If all pass, leave both endpoints running. Wire hermes (separate PR).
6. Decision after 7 days of real traffic: keep the sidecar, swap up to 4B/8B, or remove.

## Critical files

- `hosts/hestia/llms/docker-compose-vllm.yml` — **modify** (`--gpu-memory-utilization 0.95` → `0.92`).
- `hosts/hestia/llms/docker-compose-vllm-vision.yml` — **new** (sidecar compose).
- `docs/research/2026-05-09-vllm-vision-sidecar.md` — **new**, gauntlet results log per the same pattern as `2026-05-07-vllm-frontier-experiments.md`.
- `~/.claude/HOMELAB.md` (operator-private) — append the second endpoint URL.
- (Eventually) hermes-config-yaml or routing config to dispatch image-bearing messages to port 8001.

## Out of scope

- **Replacement-mode VL** (single 30B-A3B-VL model on TP=2) — separate plan if the sidecar route turns out to be insufficient.
- **Larger sidecar** (4B or 8B Qwen3-VL) — would require dropping the text winner from TP=2 to TP=1 (single-GPU AWQ-INT4 27B from Phase 1, which decoded at 50 t/s instead of 194 t/s). That's a meaningful regression to the text path; treat it as its own plan if the 2B sidecar is quality-inadequate.
- **Hermes routing** — needs SDK / config changes in the hermes deployment. Plan-of-record this work after the sidecar is up.
- **Vision-only models without chat semantics** (Florence-2, Moondream) — viable but a different integration pattern (no `/v1/chat/completions`); recorded here as a fallback if Qwen3-VL-2B has issues but not the primary path.

## Verification

Plan succeeds when:

- Both vllm and vllm-vision SCALE apps RUNNING simultaneously, each healthy.
- Text endpoint (port 8000) re-benchmarks at ≥ 190 t/s decode for the medium workload — confirms the 0.92 utilization tweak hasn't regressed perf.
- Vision endpoint (port 8001) responds to a curl POST with an image attachment and produces a correct one-sentence description within 5 s.
- 30-min combined soak: 0 errors on both endpoints, no VRAM creep on either GPU, text endpoint TPS within noise of solo-mode measurement.

## Cross-references

- Parent (completed): [`docs/plans/2026-05-07-vllm-frontier-model-experiments.md`](2026-05-07-vllm-frontier-model-experiments.md) — wins the text endpoint.
- Sibling research (text-mode results): [`docs/research/2026-05-07-vllm-frontier-experiments.md`](../research/2026-05-07-vllm-frontier-experiments.md) — Phase 7-bis section sketches the vision-replacement alternative.
- vLLM multimodal docs: <https://docs.vllm.ai/en/stable/features/multimodal_inputs/>
- Qwen3-VL recipe: <https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3-VL.html>
- HF model: <https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-FP8>
