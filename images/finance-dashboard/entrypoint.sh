#!/bin/sh
# Render the dashboard from the mounted positions data, then serve it.
set -eu

OUT_DIR="${OUT_DIR:-/srv/html}"
POSITIONS_FILE="${POSITIONS_FILE:-/data/positions.yaml}"

mkdir -p "$OUT_DIR"

# Static prices (offline) for v1 — no internet egress required, so the
# NetworkPolicy stays DNS-only. Add `--live` later (plus an egress rule for the
# Stooq / Coinbase quote endpoints) to mark public holdings to market.
python report_html.py --file "$POSITIONS_FILE" --out "$OUT_DIR/index.html"

cd "$OUT_DIR"
exec python -m http.server 8080
