#!/usr/bin/env python3
"""mqttscope — MQTT broker health + round-trip latency exporter.

A small Prometheus exporter for a mosquitto broker, in the same "scope" spirit
as netscope (eBPF network) and thermalscope (thermal/RAPL). It does two things:

  1. Subscribes to the broker's `$SYS/#` tree and republishes the interesting
     gauges (clients, throughput, churn, stored messages, uptime, drops) as
     Prometheus metrics.

  2. Runs an active round-trip latency probe: on a timer it publishes a
     timestamped, nonce-tagged message to `mqttscope/probe` — a topic it is
     itself subscribed to — and records the publish→deliver round-trip as
     `mqtt_probe_latency_seconds`. This is the "is MQTT laggy right now" signal
     that $SYS counters can't give you (they show volume, not latency).

Config (all via env):
  MQTT_HOST            broker host                (default: mosquitto.mosquitto.svc.cluster.local)
  MQTT_PORT            broker port                (default: 1883)
  MQTT_USERNAME        broker username            (default: unset -> anonymous)
  MQTT_PASSWORD        broker password            (default: unset)
  MQTT_CLIENT_ID       client id                  (default: mqttscope)
  MQTT_PROBE_TOPIC     round-trip probe topic     (default: mqttscope/probe)
  MQTT_PROBE_INTERVAL  seconds between probes     (default: 15)
  MQTT_PROBE_TIMEOUT   seconds to await an echo   (default: 10)
  MQTT_PROBE_QOS       probe QoS (0|1|2)          (default: 1)
  MQTT_SYS_TOPIC       $SYS subtree to subscribe  (default: $SYS/#)
  LISTEN_PORT          metrics/healthz HTTP port  (default: 9103)
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    generate_latest,
)

log = logging.getLogger("mqttscope")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


HOST = _env("MQTT_HOST", "mosquitto.mosquitto.svc.cluster.local")
PORT = int(_env("MQTT_PORT", "1883"))
USERNAME = os.environ.get("MQTT_USERNAME") or None
PASSWORD = os.environ.get("MQTT_PASSWORD") or None
CLIENT_ID = _env("MQTT_CLIENT_ID", "mqttscope")
PROBE_TOPIC = _env("MQTT_PROBE_TOPIC", "mqttscope/probe")
PROBE_INTERVAL = float(_env("MQTT_PROBE_INTERVAL", "15"))
PROBE_TIMEOUT = float(_env("MQTT_PROBE_TIMEOUT", "10"))
PROBE_QOS = int(_env("MQTT_PROBE_QOS", "1"))
SYS_TOPIC = _env("MQTT_SYS_TOPIC", "$SYS/#")
LISTEN_PORT = int(_env("LISTEN_PORT", "9103"))

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
# Exporter <-> broker connection state. 1 while the exporter holds a live MQTT
# session, 0 (or absent) otherwise — the honest "can we even reach the broker"
# signal, independent of Prometheus being able to scrape this pod.
mqtt_up = Gauge("mqtt_up", "1 if the exporter is connected to the MQTT broker, else 0")

# Round-trip probe: publish->deliver latency over the broker, in seconds.
mqtt_probe_latency = Gauge(
    "mqtt_probe_latency_seconds",
    "Round-trip latency of the last successful mqttscope probe (publish to self-delivery), in seconds",
)
mqtt_probe_success_ts = Gauge(
    "mqtt_probe_last_success_timestamp_seconds",
    "Unix timestamp of the last successful round-trip probe",
)
mqtt_probe_failures = Counter(
    "mqtt_probe_failures_total",
    "Number of round-trip probes that were not echoed back within MQTT_PROBE_TIMEOUT",
)

# Broker $SYS gauges. Keyed by the $SYS topic they are populated from; the
# subscribe handler looks each incoming topic up in SYS_MAP below.
mqtt_clients_connected = Gauge("mqtt_clients_connected", "Currently connected MQTT clients")
mqtt_clients_total = Gauge("mqtt_clients_total", "Total registered MQTT clients (connected + persistent)")
mqtt_messages_received_per_min = Gauge(
    "mqtt_messages_received_per_min", "Broker messages received, 1-minute moving average"
)
mqtt_messages_sent_per_min = Gauge(
    "mqtt_messages_sent_per_min", "Broker messages sent, 1-minute moving average"
)
mqtt_bytes_received_per_min = Gauge(
    "mqtt_bytes_received_per_min", "Broker bytes received, 1-minute moving average"
)
mqtt_bytes_sent_per_min = Gauge(
    "mqtt_bytes_sent_per_min", "Broker bytes sent, 1-minute moving average"
)
mqtt_messages_stored = Gauge("mqtt_messages_stored", "Messages currently held in the broker message store")
mqtt_connections_per_min = Gauge(
    "mqtt_connections_per_min",
    "New CONNECT packets per minute (1-min moving average) — reconnect churn indicator",
)
mqtt_sockets_per_min = Gauge(
    "mqtt_sockets_per_min",
    "New socket connections per minute (1-min moving average) — reconnect churn indicator",
)
mqtt_uptime_seconds = Gauge("mqtt_uptime_seconds", "Broker uptime in seconds")
mqtt_messages_dropped = Gauge(
    "mqtt_messages_dropped", "Publish messages dropped by the broker (queue/inflight limits)"
)

# $SYS topic -> Gauge. mosquitto publishes these retained, so we get an
# immediate value on (re)subscribe and updates thereafter.
SYS_MAP = {
    "$SYS/broker/clients/connected": mqtt_clients_connected,
    "$SYS/broker/clients/total": mqtt_clients_total,
    "$SYS/broker/load/messages/received/1min": mqtt_messages_received_per_min,
    "$SYS/broker/load/messages/sent/1min": mqtt_messages_sent_per_min,
    "$SYS/broker/load/bytes/received/1min": mqtt_bytes_received_per_min,
    "$SYS/broker/load/bytes/sent/1min": mqtt_bytes_sent_per_min,
    "$SYS/broker/messages/stored": mqtt_messages_stored,
    "$SYS/broker/load/connections/1min": mqtt_connections_per_min,
    "$SYS/broker/load/sockets/1min": mqtt_sockets_per_min,
    "$SYS/broker/uptime": mqtt_uptime_seconds,
    "$SYS/broker/publish/messages/dropped": mqtt_messages_dropped,
}

# nonce -> monotonic send time for in-flight probes. Guarded by _probe_lock.
_inflight: dict[str, float] = {}
_probe_lock = threading.Lock()


def _parse_number(payload: str) -> float | None:
    """mosquitto $SYS values are mostly bare numbers, but uptime is
    '<n> seconds'. Take the first whitespace-separated token and float it."""
    token = payload.strip().split(" ", 1)[0]
    try:
        return float(token)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# MQTT callbacks (paho CallbackAPIVersion.VERSION2)
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, reason_code, properties):
    # paho v2 hands us a ReasonCode (has .is_failure); fall back to an int
    # comparison for safety.
    failed = getattr(reason_code, "is_failure", None)
    if failed is None:
        failed = reason_code != 0
    if failed:
        mqtt_up.set(0)
        log.error("connect failed: %s", reason_code)
        return
    mqtt_up.set(1)
    log.info("connected to %s:%s", HOST, PORT)
    # Subscribe to the $SYS tree (retained values arrive immediately) and to
    # our own probe topic so published probes loop back to us.
    client.subscribe([(SYS_TOPIC, 0), (PROBE_TOPIC, PROBE_QOS)])


def on_disconnect(client, userdata, flags, reason_code, properties):
    mqtt_up.set(0)
    log.warning("disconnected: %s", reason_code)


def on_message(client, userdata, msg):
    topic = msg.topic
    if topic == PROBE_TOPIC:
        _handle_probe_echo(msg.payload)
        return
    gauge = SYS_MAP.get(topic)
    if gauge is None:
        return
    try:
        value = _parse_number(msg.payload.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - never let a bad payload kill the loop
        return
    if value is not None:
        gauge.set(value)


def _handle_probe_echo(payload: bytes) -> None:
    """Payload is 'nonce:monotonic'. Match the nonce to an in-flight probe and
    record the round-trip latency."""
    try:
        nonce, _sent = payload.decode("ascii", "replace").split(":", 1)
    except ValueError:
        return
    now = time.monotonic()
    with _probe_lock:
        sent = _inflight.pop(nonce, None)
    if sent is None:
        return  # not ours, or already timed out
    latency = now - sent
    mqtt_probe_latency.set(latency)
    mqtt_probe_success_ts.set(time.time())


# ---------------------------------------------------------------------------
# Probe + timeout-sweep loop
# ---------------------------------------------------------------------------
def probe_loop(client: mqtt.Client) -> None:
    while True:
        _sweep_timeouts()
        if client.is_connected():
            nonce = uuid.uuid4().hex
            sent = time.monotonic()
            with _probe_lock:
                _inflight[nonce] = sent
            payload = f"{nonce}:{sent}"
            try:
                client.publish(PROBE_TOPIC, payload, qos=PROBE_QOS)
            except Exception as exc:  # noqa: BLE001
                log.warning("probe publish failed: %s", exc)
                with _probe_lock:
                    _inflight.pop(nonce, None)
        time.sleep(PROBE_INTERVAL)


def _sweep_timeouts() -> None:
    """Expire probes that were never echoed within PROBE_TIMEOUT and count
    them as failures — a stalled/lossy broker shows up here."""
    cutoff = time.monotonic() - PROBE_TIMEOUT
    expired = 0
    with _probe_lock:
        for nonce in [n for n, t in _inflight.items() if t < cutoff]:
            del _inflight[nonce]
            expired += 1
    for _ in range(expired):
        mqtt_probe_failures.inc()


# ---------------------------------------------------------------------------
# HTTP: /metrics + /healthz
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    # Set by main() so /healthz can report broker connectivity.
    client: mqtt.Client = None  # type: ignore[assignment]

    def do_GET(self):  # noqa: N802 - stdlib naming
        if self.path == "/healthz":
            ok = self.client is not None and self.client.is_connected()
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n" if ok else b"broker unreachable\n")
            return
        if self.path.startswith("/metrics"):
            output = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(output)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):  # silence per-request logging
        return


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    mqtt_up.set(0)

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=CLIENT_ID,
    )
    if USERNAME:
        client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    # Auto-reconnect with backoff so broker restarts / churn don't kill us.
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    Handler.client = client
    httpd = ThreadingHTTPServer(("", LISTEN_PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    log.info("serving /metrics and /healthz on :%s", LISTEN_PORT)

    threading.Thread(target=probe_loop, args=(client,), daemon=True, name="probe").start()

    # connect_async + loop_forever handles the initial connect and all
    # reconnects on the network thread; this call blocks for the process life.
    client.connect_async(HOST, PORT, keepalive=30)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
