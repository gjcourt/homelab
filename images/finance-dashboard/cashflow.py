#!/usr/bin/env python3
"""Monthly household cash-flow model — income timing vs the monthly nut.

Annual totals hide the shape that matters month-to-month: front-loaded 401(k),
lumpy bonus + quarterly RSU vests, CA property tax in two installments (Apr+Dec),
and the April tax true-up from RSU/bonus supplemental under-withholding.

The HTML page is interactive — sliders for base / RSU / bonus / mortgage /
living / 401(k) recompute the monthly table + summary live (client-side JS).
Inputs default from cashflow.yaml; the CLI text mode and overrides still work.

  python3 cashflow.py                                  # text table
  python3 cashflow.py --file cashflow.yaml --html --out cashflow.html

NOT tax advice — withholding rates are planning approximations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

import webcommon as wc

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

DEFAULTS = dict(
    base=298_700.0, bonus=59_740.0, bonus_month=3,
    rsu_annual=450_000.0,
    pretax_pct=0.10, deferral_cap=24_500.0,
    aftertax_pct=0.13, total_415c=72_000.0, match=10_800.0,
    salary_wh=0.35, supp_wh=0.3223,
    mortgage_pi=12_865.0,
    prop_tax=45_000.0,
    hoa=500.0, insurance=1_000.0, insurance_month=1,
    living=12_500.0,
    april_trueup=40_000.0,
    start_cash=50_000.0,
)
RSU_Q = (3, 6, 9, 12)

# (id, label, min, max, step, fmt) — the live sliders. Everything else is baked.
SLIDERS = [
    ("base", "Base salary", 200_000, 1_000_000, 5_000, "money"),
    ("rsu_annual", "RSU / stock per yr", 0, 5_000_000, 25_000, "money"),
    ("bonus", "Bonus", 0, 150_000, 5_000, "money"),
    ("mortgage_pi", "Mortgage P&I (monthly)", 4_000, 20_000, 250, "money"),
    ("living", "Living (monthly)", 5_000, 25_000, 500, "money"),
    ("pretax_pct", "401k pre-tax %", 0, 0.20, 0.01, "pct"),
    ("aftertax_pct", "401k after-tax % (mega-backdoor)", 0, 0.30, 0.01, "pct"),
]
FIXED_KEYS = ["bonus_month", "deferral_cap", "total_415c", "match", "salary_wh",
              "supp_wh", "prop_tax", "hoa", "insurance", "insurance_month",
              "april_trueup", "start_cash"]

JS = r"""
const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const money=x=>(x<0?'−$':'$')+Math.abs(Math.round(x)).toLocaleString();
const fmtV=(v,f)=>f==='pct'?(v*100).toFixed(0)+'%':'$'+Math.round(v).toLocaleString();
function build(p){
  const gm=p.base/12, rv=p.rsu_annual/4;
  const annualPre=Math.min(p.pretax_pct*p.base, p.deferral_cap);     // actual pre-tax (capped at the deferral limit)
  const atroom=Math.max(0, p.total_415c-annualPre-p.match);          // after-tax fills the rest up to the $72k 415(c) max
  let pd=0, ad=0, cum=p.start_cash, rows=[], tin=0, tout=0, t401k=0;
  for(let i=0;i<12;i++){ const m=i+1;
    const pre=Math.max(0,Math.min(p.pretax_pct*gm, p.deferral_cap-pd)); pd+=pre;
    const at=Math.max(0,Math.min(p.aftertax_pct*gm, atroom-ad)); ad+=at;
    const ns=(gm-pre)*(1-p.salary_wh)-at;
    const nb=(m===p.bonus_month?p.bonus:0)*(1-p.supp_wh);
    const nr=([3,6,9,12].indexOf(m)>=0?rv:0)*(1-p.supp_wh);
    const inf=ns+nb+nr;
    const ptax=(m===4||m===12)?p.prop_tax/2:0, ins=(m===p.insurance_month)?p.insurance:0, tu=(m===4)?p.april_trueup:0;
    const out=p.mortgage_pi+ptax+p.hoa+ins+p.living+tu;
    const net=inf-out; cum+=net;
    rows.push([MONTHS[i],ns,nb,nr,inf,out,net,cum]); tin+=inf; tout+=out; t401k+=pre+at;
  }
  return {rows,tin,tout,t401k,surplus:tin-tout,min:Math.min(...rows.map(r=>r[7]))};
}
function recompute(){
  const p=Object.assign({},FIXED);
  document.querySelectorAll('input[type=range]').forEach(el=>{ p[el.id]=parseFloat(el.value);
    document.getElementById(el.id+'_v').textContent=fmtV(p[el.id], el.dataset.fmt); });
  const r=build(p);
  document.getElementById('t_surplus').textContent=money(r.surplus);
  document.getElementById('t_takehome').textContent=money(r.tin);
  document.getElementById('t_spend').textContent=money(r.tout);
  const tot401=r.t401k+p.match, maxed=tot401>=p.total_415c-1;
  document.getElementById('t_401k').textContent=money(tot401);
  document.getElementById('t_401k').className='value big'+(maxed?' pos':'');
  document.getElementById('t_401k_sub').textContent=(maxed?'maxed · ':'')+'of '+money(p.total_415c)+' 415(c) max';
  let h='';
  for(const x of r.rows){ const net=x[6], ncls=net<0?'num neg':'num';
    h+='<tr><td>'+x[0]+'</td><td class=num>'+money(x[1])+'</td><td class=num>'+(x[2]?money(x[2]):'—')
      +'</td><td class=num>'+(x[3]?money(x[3]):'—')+'</td><td class=num>'+money(x[4])+'</td><td class=num>'
      +money(x[5])+'</td><td class="'+ncls+'">'+money(net)+'</td><td class=num>'+money(x[7])+'</td></tr>'; }
  h+='<tr class=grp><td>Year</td><td></td><td></td><td></td><td class=num>'+money(r.tin)+'</td><td class=num>'
    +money(r.tout)+'</td><td class=num>'+money(r.surplus)+'</td><td class=num>'+money(r.rows[11][7])+'</td></tr>';
  document.getElementById('cfbody').innerHTML=h;
  document.getElementById('lowcash').textContent=money(r.min);
}
function toggleEdit(){ const c=document.getElementById('cfg'), b=document.getElementById('editbtn');
  const hidden=c.classList.toggle('hidden');                       // default: hidden (display mode)
  b.textContent=hidden?'✎ Edit inputs':'Done'; b.classList.toggle('on', !hidden); }
window.addEventListener('load', function(){
  document.querySelectorAll('input[type=range]').forEach(el=>el.addEventListener('input', recompute));
  recompute();
});
"""


def load_params(path: str | None) -> dict:
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
    annual_pre = min(p["pretax_pct"] * p["base"], p["deferral_cap"])
    aftertax_room = max(0.0, p["total_415c"] - annual_pre - p["match"])
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
    fixed = {k: p[k] for k in FIXED_KEYS}
    sliders = ""
    for sid, label, lo, hi, step, fmt in SLIDERS:
        sliders += (f'<div class=ctrl><label>{label} <b id="{sid}_v"></b></label>'
                    f'<input type=range id="{sid}" min="{lo}" max="{hi}" step="{step}" '
                    f'value="{p[sid]}" data-fmt="{fmt}"></div>')

    def card(label, vid, sub=""):
        return (f'<div class=card><div class=label>{label}</div>'
                f'<div class="value big" id={vid}></div><div class=sub id={vid}_sub>{sub}</div></div>')

    body = f"""
<h1>Monthly Cash Flow</h1>
<div class=sublede>Display mode shows the year as budgeted. Hit <b>Edit inputs</b> to tune base / RSU / bonus / mortgage /
living / 401(k) — the monthly table + totals update live. Salary alone runs short most months; the quarterly RSU
vests + March bonus carry the year.</div>

<div class=editbar><button id=editbtn class=btn onclick="toggleEdit()">&#9998; Edit inputs</button></div>
<div class="controls hidden" id=cfg>
 {sliders}
</div>

<div class=results>
 {card("Cash surplus / yr", "t_surplus", "after everything")}
 {card("Take-home / yr", "t_takehome", "net of tax + 401k")}
 {card("Total spend / yr", "t_spend", "incl. mortgage + tax")}
 {card("401(k) saved", "t_401k", "you + employer match")}
</div>
<p class=note>Cash bottoms at <b id=lowcash></b> (the April property-tax installment + the ~{wc.usd(p['april_trueup'])}
tax true-up estimate). Withholding rates are approximations — a CPA item; quarterly estimates would smooth April.</p>

<h2>Month by month</h2>
<table>
 <thead><tr><th>Mo</th><th class=num>Net Pay</th><th class=num>Bonus</th><th class=num>RSU</th>
     <th class=num>In</th><th class=num>Out</th><th class=num>Net</th><th class=num>Cumulative</th></tr></thead>
 <tbody id=cfbody></tbody>
</table>
"""
    head = "<script>\nconst FIXED = " + json.dumps(fixed) + ";\n" + JS + "\n</script>"
    return wc.page("Cash Flow", "cashflow.html", body, head_extra=head)


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
    print(f"  {'YR':<4}{'':>32}{_txt(tin):>11}{_txt(tout):>11}{_txt(tin-tout):>11}{_txt(rows[-1][7]):>12}\n")


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
    for k in DEFAULTS:
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
