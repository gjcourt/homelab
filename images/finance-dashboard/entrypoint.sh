#!/bin/sh
# Render the four pages from the mounted data, copy static assets, then serve.
set -eu

OUT="${OUT_DIR:-/srv/html}"
DATA="${DATA_DIR:-/data}"

mkdir -p "$OUT"
cp /app/static/* "$OUT"/   # style.css, chart.min.js, chartjs-zoom.min.js

# Static prices (offline) — no internet egress; all charts are client-side JS.
python report_html.py --file "$DATA/positions.yaml"                                --out "$OUT/index.html"
python cashflow.py    --file "$DATA/cashflow.yaml"   --html                        --out "$OUT/cashflow.html"
python realestate.py  --str  "$DATA/str.yaml" --candidates "$DATA/candidates.yaml" --out "$OUT/realestate.html"
python runway.py      --file "$DATA/runway.yaml"                                   --out "$OUT/runway.html"

cd "$OUT"
exec python -m http.server 8080
