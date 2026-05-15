# Hestia memory bandwidth benchmark

Measures memory bandwidth and latency on hestia (ASRock Rack SIENAD8-2L2T, EPYC 8004 / Siena, 6-channel DDR5) under two physical DIMM populations: 6 DIMMs at 1DPC × 6 channels (full JEDEC DDR5-4800) vs. 8 DIMMs with two channels at 2DPC (typically derated to DDR5-3600 or DDR5-4000). Output is a JSONL run log per config plus an aggregated comparison written to `docs/research/`.

## Prerequisites

- Intel MLC tarball — download from <https://www.intel.com/content/www/us/en/developer/articles/tool/intelr-memory-latency-checker.html>, accept the EULA, and place `mlc_v3.11a.tgz` in this directory before `docker build`.
- Docker available on the host.
- Root / `sudo` access (MLC and `numactl` pinning require it).
- vLLM stoppable. Operator owns the recipe; recap:
  ```bash
  midclt call app.stop vllm
  nvidia-smi   # confirm GPUs idle, no PIDs holding VRAM
  ```
- `cpupower` installed (`apt-get install linux-cpupower`) or fallback: write directly to `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`.
- SIENAD8-2L2T board manual on hand for the DIMM-slot map: `~/Downloads/SIENAD8-2L2T.pdf` (slot layout on p. 12-13).

## Build

```bash
docker build -t hestia-memory-bench:1 .
```

The build will fail loudly if `mlc_v3.11a.tgz` is missing from the build context.

## Run

### 1. Stop vLLM

```bash
midclt call app.stop vllm
nvidia-smi                      # both GPUs must show 0 MiB used and no PIDs
```

If vLLM does not stop cleanly within ~60s, see Troubleshooting.

### 2. Apply controls and benchmark

```bash
# Optional: override results location (default lives on the boot pool)
export RESULTS_DIR=/mnt/tank/bench/memory

# Label is whatever the operator wants — convention is the DIMM count
sudo ./run.sh 6dimm
```

`run.sh` handles governor pinning, C-state masking, NUMA setup, container invocation, and JSONL emission. The last line printed is the absolute path to the results file — note it before continuing.

## Swap DIMMs

1. `sudo shutdown -h now`
2. Open the case. Board manual: `~/Downloads/SIENAD8-2L2T.pdf` (slot layout p. 12-13). The 2DPC-eligible slots are labelled in the manual.
3. Move DIMMs:
   - **6 → 8**: add 2 DIMMs to the 2DPC slots.
   - **8 → 6**: remove the 2 DIMMs from the 2DPC slots.
4. Boot to BIOS. Verify all DIMMs detected and note the configured memory speed grade (DDR5-4800 vs. derated).
5. Boot to OS. Re-run:
   ```bash
   sudo ./run.sh 8dimm    # or 6dimm
   ```

## Aggregate

`run.sh` prints the exact JSONL path it produced at the end of each run — copy those into the command below. Run from the repo root so the output path is unambiguous:

```bash
cd ~/src/homelab
python3 hosts/hestia/bench/memory/aggregate.py \
    "${RESULTS_DIR:-/mnt/tank/bench/memory}/6dimm-<ts>.jsonl" \
    "${RESULTS_DIR:-/mnt/tank/bench/memory}/8dimm-<ts>.jsonl" \
    --out docs/research/2026-05-15-hestia-memory-bandwidth.md
```

Filenames are `<label>-<utc-ts>.jsonl` (e.g. `6dimm-2026-05-15T14-23-45Z.jsonl`) — the label is what you passed to `run.sh`, the timestamp is when the run started.

Diff the two JSONLs into a single markdown comparison under `docs/research/`. The aggregate script computes Δ% on STREAM Triad, idle latency delta, and a small loaded-latency table.

## Restore service

```bash
midclt call app.start vllm
# smoke test
curl -sS http://10.42.2.10:8000/v1/models | jq '.data[].id'
```

## Interpreting results

- **STREAM Triad Δ%** — the headline number. Expect 6DIMM to win by the ratio of (4800 / configured 8DIMM speed), minus a few percent for 2DPC overhead.
- **Idle latency delta** — typically a few ns; 8DIMM tends to be slightly higher.
- **Loaded-latency curve** — 8DIMM's "knee" (where latency starts climbing under bandwidth pressure) shifts left, i.e. the bus saturates sooner.
- **Run-to-run stddev** — should be <2%. Higher means controls leaked; see Troubleshooting.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `mlc_v3.11a.tgz not found` at build time | Tarball wasn't placed next to the Dockerfile | Download from Intel, accept EULA, drop in this directory, rebuild. |
| 8DIMM didn't derate the bus | CPU silicon binning or BIOS auto-bump kept the bus at 4800 | Inspect the preflight JSON for `Configured Memory Speed` (from `dmidecode -t memory`). If it really is 4800 at 2DPC, document it — that's the answer. |
| Run-to-run stddev > 2% | C-states or turbo bouncing leaked through | Check `/sys/devices/system/cpu/cpu0/cpuidle/state*/disable` — all non-C0/C1 should be `1`. Retry with `idle=poll` on the kernel cmdline. |
| vLLM won't stop cleanly | Worker stuck on a CUDA op | Grace period 60s, then `midclt call app.kill vllm`. Confirm with `nvidia-smi`. |

## Related

- Plan: [`docs/plans/2026-05-15-hestia-memory-benchmark.md`](../../../../docs/plans/2026-05-15-hestia-memory-benchmark.md)
- Methodology precedent: [`docs/plans/2026-05-04-llama-cpp-benchmarking.md`](../../../../docs/plans/2026-05-04-llama-cpp-benchmarking.md)
- Harness inspiration: [`scripts/llama-cpp-bench.py`](../../../../scripts/llama-cpp-bench.py)
- Board manual: `~/Downloads/SIENAD8-2L2T.pdf`
