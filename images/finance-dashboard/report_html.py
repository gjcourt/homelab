#!/usr/bin/env python3
"""Render the portfolio as a self-contained HTML dashboard (no JS, works offline).

  python3 report_html.py [--file positions.yaml] [--live] [--out report.html] [--open]
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

import yaml

from portfolio import value_position  # reuse the pricing logic


def usd(x: float) -> str:
    return f"${x:,.0f}"


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


SLEEVE_COLOR = {"equity": "#2f6fed", "fixed_income": "#16a34a", "cash": "#d97706"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(Path(__file__).parent / "positions.yaml"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", default=str(Path(__file__).parent / "report.html"))
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    data = yaml.safe_load(Path(args.file).read_text())
    ips, positions = data["ips"], data["positions"]
    liabilities = data.get("liabilities", [])
    for p in positions:
        p["_value"], _ = value_position(p, args.live)

    def by(b):
        return [p for p in positions if p.get("bucket") == b]

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

    g = investable / ips["fi_target_gross_usd"]
    m = investable / ips["fi_target_mortgage_adj_usd"]
    cash = sleeves["cash"]
    deploy = cash - ips["cash_reserve_usd"]

    # ---- HTML ----
    def card(label, value, sub=""):
        return (f'<div class="card"><div class="label">{label}</div>'
                f'<div class="value">{value}</div><div class="sub">{sub}</div></div>')

    def progress(label, frac, target_label):
        f = max(0.0, min(1.0, frac))
        return (f'<div class="prow"><div class="plabel">{label}</div>'
                f'<div class="ptrack"><div class="pfill" style="width:{f*100:.1f}%"></div></div>'
                f'<div class="pnum">{pct(frac)} <span class="muted">of {target_label}</span></div></div>')

    # allocation rows
    alloc_rows = ""
    for s in ("equity", "fixed_income", "cash"):
        a = sleeves[s] / liquid_total if liquid_total else 0
        t = ips["target_allocation"][s]
        drift = a - t
        off = abs(drift) > 0.05
        color = SLEEVE_COLOR[s]
        marker = f'<div class="tmark" style="left:{t*100:.1f}%"></div>'
        bar = (f'<div class="atrack">{marker}'
               f'<div class="afill" style="width:{a*100:.1f}%;background:{color}"></div></div>')
        cls = "drift off" if off else "drift"
        alloc_rows += (f'<tr><td>{s.replace("_"," ").title()}</td>'
                       f'<td class="bar">{bar}</td>'
                       f'<td class="num">{pct(a)}</td><td class="num muted">{pct(t)}</td>'
                       f'<td class="num {cls}">{drift*100:+.1f}</td>'
                       f'<td class="num">{usd(sleeves[s])}</td></tr>')

    # concentration
    conc = ""
    single = {}
    for p in liquid:
        if p.get("single_name") and p["asset_class"] != "private_equity":
            single[p["single_name"]] = single.get(p["single_name"], 0.0) + p["_value"]
    cap = ips["concentration"]["public_single_name_max_pct"]
    for name, v in sorted(single.items(), key=lambda kv: -kv[1]):
        share = v / liquid_total if liquid_total else 0
        ok = share <= cap
        conc += (f'<tr><td>{name}</td><td class="num">{usd(v)}</td>'
                 f'<td class="num">{pct(share)}</td><td class="num muted">{pct(cap)}</td>'
                 f'<td class="{"ok" if ok else "off"}">{"ok" if ok else "BREACH"}</td></tr>')
    illiq = sum(p["_value"] for p in by("illiquid_private"))
    icap = ips["concentration"]["illiquid_private_max_pct"]
    ishare = illiq / investable if investable else 0
    conc += (f'<tr><td>Illiquid private</td><td class="num">{usd(illiq)}</td>'
             f'<td class="num">{pct(ishare)}</td><td class="num muted">{pct(icap)}</td>'
             f'<td class="{"ok" if ishare<=icap else "off"}">{"ok" if ishare<=icap else "BREACH"}</td></tr>')

    # holdings
    BUCKET_LABEL = {"liquid": "Liquid", "education": "Education (529)",
                    "illiquid_private": "Illiquid private", "real_estate": "Real estate"}
    holdings = ""
    for b in ("liquid", "education", "illiquid_private", "real_estate"):
        rows = sorted(by(b), key=lambda x: -x["_value"])
        if not rows:
            continue
        sub = sum(p["_value"] for p in rows)
        holdings += f'<tr class="grp"><td colspan="4">{BUCKET_LABEL[b]}</td><td class="num">{usd(sub)}</td></tr>'
        for p in rows:
            holdings += (f'<tr><td>{html.escape(p["name"])}</td>'
                         f'<td class="muted">{p.get("institution","")}</td>'
                         f'<td class="muted">{p["asset_class"].replace("_"," ")}</td>'
                         f'<td class="num muted">{pct(p["_value"]/net_worth) if net_worth else ""}</td>'
                         f'<td class="num">{usd(p["_value"])}</td></tr>')
    liab_rows = "".join(
        f'<tr><td>{html.escape(l["name"])}</td><td class="muted"></td><td class="muted"></td>'
        f'<td class="num muted"></td><td class="num neg">−{usd(l["balance_usd"])}</td></tr>'
        for l in liabilities)

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Balance Sheet — {html.escape(data['meta']['owner'])}</title>
<style>
 :root {{ --bg:#0f1419; --panel:#171c24; --line:#262d38; --txt:#e6e9ef; --muted:#8b97a8; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; background:var(--bg); color:var(--txt);
   font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }}
 .wrap {{ max-width:980px; margin:0 auto; padding:28px 20px 60px; }}
 h1 {{ font-size:20px; margin:0 0 2px; }} .date {{ color:var(--muted); font-size:13px; }}
 .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:22px 0; }}
 .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
 .card .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
 .card .value {{ font-size:22px; font-weight:650; margin-top:4px; }}
 .card .sub {{ color:var(--muted); font-size:12px; margin-top:2px; }}
 h2 {{ font-size:14px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
   border-bottom:1px solid var(--line); padding-bottom:8px; margin:30px 0 14px; }}
 table {{ width:100%; border-collapse:collapse; }}
 td,th {{ padding:7px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:middle; }}
 .num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
 .muted {{ color:var(--muted); }} .neg {{ color:#f87171; }}
 .grp td {{ font-weight:650; background:#11161d; color:var(--txt); border-bottom:1px solid var(--line); }}
 .ok {{ color:#16a34a; }} .off {{ color:#f87171; font-weight:600; }}
 .drift.off {{ color:#f87171; }} .drift {{ color:#16a34a; }}
 .bar {{ width:42%; }}
 .atrack {{ position:relative; height:14px; background:#0d1117; border-radius:7px; overflow:hidden; }}
 .afill {{ height:100%; border-radius:7px; }}
 .tmark {{ position:absolute; top:-3px; width:2px; height:20px; background:#e6e9ef; z-index:2; }}
 .prow {{ display:grid; grid-template-columns:160px 1fr 200px; align-items:center; gap:14px; margin:9px 0; }}
 .ptrack {{ height:12px; background:#0d1117; border-radius:6px; overflow:hidden; }}
 .pfill {{ height:100%; background:linear-gradient(90deg,#2f6fed,#16a34a); }}
 .pnum {{ font-variant-numeric:tabular-nums; }}
 .foot {{ color:var(--muted); font-size:12px; margin-top:30px; }}
</style></head><body><div class="wrap">

<h1>Balance Sheet — {html.escape(data['meta']['owner'])}</h1>
<div class="date">As of {data['meta']['as_of']} · reconciled to statements</div>

<div class="cards">
 {card("Net Worth", usd(net_worth))}
 {card("Investable", usd(investable), "ex-home")}
 {card("FI (mtg-adj)", pct(m), f"{pct(g)} of gross")}
 {card("Cash to deploy", usd(deploy), "→ bonds")}
</div>

<h2>Financial Independence</h2>
{progress("vs gross "+usd(ips['fi_target_gross_usd']), g, "target")}
{progress("vs mortgage-adj "+usd(ips['fi_target_mortgage_adj_usd']), m, "target")}

<h2>Liquid Allocation vs IPS &nbsp;(target {ips['target_allocation']['equity']*100:.0f} / {ips['target_allocation']['fixed_income']*100:.0f} / {ips['target_allocation']['cash']*100:.0f}) · white tick = target</h2>
<table>
 <tr><th>Sleeve</th><th></th><th class="num">Actual</th><th class="num">Target</th><th class="num">Drift</th><th class="num">Value</th></tr>
 {alloc_rows}
</table>

<h2>Concentration</h2>
<table>
 <tr><th>Position</th><th class="num">Value</th><th class="num">Share</th><th class="num">Cap</th><th></th></tr>
 {conc}
</table>

<h2>Holdings</h2>
<table>
 <tr><th>Holding</th><th>Institution</th><th>Class</th><th class="num">% NW</th><th class="num">Value</th></tr>
 {holdings}
 <tr class="grp"><td colspan="4">Liabilities</td><td class="num neg">−{usd(total_debt)}</td></tr>
 {liab_rows}
 <tr class="grp"><td colspan="4">Net Worth</td><td class="num">{usd(net_worth)}</td></tr>
</table>

<div class="foot">Generated by portfolio/report_html.py · not financial advice · {("LIVE prices" if args.live else "static prices")}</div>
</div></body></html>"""

    Path(args.out).write_text(doc)
    print(f"wrote {args.out}")
    if args.open:
        import subprocess
        subprocess.run(["open", args.out], check=False)


if __name__ == "__main__":
    main()
