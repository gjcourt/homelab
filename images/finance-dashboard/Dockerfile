# Internal-only personal-finance site — 4 static pages rendered at startup from
# mounted encrypted-YAML data, served on :8080.
#   /index.html       balance sheet      (report_html.py ← positions.yaml)
#   /cashflow.html    monthly cash flow  (cashflow.py    ← cashflow.yaml)
#   /realestate.html  STR dual-structure (realestate.py  ← str.yaml + candidates.yaml)
#   /runway.html      retirement runway  (runway.py      ← runway.yaml)
#
# CODE-ONLY image — no financial data baked in (verified). Numbers arrive at
# runtime via the mounted `finance-dashboard-data` secret. Chart.js is vendored
# (static/chart.min.js, pinned 4.4.1) → no runtime egress, served locally.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Renderers + shared layer + static assets (style.css, vendored chart.min.js).
COPY webcommon.py portfolio.py report_html.py cashflow.py realestate.py runway.py entrypoint.sh ./
COPY static/ ./static/
RUN chmod 0755 entrypoint.sh

EXPOSE 8080

# nobody:nogroup — unprivileged; pairs with readOnlyRootFilesystem (HTML is
# written to an emptyDir at /srv/html, never the image fs).
USER 65534:65534

ENTRYPOINT ["./entrypoint.sh"]
