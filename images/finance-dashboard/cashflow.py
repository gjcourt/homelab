#!/usr/bin/env python3
"""Monthly household cash-flow model — income timing vs the monthly nut.

Annual totals hide the shape that matters month-to-month: front-loaded 401(k),
lumpy bonus + quarterly RSU vests, CA property tax in two installments (Apr+Dec),
and the April tax true-up from RSU/bonus supplemental under-withholding.

Inputs come from cashflow.yaml (DEFAULTS below is the schema + fallback). Render
the HTML page, or print the text table, or override any field on the CLI:
  python3 cashflow.py                                  # text table (defaults)
  python3 cashflow.py --file cashflow.yaml --html --out cashflow.html
  python3 cashflow.py --living 14000 --rsu-annual 0    # scenario override

NOT tax advice — withholding rates are planning approximations; the exact April
true-up is a CPA item.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

import webcommon as wc

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

DEFAULTS = dict(
    base=298_700.0, bonus=59_740.0, bonus_month=3,      # 20% bonus, paid March
    rsu_annual=450_000.0,                               # vests quarterly (Mar/Jun/Sep/Dec)
    pretax_pct=0.10, deferral_cap=24_500.0,
    aftertax_pct=0.13, total_415c=72_000.0, match=10_800.0,
    salary_wh=0.35,                                     # blended fed+CA withholding on salary
    supp_wh=0.3223,                                     # 22% fed + 10.23% CA supplemental (bonus/RSU)
    mortgage_pi=12_865.0,
    prop_tax=45_000.0,                                  # CA: 2 installments (Apr + Dec)
    hoa=500.0, insurance=1_000.0, insurance_month=1,
    living=12_500.0,
    april_trueup=40_000.0,                              # ESTIMATE — CPA item
    start_cash=50_000.0,
)
RSU_Q = (3, 6, 9, 12)


def load_params(path: str | None) -> dict:
    """Merge cashflow.yaml over DEFAULTS (yaml supplies values; DEFAULTS fills gaps)."""
    p = dict(DEFAULTS)
    if path and Path(path).exists():
        loaded = yaml.safe_load(Path(path).read_text()) or {}
        p.update({k: v for k, v in loaded.items() if k in DEFAULTS})
    return p


def _txt(x):
    return f"{x:,.0f}"


def build(p):
    gross_m = p["base"] / 12
    rsu_vest = p["rsu_annual"] / len(RSU_Q)
    aftertax_room = p["total_415c"] - p["deferral_cap"] - p["match"]
    pre_done = at_done = 0.0
    cum = p["start_cash"]
    rows, tot_in, tot_out, tot_401k = [], 0.0, 0.0, 0.0

    for i in range(12):
        m = i + 1
        pre = max(0.0, min(p["pretax_pct"] * gross_m, p["deferral_cap"] - pre_done))
        pre_done += pre
        at = max(0.0, min(p["aftertax_pct"] * gross_m, aftertax_room - at_done))
        at_done += at

        net_sal = (gross_m - pre) * (1 - p["salary_wh"]) - at
        net_bonus = (p["bonus"] if m == p["bonus_month"] else 0.0) * (1 - p["supp_wh"])
        net_rsu = (rsu_vest if m in RSU_Q else 0.0) * (1 - p["supp_wh"])
        inflow = net_sal + net_bonus + net_rsu

        ptax = p["prop_tax"] / 2 if m in (4, 12) else 0.0
        ins = p["insurance"] if m == p["insurance_month"] else 0.0
        trueup = p["april_trueup"] if m == 4 else 0.0
        outflow = p["mortgage_pi"] + ptax + p["hoa"] + ins + p["living"] + trueup

        net = inflow - outflow
        cum += net
        rows.append((MONTHS[i], net_sal, net_bonus, net_rsu, inflow, outflow, net, cum))
        tot_in += inflow
        tot_out += outflow
        tot_401k += pre + at

    return rows, tot_in, tot_out, tot_401k, pre_done, at_done


def render_html(p: dict) -> str:
    rows, tin, tout, t401k, pre_done, at_done = build(p)
    u = wc.usd
    surplus = tin - tout
    min_cum = min(r[7] for r in rows)
    min_mo = min(rows, key=lambda r: r[7])[0]

    def card(label, value, sub=""):
        return (f'<div class=card><div class=label>{label}</div>'
                f'<div class=value>{value}</div><div class=sub>{sub}</div></div>')

    trows = ""
    for mo, ns, nb, nr, inflow, outflow, net, cum in rows:
        ncls = ' class="num neg"' if net < 0 else " class=num"
        trows += (f'<tr><td>{mo}</td><td class=num>{u(ns)}</td>'
                  f'<td class=num>{u(nb) if nb else "—"}</td>'
                  f'<td class=num>{u(nr) if nr else "—"}</td>'
                  f'<td class=num>{u(inflow)}</td><td class=num>{u(outflow)}</td>'
                  f'<td{ncls}>{u(net)}</td><td class=num>{u(cum)}</td></tr>')

    body = f"""
<h1>Monthly Cash Flow</h1>
<div class=sublede>base {u(p['base'])} + {u(p['bonus'])} bonus + {u(p['rsu_annual'])} RSU/yr (quarterly) ·
living {u(p['living'])}/mo · mortgage {u(p['mortgage_pi'])}/mo · 401k {p['pretax_pct']*100:.0f}% pre + {p['aftertax_pct']*100:.0f}% after-tax</div>

<div class=cards>
 {card("Cash surplus / yr", u(surplus), "after everything")}
 {card("Take-home / yr", u(tin), "net of tax + 401k")}
 {card("Total spend / yr", u(tout), "incl. mortgage + tax")}
 {card("401(k) saved", u(t401k + p['match']), f"{u(t401k)} you + {u(p['match'])} match")}
</div>

<p class=note>Salary alone runs short most months — the quarterly RSU vests + the March bonus carry the year.
Cash bottoms at <b>{u(min_cum)}</b> in <b>{min_mo}</b> (property-tax installment + the ~{u(p['april_trueup'])} April tax true-up).
Keep ~{u(max(0, -min_cum) + 60000)} buffer, or set up quarterly estimates to flatten April.</p>

<h2>Month by month</h2>
<table>
 <tr><th>Mo</th><th class=num>Net Pay</th><th class=num>Bonus</th><th class=num>RSU</th>
     <th class=num>In</th><th class=num>Out</th><th class=num>Net</th><th class=num>Cumulative</th></tr>
 {trows}
 <tr class=grp><td>Year</td><td class=num></td><td class=num></td><td class=num></td>
     <td class=num>{u(tin)}</td><td class=num>{u(tout)}</td><td class=num>{u(surplus)}</td><td class=num>{u(rows[-1][7])}</td></tr>
</table>
<p class=note>April includes a ~{u(p['april_trueup'])} tax true-up <i>estimate</i> (RSU/bonus supplemental under-withholding) — a CPA item; quarterly estimates would smooth it.</p>
"""
    return wc.page("Cash Flow", "cashflow.html", body)


def print_text(p):
    rows, tin, tout, t401k, pre_done, at_done = build(p)
    print(f"\n  MONTHLY CASH FLOW — base {_txt(p['base'])}, bonus {_txt(p['bonus'])}, "
          f"RSU {_txt(p['rsu_annual'])}/yr\n")
    print(f"  {'Mo':<4}{'NetPay':>10}{'Bonus':>10}{'RSU':>11}{'IN':>11}{'OUT':>11}{'NET':>11}{'CUMUL':>12}")
    print("  " + "-" * 86)
    for mo, ns, nb, nr, i, o, n, c in rows:
        flag = "  <-- short" if n < 0 else ""
        print(f"  {mo:<4}{_txt(ns):>10}{_txt(nb):>10}{_txt(nr):>11}{_txt(i):>11}"
              f"{_txt(o):>11}{_txt(n):>11}{_txt(c):>12}{flag}")
    print("  " + "-" * 86)
    print(f"  {'YR':<4}{'':>32}{_txt(tin):>11}{_txt(tout):>11}{_txt(tin-tout):>11}{_txt(rows[-1][7]):>12}")
    print(f"\n  401(k) {_txt(t401k)} (pre {_txt(pre_done)} + after-tax {_txt(at_done)}) + match {_txt(p['match'])}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(Path(__file__).parent / "cashflow.yaml"))
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--out", default=str(Path(__file__).parent / "cashflow.html"))
    ap.add_argument("--open", action="store_true")
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k.replace('_','-')}", type=type(v), default=None)
    args = ap.parse_args()

    p = load_params(args.file)
    for k in DEFAULTS:  # CLI overrides win over the yaml
        v = getattr(args, k)
        if v is not None:
            p[k] = v

    if args.html:
        Path(args.out).write_text(render_html(p))
        print(f"wrote {args.out}")
        if args.open:
            import subprocess
            subprocess.run(["open", args.out], check=False)
    else:
        print_text(p)


if __name__ == "__main__":
    main()
