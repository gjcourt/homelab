---
status: Partial
last_modified: 2026-06-10
---

# hestia memory bandwidth — 6-DIMM baseline

Baseline run from the [memory benchmark harness](../../hosts/hestia/bench/memory/README.md)
on hestia (ASRock Rack SIENAD8-2L2T, EPYC 8324P Siena, 6-channel DDR5). This
captures the **6-DIMM, 1DPC × 6 channels** configuration only; the 8-DIMM
comparison is pending a physical DIMM swap (see
[plan](../plans/2026-05-15-hestia-memory-benchmark.md)).

## 6-DIMM baseline (1DPC × 6 channels, full DDR5-4800)

Run `6dimm-2026-05-16T04-00-00Z`:

| Metric | Result |
|---|---|
| STREAM Triad | 131.2 GB/s |
| Intel MLC all-reads | 186.1 GB/s |
| Idle latency | ~115 ns |

Raw JSONL: `/home/truenas_admin/bench/memory/6dimm-2026-05-16T04-00-00Z.jsonl`
on hestia (mirrored locally at `/tmp/hestia-bench/` at capture time).

## Pending

- **8-DIMM run** — requires adding 2 DIMMs to the 2DPC-eligible slots, which
  forces two channels into 2DPC and derates the bus. The point of the
  benchmark is to measure that derate against the 4800 baseline above. Once
  captured, run `aggregate.py` to produce the side-by-side Δ% comparison.
