# homepage-clicks — Homepage dashboard tile-click beacon exporter.
#
# Accepts same-origin `navigator.sendBeacon()` POSTs from Homepage's custom.js
# and increments a labelled Prometheus counter (homepage_tile_clicks_total)
# served on :9107. In the same "scope" family as mqttscope (a plain in-cluster
# Deployment, no host access) — stdlib http.server + prometheus_client only,
# runs unprivileged with a read-only root filesystem.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY exporter.py .

EXPOSE 9107

# nobody:nogroup — unprivileged; pairs with readOnlyRootFilesystem in the
# Deployment (the exporter writes nothing to disk).
USER 65534:65534

ENTRYPOINT ["python", "-u", "exporter.py"]
