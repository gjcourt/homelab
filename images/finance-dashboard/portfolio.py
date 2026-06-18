#!/usr/bin/env python3
"""Phase-0 portfolio engine: marks positions to market and reports actual-vs-IPS.

Hand-rolled, credential-free. Reads positions.yaml (units/value + IPS spec),
values every position, and prints:
  - net worth & investable, FI progress
  - liquid allocation vs IPS target (the cash-drag / no-bonds finding falls out)
  - concentration checks (public single-name, illiquid-private caps)
  - cash vs reserve  ->  how much to deploy

Phase 1 adds source adapters (Coinbase API / OFX / CSV / Shareworks export) that
populate `units` and cash balances; this engine stays the same.

Usage:
  python3 portfolio.py [--file positions.yaml] [--live]
  --live  fetch public spot quotes (Coinbase for crypto, Stooq for stocks) and
          mark any position that has both `units` and `quote`.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pip install pyyaml  (see requirements.txt)")


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
def fetch_quote(symbol: str) -> float | None:
    """Best-effort public spot price, no auth. Returns None on any failure."""
    try:
        if symbol.endswith("-USD"):  # crypto via Coinbase public spot
            url = f"https://api.coinbase.com/v2/prices/{symbol}/spot"
            with urllib.request.urlopen(url, timeout=8) as r:
                return float(json.load(r)["data"]["amount"])
        else:  # stock via Stooq CSV (e.g. COIN -> coin.us)
            url = f"https://stooq.com/q/l/?s={symbol.lower()}.us&f=sd2t2ohlcv&h&e=csv"
            with urllib.request.urlopen(url, timeout=8) as r:
                rows = r.read().decode().strip().splitlines()
            close = rows[1].split(",")[6]
            return float(close) if close not in ("N/D", "") else None
    except Exception:
        return None


def value_position(pos: dict, live: bool) -> tuple[float, str]:
    """Return (usd_value, price_note). Prefers units*price; falls back to value_usd."""
    units = pos.get("units")
    if units is not None:
        price = pos.get("price", 0.0)
        note = "static"
        quote = pos.get("quote")
        if live and quote:
            q = fetch_quote(quote)
            if q is not None:
                price, note = q, f"live {quote}"
        return units * price, note
    return float(pos.get("value_usd", 0.0)), "balance"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def usd(x: float) -> str:
    return f"${x:,.0f}"


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def bar(frac: float, width: int = 20) -> str:
    frac = max(0.0, min(1.0, frac))
    fill = int(round(frac * width))
    return "█" * fill + "·" * (width - fill)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(Path(__file__).parent / "positions.yaml"))
    ap.add_argument("--live", action="store_true", help="fetch live public quotes")
    args = ap.parse_args()

    data = yaml.safe_load(Path(args.file).read_text())
    ips = data["ips"]
    positions = data["positions"]
    liabilities = data.get("liabilities", [])

    # value every position
    for p in positions:
        p["_value"], p["_pricing"] = value_position(p, args.live)

    def by(bucket: str) -> list[dict]:
        return [p for p in positions if p.get("bucket") == bucket]

    total_assets = sum(p["_value"] for p in positions)
    total_debt = sum(l["balance_usd"] for l in liabilities)
    net_worth = total_assets - total_debt

    real_estate = sum(p["_value"] for p in by("real_estate"))
    investable = total_assets - real_estate

    liquid = by("liquid")
    liquid_total = sum(p["_value"] for p in liquid)
    sleeves = {"equity": 0.0, "fixed_income": 0.0, "cash": 0.0}
    for p in liquid:
        sleeves[p["asset_class"]] = sleeves.get(p["asset_class"], 0.0) + p["_value"]

    print()
    print("=" * 64)
    print(f"  PORTFOLIO REPORT — {data['meta']['owner']}   as of {data['meta']['as_of']}"
          + ("   [LIVE]" if args.live else ""))
    print("=" * 64)

    # --- Net worth ---------------------------------------------------------
    print("\n NET WORTH")
    print(f"   Assets        {usd(total_assets):>16}")
    print(f"   Mortgage      {usd(-total_debt):>16}")
    print(f"   ----------------------------")
    print(f"   Net worth     {usd(net_worth):>16}")
    print(f"   Investable (ex-home) {usd(investable):>9}")

    # --- FI progress -------------------------------------------------------
    g = investable / ips["fi_target_gross_usd"]
    m = investable / ips["fi_target_mortgage_adj_usd"]
    print("\n FINANCIAL INDEPENDENCE")
    print(f"   vs gross  {usd(ips['fi_target_gross_usd'])}:  [{bar(g)}] {pct(g)}")
    print(f"   vs mtg-adj {usd(ips['fi_target_mortgage_adj_usd'])}: [{bar(m)}] {pct(m)}")

    # --- Allocation vs IPS -------------------------------------------------
    ta = ips["target_allocation"]
    print(f"\n LIQUID ALLOCATION vs IPS  (target "
          f"{ta['equity']*100:.0f} / {ta['fixed_income']*100:.0f} / {ta['cash']*100:.0f})")
    print(f"   {'sleeve':<14}{'actual':>10}{'target':>9}{'drift':>9}")
    for s in ("equity", "fixed_income", "cash"):
        a = sleeves[s] / liquid_total if liquid_total else 0
        t = ips["target_allocation"][s]
        flag = "  <-- off" if abs(a - t) > 0.05 else ""
        print(f"   {s:<14}{pct(a):>10}{pct(t):>9}{(a-t)*100:>+8.1f}{flag}")
    print(f"   liquid total: {usd(liquid_total)}")

    # --- Cash vs reserve ---------------------------------------------------
    cash = sleeves["cash"]
    deployable = cash - ips["cash_reserve_usd"]
    print("\n CASH")
    print(f"   Cash on hand  {usd(cash)}")
    print(f"   Reserve       {usd(ips['cash_reserve_usd'])}")
    print(f"   -> DEPLOY     {usd(deployable)}  (tranche into 65/25)")

    # --- Concentration -----------------------------------------------------
    print("\n CONCENTRATION")
    single = {}
    for p in liquid:
        if p.get("single_name") and p["asset_class"] != "private_equity":
            single[p["single_name"]] = single.get(p["single_name"], 0.0) + p["_value"]
    cap = ips["concentration"]["public_single_name_max_pct"]
    for name, v in sorted(single.items(), key=lambda kv: -kv[1]):
        share = v / liquid_total if liquid_total else 0
        flag = "  BREACH" if share > cap else "ok"
        print(f"   {name:<10}{usd(v):>12}  {pct(share):>6} of liquid (cap {pct(cap)})  {flag}")

    illiq = sum(p["_value"] for p in by("illiquid_private"))
    icap = ips["concentration"]["illiquid_private_max_pct"]
    ishare = illiq / investable if investable else 0
    iflag = "  BREACH" if ishare > icap else "ok"
    print(f"   illiquid private {usd(illiq):>9}  {pct(ishare):>6} of investable (cap {pct(icap)}){iflag}")

    # --- Holdings ----------------------------------------------------------
    print("\n HOLDINGS")
    for b in ("liquid", "education", "illiquid_private", "real_estate"):
        rows = by(b)
        if not rows:
            continue
        print(f"   [{b}]")
        for p in sorted(rows, key=lambda x: -x["_value"]):
            print(f"     {p['name']:<34}{usd(p['_value']):>13}  ({p['_pricing']})")
    print()


if __name__ == "__main__":
    main()
