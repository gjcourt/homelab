#!/usr/bin/env python3
"""homepage-clicks — a tiny click-beacon exporter for the Homepage dashboard.

Homepage (gethomepage.dev) tiles are plain `<a href>` links, so nothing is
recorded when a tile is clicked. This exporter is the counting half of a
lightweight, self-hosted usage tracker in the same "scope" spirit as
mqttscope / netscope / thermalscope:

  * A ~20-line delegated click handler injected into Homepage's `custom.js`
    fires `navigator.sendBeacon('/api/clicks', '{"service","group"}')` on
    every tile click.
  * The gateway routes `home.burntbytes.com/api/clicks` (same-origin path
    match) to this Deployment, which increments the labelled Prometheus
    counter `homepage_tile_clicks_total{service,group}` and serves it at
    `/metrics` for kube-prometheus-stack to scrape.

Grafana then renders clicks-over-time, top tiles, and never-clicked tiles,
turning dashboard re-ordering from a guess into a measured decision.

Privacy / abuse posture. The Homepage dashboard is PUBLIC, so `/api/clicks`
is reachable by anyone who can reach the gateway. We store only a service
label + timestamp (no PII, no IP, no href, no user agent). Because the
endpoint is public, three cheap defences bound the blast radius of a bad
actor spamming it:

  1. Origin allowlist   — reject POSTs whose Origin isn't the dashboard's own
                          host. (A filter, not a security boundary: a
                          non-browser client can forge Origin. It stops
                          casual cross-site beacons, nothing more.)
  2. Global rate limit  — a token bucket caps sustained + burst intake.
  3. Series cap         — refuse to register a *new* {service,group} label
                          pair once MAX_SERIES distinct pairs are known, so a
                          spammer cannot explode Prometheus cardinality. Known
                          pairs keep counting.

Label values are also length-capped and charset-restricted before they ever
become a metric label.

Config (all via env):
  LISTEN_PORT      metrics + beacon HTTP port          (default: 9107)
  BEACON_PATH      POST path the gateway forwards here  (default: /api/clicks)
  ALLOWED_ORIGINS  comma-separated Origin allowlist;
                   empty disables the check
                   (default: https://home.burntbytes.com)
  MAX_LABEL_LEN    max chars per label value            (default: 64)
  MAX_SERIES       max distinct {service,group} pairs   (default: 256)
  RATE_QPS         sustained beacons/sec (token refill)  (default: 20)
  RATE_BURST       token-bucket capacity                (default: 40)
  MAX_BODY_BYTES   max request body read                 (default: 4096)
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

log = logging.getLogger("homepage-clicks")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


LISTEN_PORT = int(_env("LISTEN_PORT", "9107"))
BEACON_PATH = _env("BEACON_PATH", "/api/clicks")
ALLOWED_ORIGINS = {
    o.strip()
    for o in _env("ALLOWED_ORIGINS", "https://home.burntbytes.com").split(",")
    if o.strip()
}
MAX_LABEL_LEN = int(_env("MAX_LABEL_LEN", "64"))
MAX_SERIES = int(_env("MAX_SERIES", "256"))
RATE_QPS = float(_env("RATE_QPS", "20"))
RATE_BURST = float(_env("RATE_BURST", "40"))
MAX_BODY_BYTES = int(_env("MAX_BODY_BYTES", "4096"))

# Permitted characters in a label value. Covers every current tile/group name
# ("Home Assistant", "AdGuard Home", "Cluster Resources", "Living Room") plus a
# little slack; anything else is rejected rather than sanitised, so a weird
# payload never silently becomes a bogus metric.
_LABEL_RE = re.compile(r"^[\w \-.()/&+']+$")

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
tile_clicks = Counter(
    "homepage_tile_clicks_total",
    "Homepage dashboard tile clicks, by tile and group.",
    ["service", "group"],
)

# Operational visibility into the beacon endpoint itself: how many POSTs came
# in and why they were accepted/rejected. Handy for spotting abuse or a broken
# client without reading logs.
beacon_requests = Counter(
    "homepage_beacon_requests_total",
    "Beacon POSTs received, by outcome.",
    ["result"],
)

# Distinct {service,group} pairs currently tracked — watch this against
# MAX_SERIES to know if the cardinality cap is being approached.
beacon_series = Gauge(
    "homepage_beacon_series",
    "Number of distinct {service,group} label pairs currently tracked.",
)


# ---------------------------------------------------------------------------
# Token-bucket rate limiter (global, thread-safe)
# ---------------------------------------------------------------------------
class TokenBucket:
    def __init__(self, rate: float, capacity: float) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._capacity, self._tokens + (now - self._last) * self._rate
            )
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


_bucket = TokenBucket(RATE_QPS, RATE_BURST)

# Distinct {service,group} pairs seen so far — the cardinality cap set.
_seen: set[tuple[str, str]] = set()
_seen_lock = threading.Lock()


def _clean(value: object) -> str | None:
    """Coerce, trim, length-cap and charset-check one label value. Returns the
    cleaned string, or None if it is empty or contains disallowed characters."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or len(v) > MAX_LABEL_LEN:
        return None
    if not _LABEL_RE.match(v):
        return None
    return v


def _record(service: str, group: str) -> str:
    """Increment the click counter, honouring the distinct-series cap. Returns
    the outcome string used as the beacon_requests result label."""
    key = (service, group)
    with _seen_lock:
        if key not in _seen:
            if len(_seen) >= MAX_SERIES:
                return "rejected_series_cap"
            _seen.add(key)
            beacon_series.set(len(_seen))
    tile_clicks.labels(service=service, group=group).inc()
    return "accepted"


# ---------------------------------------------------------------------------
# HTTP: POST <BEACON_PATH>  +  GET /metrics  +  GET /healthz
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802 - stdlib naming
        if self.path == "/healthz":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/metrics"):
            output = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)
            return
        self._empty(404)

    def do_POST(self):  # noqa: N802 - stdlib naming
        # Route: only the configured beacon path is a valid POST target.
        if self.path.split("?", 1)[0] != BEACON_PATH:
            self._empty(404)
            return

        # Origin allowlist (a filter, not a security boundary — see module docstring).
        if ALLOWED_ORIGINS:
            origin = self.headers.get("Origin", "")
            if origin not in ALLOWED_ORIGINS:
                beacon_requests.labels(result="rejected_origin").inc()
                self._empty(403)
                return

        # Global rate limit.
        if not _bucket.take():
            beacon_requests.labels(result="rejected_ratelimit").inc()
            self._empty(429)
            return

        # Bounded body read + JSON parse.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            beacon_requests.labels(result="rejected_payload").inc()
            self._empty(400)
            return
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8", "strict"))
        except Exception:  # noqa: BLE001 - any parse failure is a bad payload
            beacon_requests.labels(result="rejected_payload").inc()
            self._empty(400)
            return

        if not isinstance(data, dict):
            beacon_requests.labels(result="rejected_payload").inc()
            self._empty(400)
            return

        service = _clean(data.get("service"))
        # group is optional; an empty/blank group is recorded as "".
        group_raw = data.get("group")
        group = "" if group_raw in (None, "") else _clean(group_raw)
        if service is None or group is None:
            beacon_requests.labels(result="rejected_payload").inc()
            self._empty(400)
            return

        result = _record(service, group)
        beacon_requests.labels(result=result).inc()
        # sendBeacon ignores the response body/status; 204 either way.
        self._empty(204 if result == "accepted" else 429)

    def log_message(self, *args):  # silence per-request logging
        return


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    # Initialise the outcome series so they exist at 0 before the first POST.
    for r in (
        "accepted",
        "rejected_origin",
        "rejected_ratelimit",
        "rejected_payload",
        "rejected_series_cap",
    ):
        beacon_requests.labels(result=r)
    beacon_series.set(0)

    httpd = ThreadingHTTPServer(("", LISTEN_PORT), Handler)
    log.info(
        "serving POST %s + /metrics + /healthz on :%s (origins=%s, max_series=%s)",
        BEACON_PATH,
        LISTEN_PORT,
        sorted(ALLOWED_ORIGINS) or "<any>",
        MAX_SERIES,
    )
    httpd.serve_forever()


if __name__ == "__main__":
    main()
