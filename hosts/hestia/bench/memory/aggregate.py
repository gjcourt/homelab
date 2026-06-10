#!/usr/bin/env python3
"""Aggregate two hestia memory-benchmark JSONL runs into a comparison writeup.

Consumes the JSONL output produced by the in-repo memory benchmark harness
(STREAM + Intel MLC) for two configurations of the same host — typically a
"6dimm" (1 DIMM-per-channel, full speed) run and an "8dimm" (mixed 1DPC/2DPC,
derated) run on the EPYC 8004 Siena server — and emits a markdown writeup
suitable to commit under `docs/research/`.

The headline number is STREAM Triad: median over the 5 runs per config, with
stddev as the spread, and a signed delta in MB/s and percent. MLC idle
latency, max-bandwidth-by-pattern, and the loaded-latency curve are reported
side-by-side. Missing sub-benchmarks degrade gracefully to `n/a`.

USAGE

    python3 aggregate.py <jsonl-a> <jsonl-b>            # markdown to stdout
    python3 aggregate.py <jsonl-a> <jsonl-b> --out FILE # write to FILE

The first JSONL argument is "A", the second is "B"; deltas are computed as
(B - A) so a negative percent means B is slower than A. Labels for each
column come from the `meta` event embedded in each file.

DEPENDENCIES

    Python 3.9+ standard library only — json, statistics, argparse, pathlib,
    datetime. No pandas, no numpy.

Designed to pair with the harness in `hosts/hestia/bench/memory/` per the
plan in `docs/plans/2026-05-15-hestia-memory-benchmark.md`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Kernel display order in the STREAM table. Matches McCalpin's canonical
# Copy / Scale / Add / Triad order so the table reads top-to-bottom in the
# same order the benchmark prints them.
STREAM_KERNELS = ["Copy", "Scale", "Add", "Triad"]

# MLC max-bandwidth pattern keys (as emitted by the harness) and their
# human-readable labels for the comparison table.
MLC_PATTERNS = [
    ("all_reads", "All reads"),
    ("3_1_reads_writes", "3:1 reads/writes"),
    ("2_1_reads_writes", "2:1 reads/writes"),
    ("1_1_reads_writes", "1:1 reads/writes"),
    ("stream_triad_like", "Stream triad-like"),
]


def load_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file into a list of event dicts. Tolerates blank lines."""
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{lineno}: invalid JSON: {e}") from e
    return events


def find_meta(events: list[dict]) -> dict:
    for ev in events:
        if ev.get("event") == "meta":
            return ev
    return {}


def find_done(events: list[dict]) -> dict:
    for ev in events:
        if ev.get("event") == "done":
            return ev
    return {}


def stream_runs_by_kernel(events: list[dict]) -> dict[str, list[float]]:
    """Return rate (MB/s) lists keyed by kernel name."""
    out: dict[str, list[float]] = {k: [] for k in STREAM_KERNELS}
    for ev in events:
        if ev.get("event") != "stream_run":
            continue
        kernel = ev.get("kernel")
        rate = ev.get("rate_mb_per_s")
        if kernel in out and isinstance(rate, (int, float)):
            out[kernel].append(float(rate))
    return out


def mlc_idle(events: list[dict]) -> Optional[dict]:
    for ev in events:
        if ev.get("event") == "mlc_idle_latency":
            return ev
    return None


def mlc_max_bandwidth(events: list[dict]) -> dict[str, float]:
    """Pattern → bandwidth_mb_per_s."""
    out: dict[str, float] = {}
    for ev in events:
        if ev.get("event") != "mlc_max_bandwidth":
            continue
        pattern = ev.get("pattern")
        bw = ev.get("bandwidth_mb_per_s")
        if isinstance(pattern, str) and isinstance(bw, (int, float)):
            out[pattern] = float(bw)
    return out


def mlc_loaded_curve(events: list[dict]) -> list[dict]:
    """List of {inject_delay, latency_ns, bandwidth_mb_per_s} in file order."""
    curve = [ev for ev in events if ev.get("event") == "mlc_loaded_latency"]
    # Sort by inject_delay if present so adjacent rows are comparable.
    curve.sort(key=lambda e: e.get("inject_delay", 0))
    return curve


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def fmt_mb(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.0f}"


def fmt_mb_pm(median: Optional[float], stdev: Optional[float]) -> str:
    """Format a median ± stddev MB/s pair."""
    if median is None:
        return "n/a"
    if stdev is None:
        return f"{median:,.0f}"
    return f"{median:,.0f} ± {stdev:,.0f}"


def fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ("" if value == 0 else "")
    # negatives already carry their own sign; positives get an explicit +
    if value > 0:
        return f"+{value:.1f}%"
    return f"{value:.1f}%"


def fmt_delta_mb(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if value > 0:
        return f"+{value:,.0f}"
    return f"{value:,.0f}"


def fmt_ns(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f} ns"


def fmt_delta_ns(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if value > 0:
        return f"+{value:.1f}"
    return f"{value:.1f}"


def signed_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """(b - a) / a * 100. None if either side missing or a == 0."""
    if a is None or b is None or a == 0:
        return None
    return (b - a) / a * 100.0


def signed_diff(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return b - a


def median_and_stdev(values: list[float]) -> tuple[Optional[float], Optional[float]]:
    if not values:
        return (None, None)
    med = statistics.median(values)
    sd = statistics.stdev(values) if len(values) >= 2 else 0.0
    return (med, sd)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render(path_a: Path, path_b: Path) -> str:
    events_a = load_jsonl(path_a)
    events_b = load_jsonl(path_b)

    meta_a = find_meta(events_a)
    meta_b = find_meta(events_b)
    done_a = find_done(events_a)
    done_b = find_done(events_b)

    label_a = str(meta_a.get("label") or path_a.stem)
    label_b = str(meta_b.get("label") or path_b.stem)

    # Host descriptor — assume A is authoritative for the shared host fields,
    # fall back to B if A is missing them.
    host = meta_a.get("host") or meta_b.get("host") or "unknown"
    cpu = meta_a.get("cpu") or meta_b.get("cpu") or "unknown CPU"
    numa = meta_a.get("numa_nodes", meta_b.get("numa_nodes", "?"))
    cores = meta_a.get("physical_cores", meta_b.get("physical_cores", "?"))

    # Measurement date — pull from meta.ts (ISO 8601), fall back to "unknown".
    ts_raw = meta_a.get("ts") or meta_b.get("ts")
    date_str = "unknown"
    if isinstance(ts_raw, str):
        try:
            # tolerate trailing Z
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        except ValueError:
            date_str = ts_raw

    out: list[str] = []

    out.append(f"# Hestia memory bandwidth — {label_a} vs {label_b}")
    out.append("")
    out.append(f"**Measured:** {date_str}")
    out.append(
        f"**Host:** {host} ({cpu}, {numa} NUMA "
        f"node{'s' if isinstance(numa, int) and numa != 1 else ''}, "
        f"{cores} physical cores)"
    )
    out.append("**Tools:** STREAM (McCalpin, NTIMES=20), Intel MLC v3.12")
    out.append("")

    # ---------------- Headline ----------------
    runs_a = stream_runs_by_kernel(events_a)
    runs_b = stream_runs_by_kernel(events_b)

    triad_a_med, triad_a_sd = median_and_stdev(runs_a["Triad"])
    triad_b_med, triad_b_sd = median_and_stdev(runs_b["Triad"])
    triad_delta_pct = signed_pct(triad_a_med, triad_b_med)

    if triad_delta_pct is None:
        headline_delta = "n/a"
    else:
        # Decide which label is the faster one for the parenthetical
        if triad_delta_pct < 0:
            faster_label = label_a
            unexpected = ""
        elif triad_delta_pct > 0:
            faster_label = label_b
            # If B is the higher-density / typically-derated config, surface
            # that as "unexpected" so the operator notices. We can't know for
            # certain — heuristic: if label_b contains "8" and label_a "6",
            # B-faster is unexpected.
            unexpected = ", unexpected" if ("8" in label_b and "6" in label_a) else ""
        else:
            faster_label = "tie"
            unexpected = ""
        sign = "+" if triad_delta_pct > 0 else ""
        if faster_label == "tie":
            headline_delta = f"Δ = {sign}{triad_delta_pct:.1f}% (tie)"
        else:
            headline_delta = (
                f"Δ = {sign}{triad_delta_pct:.1f}% ({faster_label} faster{unexpected})"
            )

    out.append("## Headline")
    out.append("")
    out.append(
        f"**STREAM Triad: {label_a} = {fmt_mb(triad_a_med)} MB/s · "
        f"{label_b} = {fmt_mb(triad_b_med)} MB/s · {headline_delta}**"
    )
    out.append("")

    # ---------------- STREAM table ----------------
    out.append("## STREAM (sustained bandwidth)")
    out.append("")
    out.append("5 runs per kernel per config. Reported: median ± stddev across runs, in MB/s.")
    out.append("")
    out.append(f"| Kernel | {label_a} | {label_b} | Δ MB/s | Δ % |")
    out.append("|---|---:|---:|---:|---:|")
    for kernel in STREAM_KERNELS:
        a_med, a_sd = median_and_stdev(runs_a[kernel])
        b_med, b_sd = median_and_stdev(runs_b[kernel])
        d_mb = signed_diff(a_med, b_med)
        d_pct = signed_pct(a_med, b_med)
        out.append(
            f"| {kernel:<6} | {fmt_mb_pm(a_med, a_sd)} | {fmt_mb_pm(b_med, b_sd)} "
            f"| {fmt_delta_mb(d_mb)} | {fmt_pct(d_pct)} |"
        )
    out.append("")

    # ---------------- MLC idle latency ----------------
    idle_a = mlc_idle(events_a)
    idle_b = mlc_idle(events_b)

    out.append("## MLC idle latency")
    out.append("")
    out.append(f"| Pattern | {label_a} | {label_b} | Δ ns |")
    out.append("|---|---:|---:|---:|")
    for key, label in [("random_ns", "Random"), ("sequential_ns", "Sequential")]:
        a_val = idle_a.get(key) if idle_a else None
        b_val = idle_b.get(key) if idle_b else None
        a_str = fmt_ns(a_val) if isinstance(a_val, (int, float)) else "n/a"
        b_str = fmt_ns(b_val) if isinstance(b_val, (int, float)) else "n/a"
        d = signed_diff(
            a_val if isinstance(a_val, (int, float)) else None,
            b_val if isinstance(b_val, (int, float)) else None,
        )
        out.append(f"| {label} | {a_str} | {b_str} | {fmt_delta_ns(d)} |")
    out.append("")

    # ---------------- MLC max bandwidth ----------------
    maxbw_a = mlc_max_bandwidth(events_a)
    maxbw_b = mlc_max_bandwidth(events_b)

    out.append("## MLC max bandwidth (different access patterns)")
    out.append("")
    out.append(f"| Pattern | {label_a} | {label_b} | Δ % |")
    out.append("|---|---:|---:|---:|")
    for key, label in MLC_PATTERNS:
        a_val = maxbw_a.get(key)
        b_val = maxbw_b.get(key)
        a_str = f"{fmt_mb(a_val)} MB/s" if a_val is not None else "n/a"
        b_str = f"{fmt_mb(b_val)} MB/s" if b_val is not None else "n/a"
        d_pct = signed_pct(a_val, b_val)
        out.append(f"| {label} | {a_str} | {b_str} | {fmt_pct(d_pct)} |")
    out.append("")

    # ---------------- MLC loaded-latency curve ----------------
    curve_a = mlc_loaded_curve(events_a)
    curve_b = mlc_loaded_curve(events_b)

    out.append("## MLC loaded-latency curve")
    out.append("")
    out.append(
        'Latency (ns) vs offered load (MB/s). The "knee" — where latency '
        "jumps as bandwidth approaches saturation — is the practical ceiling."
    )
    out.append("")

    def render_curve(label: str, curve: list[dict]) -> None:
        out.append(f"{label}:")
        out.append("")
        if not curve:
            out.append("_n/a — MLC loaded-latency data not present in run._")
            out.append("")
            return
        out.append("| Inject delay | Bandwidth | Latency |")
        out.append("|---:|---:|---:|")
        for row in curve:
            delay = row.get("inject_delay")
            bw = row.get("bandwidth_mb_per_s")
            lat = row.get("latency_ns")
            delay_str = f"{delay}" if delay is not None else "n/a"
            bw_str = f"{fmt_mb(bw)} MB/s" if isinstance(bw, (int, float)) else "n/a"
            lat_str = f"{lat:.1f} ns" if isinstance(lat, (int, float)) else "n/a"
            out.append(f"| {delay_str} | {bw_str} | {lat_str} |")
        out.append("")

    render_curve(label_a, curve_a)
    render_curve(label_b, curve_b)

    # ---------------- Run quality ----------------
    # Triad stddev / mean, as a sanity check on variance. Anything > 2% is
    # noise-floor noise and shouldn't be trusted; flag with ⚠.
    def stddev_pct(values: list[float]) -> Optional[float]:
        # Matches median_and_stdev(): a single sample has zero spread.
        # Returning 0.0 rather than None keeps the run-quality table aligned
        # with the STREAM table (which renders "value ± 0.0" for n=1).
        if len(values) < 2:
            return 0.0 if values else None
        m = statistics.mean(values)
        if m == 0:
            return None
        return statistics.stdev(values) / m * 100.0

    sd_a = stddev_pct(runs_a["Triad"])
    sd_b = stddev_pct(runs_b["Triad"])

    def ok_mark(sd: Optional[float]) -> str:
        if sd is None:
            return "n/a"
        return "✓" if sd < 2.0 else "⚠"

    sd_a_str = f"{sd_a:.1f}%" if sd_a is not None else "n/a"
    sd_b_str = f"{sd_b:.1f}%" if sd_b is not None else "n/a"

    # Combined OK marker: ✓ only if both pass; otherwise ⚠.
    both_ok = (
        sd_a is not None and sd_b is not None and sd_a < 2.0 and sd_b < 2.0
    )
    triad_ok_marker = "✓ (<2%)" if both_ok else "⚠"
    if sd_a is None or sd_b is None:
        triad_ok_marker = ok_mark(sd_a if sd_a is not None else sd_b)

    dur_a = done_a.get("duration_s") if done_a else None
    dur_b = done_b.get("duration_s") if done_b else None
    dur_a_str = f"{dur_a:.0f} s" if isinstance(dur_a, (int, float)) else "n/a"
    dur_b_str = f"{dur_b:.0f} s" if isinstance(dur_b, (int, float)) else "n/a"

    out.append("## Run quality")
    out.append("")
    out.append(f"| Metric | {label_a} | {label_b} | OK? |")
    out.append("|---|---:|---:|:-:|")
    out.append(
        f"| STREAM Triad stddev (% of mean) | {sd_a_str} | {sd_b_str} | {triad_ok_marker} |"
    )
    out.append(f"| Total wall time | {dur_a_str} | {dur_b_str} | |")
    out.append("")

    # ---------------- Provenance ----------------
    out.append("## Provenance")
    out.append("")
    out.append(f"- {label_a}: `{path_a}`")
    out.append(f"- {label_b}: `{path_b}`")
    out.append("- Aggregator: `hosts/hestia/bench/memory/aggregate.py`")
    out.append("- Plan: `docs/plans/2026-05-15-hestia-memory-benchmark.md`")
    out.append("")

    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("jsonl_a", type=Path, help='first JSONL run (becomes "A" in output)')
    ap.add_argument("jsonl_b", type=Path, help='second JSONL run (becomes "B" in output)')
    ap.add_argument("--out", type=Path, default=None, help="write markdown to FILE (default: stdout)")
    args = ap.parse_args()

    for p in (args.jsonl_a, args.jsonl_b):
        if not p.exists():
            print(f"error: {p} does not exist", file=sys.stderr)
            return 2

    md = render(args.jsonl_a, args.jsonl_b)

    if args.out is None:
        sys.stdout.write(md)
        if not md.endswith("\n"):
            sys.stdout.write("\n")
    else:
        args.out.write_text(md if md.endswith("\n") else md + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
