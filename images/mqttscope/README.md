# mqttscope

MQTT broker health + round-trip latency exporter for the homelab mosquitto
broker. Sibling to `netscope` (eBPF network) and `thermalscope` (thermal/RAPL):
a small custom exporter that turns broker internals into Prometheus metrics and
surfaces them in a Grafana dashboard.

## What it does

1. **Subscribes to `$SYS/#`** and republishes the interesting broker gauges
   (connected clients, message/byte throughput, connection + socket churn,
   stored messages, uptime, dropped publishes) as Prometheus metrics.
2. **Round-trip latency probe** — on a timer it publishes a nonce-tagged,
   timestamped message to `mqttscope/probe` (a topic it also subscribes to) and
   records the publish→self-delivery round-trip as `mqtt_probe_latency_seconds`.
   Unanswered probes are counted as `mqtt_probe_failures_total`. This is the
   "is MQTT laggy / lossy right now" signal that raw `$SYS` counters can't give.

## Metrics (port 9103, `/metrics`)

| Metric | Source |
| --- | --- |
| `mqtt_up` | exporter↔broker connection state (1/0) |
| `mqtt_probe_latency_seconds` | active round-trip probe |
| `mqtt_probe_last_success_timestamp_seconds` | active round-trip probe |
| `mqtt_probe_failures_total` | probes not echoed within `MQTT_PROBE_TIMEOUT` |
| `mqtt_clients_connected` | `$SYS/broker/clients/connected` |
| `mqtt_clients_total` | `$SYS/broker/clients/total` |
| `mqtt_messages_received_per_min` | `$SYS/broker/load/messages/received/1min` |
| `mqtt_messages_sent_per_min` | `$SYS/broker/load/messages/sent/1min` |
| `mqtt_bytes_received_per_min` | `$SYS/broker/load/bytes/received/1min` |
| `mqtt_bytes_sent_per_min` | `$SYS/broker/load/bytes/sent/1min` |
| `mqtt_messages_stored` | `$SYS/broker/messages/stored` |
| `mqtt_connections_per_min` | `$SYS/broker/load/connections/1min` (churn) |
| `mqtt_sockets_per_min` | `$SYS/broker/load/sockets/1min` (churn) |
| `mqtt_uptime_seconds` | `$SYS/broker/uptime` |
| `mqtt_messages_dropped` | `$SYS/broker/publish/messages/dropped` |

`/healthz` returns 200 while the exporter holds a live broker session, 503
otherwise (drives the Deployment readiness probe).

## Configuration (env)

See the header of `exporter.py`. Defaults target the in-cluster broker
(`mosquitto.mosquitto.svc.cluster.local:1883`). Credentials come from
`MQTT_USERNAME` / `MQTT_PASSWORD` (mounted from the `mqttscope-mqtt` Secret).

## MQTT ACL requirement

The broker's mmwave/espresense/zigbee2mqtt users are scoped to their own topic
trees and **cannot read `$SYS/#`**. mqttscope needs a dedicated broker user
whose ACL grants:

```
user mqttscope
topic read $SYS/#
topic readwrite mqttscope/#
```

Add that user to the mosquitto `passwordfile` + `acl` (SOPS-encrypted,
operator-only). See `apps/base/mqttscope/secret-mqtt.yaml.example`.

## Build

Built + pushed to `ghcr.io/gjcourt/mqttscope` by
`.github/workflows/build-mqttscope.yml` on changes under `images/mqttscope/`.
Pin the resulting date-sha tag in `apps/base/mqttscope/deployment.yaml`.
