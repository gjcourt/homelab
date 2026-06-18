#!/usr/bin/env python3
"""Render the Runway page — an interactive "how much runway / can I retire now"
projector. All modeling runs client-side (JS); inputs come from runway.yaml.

Nominal model: balance compounds at the chosen return; before retirement annual
savings are added, after retirement annual spend is withdrawn; spend + savings
grow with inflation. Shows three deterministic lines (conservative / expected /
optimistic) on a Chart.js chart, plus a Monte Carlo success probability.

  python3 runway.py [--file runway.yaml] [--out runway.html]

Requires chart.min.js served alongside (the image build fetches a pinned copy).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

import webcommon as wc

JS = r"""
const R = RUNWAY;
const fmtM = x => (x<0?'−$':'$') + Math.abs(x/1e6).toFixed(2) + 'M';
const fmtK = x => '$' + Math.round(x).toLocaleString();

function project(rate, retireAge, spend0, sav0, infl){
  let bal=R.current_investable, spend=spend0, sav=sav0;
  const pts=[{x:R.current_age, y:bal}];
  for(let age=R.current_age; age<R.end_age; age++){
    bal = (age<retireAge) ? bal*(1+rate)+sav : bal*(1+rate)-spend;
    spend*=(1+infl); sav*=(1+infl);
    pts.push({x:age+1, y:bal});   // allow negative — shows how far underwater you'd go
  }
  return pts;
}
function depletion(pts){ for(const p of pts) if(p.y<=0) return p.x; return null; }

function gauss(){ let u=0,v=0; while(!u)u=Math.random(); while(!v)v=Math.random();
  return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v); }
function montecarlo(meanR, retireAge, spend0, sav0, infl, vol, n){
  let ok=0;
  for(let i=0;i<n;i++){
    let bal=R.current_investable, spend=spend0, sav=sav0, alive=true;
    for(let age=R.current_age; age<R.end_age; age++){
      const r = meanR + vol*gauss();
      bal = (age<retireAge) ? bal*(1+r)+sav : bal*(1+r)-spend;
      spend*=(1+infl); sav*=(1+infl);
      if(bal<=0){ alive=false; break; }
    }
    if(alive) ok++;
  }
  return ok/n;
}

let chart;
function recompute(){
  const get = id => parseFloat(document.getElementById(id).value);
  const retireAge = get('retire_age'), spend = get('annual_spend'),
        exp = get('expected_return'), infl = get('inflation'), sav = get('annual_savings');
  // slider value labels
  document.getElementById('retire_age_v').textContent = retireAge + (retireAge<=R.current_age?' (now)':'');
  document.getElementById('annual_spend_v').textContent = fmtK(spend)+'/yr';
  document.getElementById('expected_return_v').textContent = (exp*100).toFixed(1)+'%';
  document.getElementById('inflation_v').textContent = (infl*100).toFixed(1)+'%';
  document.getElementById('annual_savings_v').textContent = fmtK(sav)+'/yr';

  const lo = project(exp-R.spread, retireAge, spend, sav, infl);
  const mid = project(exp, retireAge, spend, sav, infl);
  const hi = project(exp+R.spread, retireAge, spend, sav, infl);
  chart.data.datasets[0].data = lo;
  chart.data.datasets[1].data = mid;
  chart.data.datasets[2].data = hi;
  chart.update('none');

  const dep = depletion(mid);
  const term = mid[mid.length-1].y;
  const success = montecarlo(exp, retireAge, spend, sav, infl, R.return_vol, R.mc_paths);

  const setT = (id, txt, cls) => { const e=document.getElementById(id);
    e.textContent=txt; e.className='value big '+(cls||''); };
  setT('t_dep', dep ? ('age '+dep) : (R.end_age+'+'), dep ? (dep<80?'bad':'warn') : 'pos');
  setT('t_succ', (success*100).toFixed(0)+'%', success>=0.9?'pos':success>=0.75?'warn':'bad');
  setT('t_term', fmtM(term), term>0?'pos':'bad');
  setT('t_verdict', success>=0.9?'Work-optional':success>=0.75?'Borderline':'Keep earning',
       success>=0.9?'pos':success>=0.75?'warn':'bad');
}

function init(){
  Chart.defaults.color = '#8b97a8';
  Chart.defaults.borderColor = '#262d38';
  const ctx = document.getElementById('chart');
  chart = new Chart(ctx, {
    type:'line',
    data:{ datasets:[
      {label:'Conservative', data:[], borderColor:'#d97706', pointRadius:0, borderWidth:1.5, tension:.1},
      {label:'Expected', data:[], borderColor:'#2f6fed', pointRadius:0, borderWidth:2.5, tension:.1,
       fill:{target:{value:0}, above:'transparent', below:'rgba(248,113,113,0.22)'}},
      {label:'Optimistic', data:[], borderColor:'#16a34a', pointRadius:0, borderWidth:1.5, tension:.1},
    ]},
    options:{ animation:false, parsing:false, responsive:true, maintainAspectRatio:false,
      interaction:{mode:'index', intersect:false},
      scales:{
        x:{type:'linear', title:{display:true,text:'Age'}, min:R.current_age, max:R.end_age,
           ticks:{stepSize:5}, grid:{color:'#1c222b'}},
        y:{title:{display:true,text:'Balance'},
           ticks:{callback:v=>(v<0?'−$':'$')+Math.abs(v/1e6).toFixed(1)+'M'},
           grid:{color:'#1c222b'}}
      },
      plugins:{ legend:{labels:{usePointStyle:true, boxWidth:8}},
        tooltip:{callbacks:{label:c=>c.dataset.label+': '+fmtM(c.parsed.y)+' @ '+c.parsed.x}},
        zoom:{ zoom:{ wheel:{enabled:true},
          drag:{enabled:true, backgroundColor:'rgba(47,111,237,0.12)', borderColor:'#2f6fed', borderWidth:1},
          mode:'x' } } }
    }
  });
  ctx.ondblclick = ()=>chart.resetZoom();
  document.querySelectorAll('input[type=range]').forEach(el=>el.addEventListener('input', recompute));
  recompute();
}
window.addEventListener('load', init);
"""

# (id, label, min, max, step) — value formatting handled in JS
SLIDERS = [
    ("retire_age", "Retirement age", "current_age", 75, 1),
    ("annual_spend", "Annual spend (today's $)", 100_000, 400_000, 5_000),
    ("expected_return", "Expected return (nominal)", 0.03, 0.10, 0.0025),
    ("inflation", "Inflation", 0.00, 0.05, 0.0025),
    ("annual_savings", "Pre-retirement savings/yr", 0, 300_000, 10_000),
]


def render_html(cfg: dict) -> str:
    baked = {k: cfg[k] for k in (
        "current_investable", "current_age", "end_age", "spread", "return_vol", "mc_paths")}

    sliders = ""
    for sid, label, lo, hi, step in SLIDERS:
        lo_v = cfg["current_age"] if lo == "current_age" else lo
        sliders += (f'<div class=ctrl><label>{label} <b id="{sid}_v"></b></label>'
                    f'<input type=range id="{sid}" min="{lo_v}" max="{hi}" step="{step}" '
                    f'value="{cfg[sid]}"></div>')

    body = f"""
<h1>Retirement Runway</h1>
<div class=sublede>Will the money last? Drag the sliders — retire earlier, spend more, change returns — and the
projection + the odds update live. Nominal model; spend &amp; savings grow with inflation. <b>Not advice.</b></div>

<div class=results>
 <div class=card><div class=label>Funds last to</div><div class="value big" id=t_dep></div><div class=sub>expected case</div></div>
 <div class=card><div class=label>Monte Carlo success</div><div class="value big" id=t_succ></div><div class=sub>chance funds last to {cfg['end_age']}</div></div>
 <div class=card><div class=label>Balance at {cfg['end_age']}</div><div class="value big" id=t_term></div><div class=sub>expected case</div></div>
 <div class=card><div class=label>Verdict</div><div class="value big" id=t_verdict></div><div class=sub>at 90% success bar</div></div>
</div>

<div class=chartbox><div style="height:340px"><canvas id=chart></canvas></div>
 <div class=legend>Three deterministic paths at expected ±{cfg['spread']*100:.0f}% return; <span style="color:#f87171">red = underwater</span>. Success % runs {cfg['mc_paths']} Monte Carlo paths (vol {cfg['return_vol']*100:.0f}%). <b>Drag to zoom a year range · double-click to reset.</b></div>
</div>

<div class=controls>
 {sliders}
</div>
<p class=note>Starting from {wc.usd(cfg['current_investable'])} investable at age {cfg['current_age']}. "Retire now" = retirement age at your current age.
Real advisors run this as Monte Carlo for exactly this reason — a single line hides sequence-of-returns risk.</p>
"""
    head = ('<script src=chart.min.js></script>\n'
            '<script src=chartjs-zoom.min.js></script>\n<script>\nconst RUNWAY = '
            + json.dumps(baked) + ';\n' + JS + '\n</script>')
    return wc.page("Retirement Runway", "runway.html", body, head_extra=head)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(Path(__file__).parent / "runway.yaml"))
    ap.add_argument("--out", default=str(Path(__file__).parent / "runway.html"))
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.file).read_text())
    Path(args.out).write_text(render_html(cfg))
    print(f"wrote {args.out}")
    if args.open:
        import subprocess
        subprocess.run(["open", args.out], check=False)


if __name__ == "__main__":
    main()
