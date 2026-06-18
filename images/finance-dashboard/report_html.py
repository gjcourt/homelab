#!/usr/bin/env python3
"""Render the balance sheet page (index.html) of the finance-dashboard site.

Self-contained, no JS. Shared chrome (nav + CSS) comes from webcommon/style.css.

  python3 report_html.py [--file positions.yaml] [--live] [--out index.html] [--open]
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

import yaml

from portfolio import value_position  # reuse the pricing logic
from webcommon import page, usd, pct

SLEEVE_COLOR = {"equity": "#2f6fed", "fixed_income": "#16a34a", "cash": "#d97706"}


def render_html(data: dict, live: bool = False) -> str:
    ips, positions = data["ips"], data["positions"]
    liabilities = data.get("liabilities", [])
    for p in positions:
        p["_value"], _ = value_position(p, live)

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
    deploy = sleeves["cash"] - ips["cash_reserve_usd"]

    def card(label, value, sub=""):
        return (f'<div class=card><div class=label>{label}</div>'
                f'<div class=value>{value}</div><div class=sub>{sub}</div></div>')

    def progress(label, frac, target_label):
        f = max(0.0, min(1.0, frac))
        return (f'<div class=prow><div class=plabel>{label}</div>'
                f'<div class=ptrack><div class=pfill style="width:{f*100:.1f}%"></div></div>'
                f'<div class=pnum>{pct(frac)} <span class=muted>of {target_label}</span></div></div>')

    alloc_rows = ""
    for s in ("equity", "fixed_income", "cash"):
        a = sleeves[s] / liquid_total if liquid_total else 0
        t = ips["target_allocation"][s]
        drift = a - t
        off = abs(drift) > 0.05
        marker = f'<div class=tmark style="left:{t*100:.1f}%"></div>'
        bar = (f'<div class=atrack>{marker}'
               f'<div class=afill style="width:{a*100:.1f}%;background:{SLEEVE_COLOR[s]}"></div></div>')
        cls = "drift off" if off else "drift"
        alloc_rows += (f'<tr><td>{s.replace("_"," ").title()}</td><td class=bar>{bar}</td>'
                       f'<td class=num>{pct(a)}</td><td class="num muted">{pct(t)}</td>'
                       f'<td class="num {cls}">{drift*100:+.1f}</td>'
                       f'<td class=num>{usd(sleeves[s])}</td></tr>')

    conc = ""
    single = {}
    for p in liquid:
        if p.get("single_name") and p["asset_class"] != "private_equity":
            single[p["single_name"]] = single.get(p["single_name"], 0.0) + p["_value"]
    cap = ips["concentration"]["public_single_name_max_pct"]
    for name, v in sorted(single.items(), key=lambda kv: -kv[1]):
        share = v / liquid_total if liquid_total else 0
        ok = share <= cap
        conc += (f'<tr><td>{name}</td><td class=num>{usd(v)}</td>'
                 f'<td class=num>{pct(share)}</td><td class="num muted">{pct(cap)}</td>'
                 f'<td class="{"ok" if ok else "off"}">{"ok" if ok else "BREACH"}</td></tr>')
    illiq = sum(p["_value"] for p in by("illiquid_private"))
    icap = ips["concentration"]["illiquid_private_max_pct"]
    ishare = illiq / investable if investable else 0
    conc += (f'<tr><td>Illiquid private</td><td class=num>{usd(illiq)}</td>'
             f'<td class=num>{pct(ishare)}</td><td class="num muted">{pct(icap)}</td>'
             f'<td class="{"ok" if ishare<=icap else "off"}">{"ok" if ishare<=icap else "BREACH"}</td></tr>')

    BUCKET_LABEL = {"liquid": "Liquid", "education": "Education (529)",
                    "illiquid_private": "Illiquid private", "real_estate": "Real estate"}
    holdings = ""
    for b in ("liquid", "education", "illiquid_private", "real_estate"):
        rows = sorted(by(b), key=lambda x: -x["_value"])
        if not rows:
            continue
        sub = sum(p["_value"] for p in rows)
        holdings += f'<tr class=grp><td colspan=4>{BUCKET_LABEL[b]}</td><td class=num>{usd(sub)}</td></tr>'
        for p in rows:
            holdings += (f'<tr><td>{html.escape(p["name"])}</td>'
                         f'<td class=muted>{html.escape(str(p.get("institution","")))}</td>'
                         f'<td class=muted>{p["asset_class"].replace("_"," ")}</td>'
                         f'<td class="num muted">{pct(p["_value"]/net_worth) if net_worth else ""}</td>'
                         f'<td class=num>{usd(p["_value"])}</td></tr>')
    liab_rows = "".join(
        f'<tr><td>{html.escape(l["name"])}</td><td class=muted></td><td class=muted></td>'
        f'<td class="num muted"></td><td class="num neg">−{usd(l["balance_usd"])}</td></tr>'
        for l in liabilities)

    ta = ips["target_allocation"]
    body = f"""
<h1>Balance Sheet — {html.escape(data['meta']['owner'])}</h1>
<div class=date>As of {data['meta']['as_of']} · reconciled to statements{" · LIVE prices" if live else ""}</div>

<div class=cards>
 {card("Net Worth", usd(net_worth))}
 {card("Investable", usd(investable), "ex-home")}
 {card("FI (mtg-adj)", pct(m), f"{pct(g)} of gross")}
 {card("Cash to deploy", usd(deploy), "→ bonds")}
</div>

<h2>Financial Independence</h2>
{progress("vs gross " + usd(ips['fi_target_gross_usd']), g, "target")}
{progress("vs mortgage-adj " + usd(ips['fi_target_mortgage_adj_usd']), m, "target")}

<h2>Liquid Allocation vs IPS &nbsp;(target {ta['equity']*100:.0f} / {ta['fixed_income']*100:.0f} / {ta['cash']*100:.0f}) · white tick = target</h2>
<table>
 <tr><th>Sleeve</th><th></th><th class=num>Actual</th><th class=num>Target</th><th class=num>Drift</th><th class=num>Value</th></tr>
 {alloc_rows}
</table>

<h2>Concentration</h2>
<table>
 <tr><th>Position</th><th class=num>Value</th><th class=num>Share</th><th class=num>Cap</th><th></th></tr>
 {conc}
</table>

<h2>Holdings</h2>
<table>
 <tr><th>Holding</th><th>Institution</th><th>Class</th><th class=num>% NW</th><th class=num>Value</th></tr>
 {holdings}
 <tr class=grp><td colspan=4>Liabilities</td><td class="num neg">−{usd(total_debt)}</td></tr>
 {liab_rows}
 <tr class=grp><td colspan=4>Net Worth</td><td class=num>{usd(net_worth)}</td></tr>
</table>
"""
    return page(f"Balance Sheet — {data['meta']['owner']}", "index.html", body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(Path(__file__).parent / "positions.yaml"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", default=str(Path(__file__).parent / "index.html"))
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    data = yaml.safe_load(Path(args.file).read_text())
    Path(args.out).write_text(render_html(data, args.live))
    print(f"wrote {args.out}")
    if args.open:
        import subprocess
        subprocess.run(["open", args.out], check=False)


if __name__ == "__main__":
    main()
