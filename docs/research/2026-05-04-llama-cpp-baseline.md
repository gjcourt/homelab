---
status: in-progress
last_modified: 2026-05-04
---

# llama.cpp baseline measurement on hestia (RTX 4090) — Phase 0

Phase 0 of [`docs/plans/2026-05-04-llama-cpp-benchmarking.md`](../plans/2026-05-04-llama-cpp-benchmarking.md). Establishes a reference baseline of the current production llama.cpp config so future tuning phases (Phase 1 onward) have something concrete to compare against.

> **Status note**: this doc is `in-progress` while the operator runs the measurements on hestia. Once numbers are pasted into the placeholder sections, status flips to `complete` (per `docs/research/README.md`'s "research is frozen once written" convention — what's frozen is the *measurement at one point in time*, not the abstract idea of benchmarking).

## Config under test

The pre-#434 / post-#441 production config in `hosts/hestia/llms/docker-compose-llama-cpp.yml`:

```
--ctx-size 409600
--cache-type-k q8_0
--cache-type-v q8_0
--n-gpu-layers 99
--parallel 1
--cont-batching
--threads 8
--temp 0.6
--top-k 20
--top-p 0.95
--min-p 0
--presence-penalty 0
--repeat-penalty 1
--chat-template-kwargs '{"preserve_thinking":true}'
```

Image: `ghcr.io/ggml-org/llama.cpp@sha256:a8c56356fbfde209910b8098d0a060f4b84997f23b65491df3f2b61fae91dd7b`.
Model: `Qwen3.6-35B-A3B-UD-IQ4_NL.gguf` at `/mnt/main/ai/models/gguf/`.

## How to run (operator, on hestia or another LAN-connected host)

### 0. Capture host state for context

```bash
ssh truenas_admin@10.42.2.10 'echo "--- nproc ---"; nproc; \
  echo "--- gpu ---"; nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv; \
  echo "--- llama image ---"; docker inspect llama --format "{{.Image}} {{.Config.Image}}"'
```

Paste the output into the [Host context](#host-context) section below.

### 1. Verify `llama-bench` is available in the image

```bash
ssh truenas_admin@10.42.2.10 'docker exec llama llama-bench --help' | head -5
```

If it returns a help message, run `llama-bench` per step 2. If "executable not found in $PATH" or similar, **skip step 2** and rely on the curl harness in step 3 alone — note that under "llama-bench output" below.

### 2. Run `llama-bench` (kernel-level synthetic)

```bash
ssh truenas_admin@10.42.2.10 \
  'docker exec llama llama-bench \
     -m /models/Qwen3.6-35B-A3B-UD-IQ4_NL.gguf \
     --n-gpu-layers 99 \
     -p 256,1024 \
     -n 128 \
     -r 5 \
     -o md'
```

`-o md` produces a Markdown table — paste it directly into [llama-bench output](#llama-bench-output) below.

### 3. Run the curl harness (HTTP / E2E)

From hestia or any LAN-connected machine:

```bash
BASE_URL=http://10.42.2.10:8000/v1 \
  python3 scripts/llama-cpp-bench.py \
  --jsonl /tmp/llama-bench-baseline.jsonl
```

Save both the summary table (stdout) and the per-run trace (stderr) — paste both into [Curl harness output](#curl-harness-output) below. The JSONL file at `/tmp/llama-bench-baseline.jsonl` is for archive.

### 4. Sample VRAM peak in a parallel terminal

```bash
ssh truenas_admin@10.42.2.10 \
  'while true; do nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits; sleep 1; done' \
  | tee /tmp/vram-baseline.txt
```

Run this **before** starting step 3 and Ctrl-C **after** step 3 finishes. Note the highest value (and the post-run steady-state) in [VRAM peak](#vram-peak) below.

## Host context

> _operator: paste output from step 0 here_

```
nproc:
gpu:
llama image:
```

## llama-bench output

> _operator: paste step 2 output here, or note "skipped — `llama-bench` not in image" if step 1 indicated absence_

```

```

## Curl harness output

> _operator: paste step 3 output here (both the stdout summary table and the stderr per-run trace)_

```

```

## VRAM peak

> _operator: paste highest steady-state VRAM use (MiB) and the post-run idle-state from step 4_

- Peak during run: ___ MiB
- Steady-state after run completes: ___ MiB

## Latency budget

Once the numbers above are filled in, codify the acceptable thresholds for hermes-bot UX. Defaults to fill in / adjust:

- TTFT (medium workload): **target < ___ s** (95th percentile)
- Sustained decode TPS (medium workload): **target > ___ t/s**
- VRAM peak (medium workload, single-stream): **target < 22 GiB** (2 GiB headroom under 24 GiB ceiling)

These thresholds become the pass/fail criteria for Phase 1 onward. Update `docs/plans/2026-05-04-llama-cpp-benchmarking.md` once they're set.

## Surprises / observations

> _operator: anything that didn't match expectations during the run — model load time, log-line warnings, transient errors, GPU thermal, etc._

## Verdict

> _operator: a one-line summary once measurements are in_

- Baseline established: yes / no — _date_
- Anything blocking Phase 1: _none / list_
