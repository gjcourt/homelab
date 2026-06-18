#!/usr/bin/env python3
"""Render the Real Estate (STR) page of the finance-dashboard site.

Two sections:
  - an INTERACTIVE dual-structure STR pro-forma (sliders → client-side JS;
    the math is ported from re_taxshield.proforma), defaults from str.yaml;
  - a candidate shortlist table from candidates.yaml (produced by
    `redfin_filter.py --emit-yaml` from a Redfin export). Clicking a candidate
    loads its price into the model.

  python3 realestate.py [--str str.yaml] [--candidates candidates.yaml] [--out realestate.html]
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import yaml

import webcommon as wc

# Sliders: (id, label, min, max, step, fmt). fmt ∈ money / pct0 / pct1
SLIDERS = [
    ("price", "Purchase price", 1_000_000, 5_000_000, 50_000, "money"),
    ("down_pct", "Down payment", 0.05, 0.30, 0.005, "pct1"),
    ("rate", "Mortgage rate", 0.04, 0.085, 0.00125, "pct1"),
    ("nightly", "Nightly rate", 300, 2000, 25, "money"),
    ("occupancy", "Occupancy", 0.25, 0.80, 0.01, "pct0"),
    ("insurance_pct", "Insurance (fire-zone)", 0.005, 0.03, 0.0005, "pct1"),
    ("costseg_pct", "Cost-seg accel.", 0.15, 0.45, 0.01, "pct0"),
    ("marginal", "Marginal tax rate", 0.35, 0.52, 0.005, "pct1"),
]
FIXED_KEYS = ("term", "prop_tax_rate", "maint_pct", "str_op_pct",
              "land_frac", "rental_share", "bonus_pct")

JS = """
function fmtVal(v, f){ if(f==='money') return '$'+Math.round(v).toLocaleString();
  if(f==='pct0') return (v*100).toFixed(0)+'%'; return (v*100).toFixed(1)+'%'; }
function money(x){ return (x<0?'−$':'$')+Math.abs(Math.round(x)).toLocaleString(); }
function annual_pi(loan,rate,term){ const r=rate/12,n=term*12;
  if(r===0) return loan/term; return loan*r*Math.pow(1+r,n)/(Math.pow(1+r,n)-1)*12; }
function proforma(p){
  const loan=p.price*(1-p.down_pct), pi=annual_pi(loan,p.rate,p.term), y1i=loan*p.rate;
  const ptax=p.price*p.prop_tax_rate, ins=p.price*p.insurance_pct, maint=p.price*p.maint_pct;
  const revenue=p.nightly*365*p.occupancy, strop=revenue*p.str_op_pct;
  const net_cash=pi+ptax+ins+maint+strop-revenue;
  const rb=(1-p.land_frac)*p.rental_share*p.price;
  const dep_y1=rb*p.costseg_pct*p.bonus_pct + rb*(1-p.costseg_pct)/27.5;
  const dep_steady=rb*(1-p.costseg_pct)/27.5;
  const rce=p.rental_share*(y1i+ptax+ins+maint)+strop;
  const at=(dep)=> net_cash - (-(revenue-rce-dep)*p.marginal);
  return {revenue, net_cash, nat_y1:at(dep_y1), nat_steady:at(dep_steady),
          down:p.price*p.down_pct};
}
function recompute(){
  const p=Object.assign({}, FIXED);
  document.querySelectorAll('input[type=range]').forEach(el=>{
    p[el.id]=parseFloat(el.value);
    document.getElementById(el.id+'_v').textContent=fmtVal(p[el.id], el.dataset.fmt);
  });
  const r=proforma(p);
  document.getElementById('r_revenue').textContent=money(r.revenue);
  document.getElementById('r_netcash').textContent=money(r.net_cash);
  document.getElementById('r_y1').textContent=money(r.nat_y1);
  document.getElementById('r_steady').textContent=money(r.nat_steady);
  document.getElementById('r_down').textContent='Down: '+money(r.down);
}
function setPrice(v){ const el=document.getElementById('price');
  el.value=Math.max(el.min, Math.min(el.max, v)); recompute();
  window.scrollTo({top:0, behavior:'smooth'}); }
window.addEventListener('load', function(){
  document.querySelectorAll('input[type=range]').forEach(el=>el.addEventListener('input', recompute));
  recompute();
});
"""


def render_html(cfg: dict, cands: dict) -> str:
    fixed = {k: cfg[k] for k in FIXED_KEYS}
    sliders = ""
    for sid, label, lo, hi, step, fmt in SLIDERS:
        sliders += (f'<div class=ctrl><label>{label} <b id="{sid}_v"></b></label>'
                    f'<input type=range id="{sid}" min="{lo}" max="{hi}" step="{step}" '
                    f'value="{cfg[sid]}" data-fmt="{fmt}"></div>')

    rows = ""
    for c in cands.get("candidates", []):
        if c.get("land"):
            continue  # existing-structure candidates only on the table
        price = c.get("price") or 0
        flags = []
        if (c.get("freeway_mi") or 9) < 0.5:
            flags.append('<span class=off>freeway</span>')
        bdba = f'{c.get("beds","?")}/{c.get("baths","?")}'
        rows += (f'<tr class=click onclick="setPrice({price})">'
                 f'<td class=num>{c.get("fit","")}</td>'
                 f'<td><a href="{html.escape(c.get("url",""))}" onclick="event.stopPropagation()">{html.escape(c.get("address",""))}</a> {" ".join(flags)}</td>'
                 f'<td class=num>${price/1e6:.2f}M</td><td class=num>{c.get("acres","")}</td>'
                 f'<td class=num>{bdba}</td><td class=num>{c.get("dist_mi","")}</td>'
                 f'<td class=num>{c.get("dom","")}</td></tr>')

    body = f"""
<h1>Wine-Country STR — Dual-Structure Model</h1>
<div class=sublede>Live pro-forma for a 2-structure property (rent one, live in the other). Drag the sliders;
click a candidate below to load its price. Fixed: {cfg['rental_share']*100:.0f}% rental share, {cfg['land_frac']*100:.0f}% land,
{cfg['prop_tax_rate']*100:.2f}% prop-tax, 100% bonus depreciation. <b>Not tax advice.</b></div>

<div class=results>
 <div class=card><div class=label>Annual STR revenue</div><div class="value big pos" id=r_revenue></div></div>
 <div class=card><div class=label>Net cash cost / yr</div><div class="value big" id=r_netcash></div></div>
 <div class=card><div class=label>Year-1 after-tax</div><div class="value big" id=r_y1></div><div class=sub id=r_down></div></div>
 <div class=card><div class=label>Steady-state / yr</div><div class="value big" id=r_steady></div><div class=sub>yr 2+ (shield spent)</div></div>
</div>

<div class=controls>
 {sliders}
</div>
<p class=note>Year-1 after-tax assumes the STR-loophole qualifies (avg stay ≤7 days + material participation) so the
cost-seg depreciation offsets W-2. Steady-state shows the carry once bonus depreciation is exhausted.</p>

<h2>Candidates &nbsp;<span class=muted style="text-transform:none;font-weight:400">(from Redfin export · {cands.get("meta",{}).get("count","?")} hits · existing homes, by fit)</span></h2>
<table>
 <tr><th class=num>Fit</th><th>Address</th><th class=num>Price</th><th class=num>Acres</th>
     <th class=num>Bd/Ba</th><th class=num>Mi</th><th class=num>DOM</th></tr>
 {rows}
</table>
<p class=note>Fit = composite (Plaza proximity · freeway distance · acreage · value · DOM-leverage). Click a row to model it.</p>
"""
    head = "<script>\nconst FIXED = " + json.dumps(fixed) + ";\n" + JS + "\n</script>"
    return wc.page("Real Estate — STR Model", "realestate.html", body, head_extra=head)


def main() -> None:
    ap = argparse.ArgumentParser()
    here = Path(__file__).parent
    ap.add_argument("--str", default=str(here / "str.yaml"))
    ap.add_argument("--candidates", default=str(here / "candidates.yaml"))
    ap.add_argument("--out", default=str(here / "realestate.html"))
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.str).read_text())
    cands = yaml.safe_load(Path(args.candidates).read_text()) if Path(args.candidates).exists() else {}
    Path(args.out).write_text(render_html(cfg, cands))
    print(f"wrote {args.out}")
    if args.open:
        import subprocess
        subprocess.run(["open", args.out], check=False)


if __name__ == "__main__":
    main()
