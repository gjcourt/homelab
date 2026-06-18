# finance-dashboard

Internal-only personal-finance **site** — four static pages rendered at startup
from mounted encrypted-YAML data, served on `:8080` via stdlib `http.server`:

| Page | Renderer ← data |
|---|---|
| `index.html` (balance sheet) | `report_html.py` ← `positions.yaml` |
| `cashflow.html` | `cashflow.py` ← `cashflow.yaml` |
| `realestate.html` (STR model) | `realestate.py` ← `str.yaml` + `candidates.yaml` |
| `runway.html` (retirement) | `runway.py` ← `runway.yaml` |

Shared chrome (nav + dark theme) comes from `webcommon.py` + `static/style.css`.
The real-estate + runway pages are interactive (client-side JS sliders, Monte
Carlo, Chart.js vendored at `static/chart.min.js`) — no backend.

**Code-only image** — no financial data baked in. The numbers arrive at runtime
via the mounted `finance-dashboard-data` Secret (5 YAML keys at `/data`), so the
image is safe in ghcr. Scripts mirror `~/src/utility/portfolio/`.

## Local test
```bash
# render the pages from sample data
cd ~/src/utility/portfolio
for r in report_html:positions cashflow:cashflow realestate runway; do :; done
python report_html.py --file positions.yaml --out /tmp/site/index.html
python cashflow.py --file cashflow.yaml --html --out /tmp/site/cashflow.html
python realestate.py --out /tmp/site/realestate.html
python runway.py --out /tmp/site/runway.html
cp style.css /tmp/site/ ; # + a chart.min.js for the runway page
# or build the image: docker build -t fd images/finance-dashboard
#   docker run -p 8080:8080 -v <dir-with-5-yamls>:/data fd
```

## Updating data
Edit the source YAMLs and run `scripts/update-finance-data.sh` (rebuilds +
encrypts the data secret) → PR → `rollout restart`. Data changes need **no image
rebuild**; only code/layout changes do.
