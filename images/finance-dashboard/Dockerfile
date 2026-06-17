# Internal-only net-worth / IPS-allocation dashboard.
#
# Renders positions.yaml (mounted at /data from a SOPS-encrypted Secret) to a
# static HTML balance-sheet + allocation report at startup, then serves it on
# :8080 via Python's stdlib http.server.
#
# CODE-ONLY image — it carries NO financial data. The numbers arrive at runtime
# via the mounted Secret, so the image is safe to publish to ghcr.io. (Verified:
# portfolio.py / report_html.py have no hardcoded values; they read --file.)
#
# Served internally only (ClusterIP + kubectl port-forward); no public ingress.
# Build triggers on changes to images/finance-dashboard/**.

FROM python:3.12-slim

WORKDIR /app

# Only dep is PyYAML — report_html.py reuses portfolio.py's pricing logic;
# matplotlib (used elsewhere in the portfolio toolkit) is not needed here.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY portfolio.py report_html.py entrypoint.sh ./
RUN chmod 0755 entrypoint.sh

EXPOSE 8080

# nobody:nogroup — unprivileged; pairs with readOnlyRootFilesystem in the
# Deployment (HTML is written to an emptyDir at /srv/html, never the image fs).
USER 65534:65534

ENTRYPOINT ["./entrypoint.sh"]
