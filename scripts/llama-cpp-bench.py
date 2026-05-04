#!/usr/bin/env python3
"""End-to-end benchmark harness for the homelab llama.cpp inference endpoint.

Sends a fixed prompt corpus through OpenAI-compat `chat/completions` with
SSE streaming and measures, per workload:

    - time-to-first-token (TTFT, seconds)
    - sustained decode rate (tokens/sec, computed from llama.cpp's
      `usage.completion_tokens` divided by elapsed time after TTFT)
    - end-to-end wall time (seconds)

Reports mean ± stddev across N runs (default 5), discards the first run as
warmup. Also prints raw per-run JSONL to stderr (for archiving).

Designed for the systematic benchmarking plan documented in
docs/plans/2026-05-04-llama-cpp-benchmarking.md.

USAGE

    # Run from anywhere with network reach to the endpoint
    python3 scripts/llama-cpp-bench.py

    # Override defaults
    BASE_URL=http://10.42.2.10:8000/v1 \\
    MODEL=Qwen3.6-35B-A3B \\
    RUNS=5 \\
    python3 scripts/llama-cpp-bench.py

    # Just one workload
    python3 scripts/llama-cpp-bench.py --workload short

    # Archive raw measurements alongside the human-readable summary
    python3 scripts/llama-cpp-bench.py --jsonl out.jsonl

DEFAULTS

    BASE_URL  http://localhost:8000/v1   (override for off-host runs)
    MODEL     Qwen3.6-35B-A3B
    RUNS      5

DEPENDENCIES

    Python 3.9+ standard library only — no pip install.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

# Three fixed workloads, sized per the plan doc:
#   short    ~50 tokens prompt, ~50 tokens response — TTFT-sensitive
#   medium   ~500 tokens prompt, ~500 tokens response — sustained decode
#   long     ~4000 tokens prompt, ~1000 tokens response — prefill + KV
#
# Prompts are intentionally deterministic (no jinja, no current-date references)
# so re-runs at different times produce comparable numbers.
WORKLOADS: dict[str, tuple[str, int]] = {
    "short": (
        "What is the capital of France? Reply in one short sentence.",
        50,
    ),
    "medium": (
        "Explain how a Bloom filter works, including: (1) what problem it "
        "solves and why hash tables alone are not enough for that problem, "
        "(2) the mechanics of insertion and membership testing using k hash "
        "functions and an m-bit array, (3) the false-positive rate as a "
        "function of n, m, and k, and (4) two real-world systems that use "
        "Bloom filters and what they use them for. Aim for a clear, "
        "intermediate-level explanation suitable for a working software "
        "engineer who has not implemented one before. Be concrete with "
        "numbers where it helps.",
        500,
    ),
    "long": (
        # ~4000-token prompt: a self-describing exercise that doesn't depend
        # on external context. Repeated paragraphs are intentional padding so
        # the prefill phase actually exercises long-context attention.
        (
            "You are reviewing a hypothetical Go service that handles "
            "high-throughput webhook delivery for a multi-tenant SaaS. The "
            "service has the following characteristics, all of which I will "
            "now describe in detail. After this description, your task is to "
            "produce a written code review covering: architecture, "
            "reliability, observability, security, and operational concerns. "
            "Be specific and propose concrete improvements where possible.\n\n"
        )
        + ((
            "Architecture: the service is a single Go binary deployed as a "
            "horizontally-scaled Kubernetes Deployment behind an internal "
            "load balancer. It receives webhook delivery jobs from a Kafka "
            "topic partitioned by tenant id, attempts HTTP POST to the "
            "tenant's configured endpoint with exponential backoff and a "
            "5-minute total deadline, then writes the outcome (delivered, "
            "failed, dropped) to a separate result topic. Failed deliveries "
            "are retried up to three times with 30s, 5m, and 30m backoff "
            "respectively. State for the in-flight retry queue lives in "
            "Redis with a 24-hour TTL and is keyed by job id. The HTTP "
            "client uses a default Go transport with MaxIdleConnsPerHost "
            "set to 100. Each webhook target has a per-host rate limit of "
            "50 requests per second enforced by a token-bucket limiter "
            "shared across replicas via Redis Lua scripts. Logs go to "
            "stdout in JSON format and are scraped by a sidecar; metrics "
            "are exposed on /metrics in Prometheus format. The service "
            "runs as non-root with a read-only root filesystem and a "
            "small set of capabilities dropped. Authentication to tenant "
            "endpoints uses HMAC signing of the request body with a "
            "per-tenant secret stored in a Kubernetes Secret. There is no "
            "circuit breaker; transient outages are handled by the retry "
            "schedule alone. The service is on Go 1.21 and has 12 weeks "
            "of production traffic.\n\n"
        ) * 8)
        + (
            "Now produce the code review. Aim for ~1000 words. Use clear "
            "section headings. Where you propose a change, briefly explain "
            "the trade-off and what you would measure to validate the "
            "change had the intended effect."
        ),
        1000,
    ),
}


def run_one(base_url: str, model: str, prompt: str, max_tokens: int, timeout_s: int = 600) -> dict:
    """Issue one streaming chat-completions request, return per-run metrics."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.0,
    }).encode()

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )

    t0 = time.perf_counter()
    ttft: float | None = None
    completion_tokens: int | None = None
    chunks_seen = 0

    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue

            # First content chunk → TTFT (check both 'content' and 'reasoning_content'
            # since Qwen3.6 models output thinking via reasoning_content first)
            choices = obj.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                has_content = bool(delta.get("content")) or bool(delta.get("reasoning_content"))
                if has_content and ttft is None:
                    ttft = time.perf_counter() - t0
                if has_content:
                    chunks_seen += 1

            # Final usage block (when stream_options.include_usage is honored)
            usage = obj.get("usage")
            if usage and usage.get("completion_tokens") is not None:
                completion_tokens = usage["completion_tokens"]

    total = time.perf_counter() - t0

    # Fall back to chunk count if the server didn't emit usage
    tokens = completion_tokens if completion_tokens is not None else chunks_seen
    decode_window = total - (ttft or 0.0)
    decode_tps = (tokens / decode_window) if (decode_window > 0 and tokens > 0) else 0.0

    return {
        "ttft_s": ttft,
        "total_s": total,
        "tokens": tokens,
        "decode_tps": decode_tps,
        "tokens_source": "usage" if completion_tokens is not None else "chunks_fallback",
    }


def stats(values: list[float]) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], 0.0)
    return (statistics.mean(values), statistics.stdev(values))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000/v1"))
    ap.add_argument("--model", default=os.environ.get("MODEL", "Qwen3.6-35B-A3B"))
    ap.add_argument("--runs", type=int, default=int(os.environ.get("RUNS", "5")),
                    help="runs per workload (the first is discarded as warmup)")
    ap.add_argument("--workload", choices=list(WORKLOADS.keys()) + ["all"], default="all",
                    help="run a single workload, or 'all'")
    ap.add_argument("--jsonl", default=None, help="archive raw per-run measurements to this file")
    ap.add_argument("--timeout", type=int, default=600, help="per-request timeout (seconds)")
    args = ap.parse_args()

    workloads = WORKLOADS if args.workload == "all" else {args.workload: WORKLOADS[args.workload]}
    jsonl = open(args.jsonl, "a") if args.jsonl else None

    print(f"endpoint     {args.base_url}")
    print(f"model        {args.model}")
    print(f"runs/wkld    {args.runs} (first discarded as warmup)")
    print()
    print(f"{'workload':<10} {'TTFT (s)':<22} {'decode TPS':<22} {'total (s)':<22}")
    print(f"{'-'*10} {'-'*22} {'-'*22} {'-'*22}")

    overall_ok = True
    for name, (prompt, max_tokens) in workloads.items():
        runs: list[dict] = []
        try:
            for i in range(args.runs):
                r = run_one(args.base_url, args.model, prompt, max_tokens, args.timeout)
                r["run"] = i
                r["workload"] = name
                runs.append(r)
                if jsonl:
                    jsonl.write(json.dumps(r) + "\n")
                    jsonl.flush()
                ttft_str = f"{r['ttft_s']:.3f}" if r['ttft_s'] is not None else "N/A"
                print(f"  {name:<8} run {i+1}/{args.runs}: ttft={ttft_str}s "
                      f"tokens={r['tokens']} decode={r['decode_tps']:.1f} t/s total={r['total_s']:.2f}s "
                      f"({r['tokens_source']})", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  {name:<8} ERROR: {e}", file=sys.stderr)
            overall_ok = False
            continue

        post_warm = runs[1:] if len(runs) > 1 else runs
        ttfts = [r["ttft_s"] for r in post_warm if r["ttft_s"] is not None]
        tpsps = [r["decode_tps"] for r in post_warm if r["decode_tps"] > 0]
        totals = [r["total_s"] for r in post_warm]

        m_ttft, sd_ttft = stats(ttfts)
        m_tps, sd_tps = stats(tpsps)
        m_tot, sd_tot = stats(totals)

        print(f"{name:<10} {m_ttft:>6.3f} ± {sd_ttft:<10.3f}    "
              f"{m_tps:>6.1f} ± {sd_tps:<10.1f}    "
              f"{m_tot:>6.2f} ± {sd_tot:<10.2f}")

    if jsonl:
        jsonl.close()
        print(f"\nraw runs archived to {args.jsonl}")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
