---
status: complete
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

```
nproc: 64
gpu: NVIDIA GeForce RTX 4090, driver 590.44.01, 24564 MiB total
llama image: sha256:4846a739f6367534ab02953e9aa454803f22079528e5bcb3b7b4204269465b74 (ghcr.io/ggml-org/llama.cpp@sha256:a8c56356fbfde209910b8098d0a060f4b84997f23b65491df3f2b61fae91dd7b)
```

## llama-bench output

> `llama-bench` not available in the llama.cpp Docker image (`exec: "llama-bench": executable file not found in $PATH`). Skipped — relying on curl harness alone.

```
SKIPPED — llama-bench not in image
```

## Curl harness output

> Runs: 5 per workload (run 1 discarded as warmup). Model: Qwen3.6-35B-A3B-UD-IQ4_NL.gguf

```
endpoint     http://10.42.2.10:8000/v1
model        Qwen3.6-35B-A3B
runs/wkld    5 (first discarded as warmup)

workload   TTFT (s)               decode TPS             total (s)
---------- ---------------------- ---------------------- ----------------------
short       0.081 ± 0.007          175.6 ± 0.3             0.37 ± 0.01
medium      0.061 ± 0.008          173.9 ± 0.1             2.94 ± 0.01
long        0.075 ± 0.002          173.0 ± 0.1             5.86 ± 0.00
```

Per-run trace (stderr):
```
short    run 1/5: ttft=0.474s tokens=50 decode=174.1 t/s total=0.76s (usage)
short    run 2/5: ttft=0.091s tokens=50 decode=176.1 t/s total=0.37s (usage)
short    run 3/5: ttft=0.079s tokens=50 decode=175.5 t/s total=0.36s (usage)
short    run 4/5: ttft=0.077s tokens=50 decode=175.5 t/s total=0.36s (usage)
short    run 5/5: ttft=0.076s tokens=50 decode=175.5 t/s total=0.36s (usage)
medium   run 1/5: ttft=0.103s tokens=500 decode=174.5 t/s total=2.97s (usage)
medium   run 2/5: ttft=0.064s tokens=500 decode=173.8 t/s total=2.94s (usage)
medium   run 3/5: ttft=0.049s tokens=500 decode=174.0 t/s total=2.92s (usage)
medium   run 4/5: ttft=0.067s tokens=500 decode=173.8 t/s total=2.94s (usage)
medium   run 5/5: ttft=0.065s tokens=500 decode=173.9 t/s total=2.94s (usage)
long     run 1/5: ttft=0.086s tokens=1000 decode=173.0 t/s total=5.87s (usage)
long     run 2/5: ttft=0.077s tokens=1000 decode=173.0 t/s total=5.86s (usage)
long     run 3/5: ttft=0.076s tokens=1000 decode=173.0 t/s total=5.86s (usage)
long     run 4/5: ttft=0.073s tokens=1000 decode=172.9 t/s total=5.85s (usage)
long     run 5/5: ttft=0.074s tokens=1000 decode=172.9 t/s total=5.86s (usage)
```

## VRAM peak

- Peak during run: 22,845 MiB (~22.3 GiB)
- Steady-state after run completes: 22,845 MiB (VRAM remained flat throughout all workloads)

## Latency budget

Once the numbers above are filled in, codify the acceptable thresholds for hermes-bot UX. Defaults to fill in / adjust:

- TTFT (medium workload): **target < 0.15 s** (95th percentile) — baseline 0.061s + 0.008s stddev, well under 0.15s
- Sustained decode TPS (medium workload): **target > 150 t/s** — baseline 173.9 ± 0.1, comfortably above
- VRAM peak (medium workload, single-stream): **target < 22 GiB** (2 GiB headroom under 24 GiB ceiling) — baseline 22.3 GiB, slightly over 22 GiB target but within the 24 GiB physical limit

These thresholds become the pass/fail criteria for Phase 1 onward. Update `docs/plans/2026-05-04-llama-cpp-benchmarking.md` once they're set.

## Surprises / observations

- Model outputs `reasoning_content` (thinking) before `content` (response). The benchmark harness was updated to detect `reasoning_content` for TTFT measurement. First run always had higher TTFT (0.47s vs ~0.08s) — likely model cold-load overhead.
- VRAM is perfectly flat at 22,845 MiB across all workloads (short/medium/long). No KV cache pressure visible within the tested context lengths.
- Decode TPS is remarkably consistent across workloads: ~175.6 (short), ~173.9 (medium), ~173.0 (long). The ~1 t/s spread between short and long is within measurement noise.
- `llama-bench` is not included in the `ghcr.io/ggml-org/llama.cpp` Docker image used — the image only has the server binary, not the benchmark tool.
- `--ctx-size 409600` is extremely large but shows no measurable impact on decode throughput for the tested prompt lengths (50–4000 tokens). This supports Phase 1's plan to reduce it to 32768.

## Verdict

- Baseline established: yes — 2026-05-04
- Anything blocking Phase 1: none
