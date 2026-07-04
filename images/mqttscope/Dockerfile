# mqttscope — MQTT broker health + round-trip latency exporter.
#
# Subscribes to the mosquitto `$SYS/#` tree and runs an active latency probe
# against `mqttscope/probe`, exposing Prometheus metrics on :9103. In the same
# "scope" family as netscope (eBPF network) and thermalscope (thermal/RAPL),
# but a plain in-cluster Deployment — it needs no host access, only a network
# path to the broker and MQTT credentials that can read $SYS and read/write the
# probe topic.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY exporter.py .

EXPOSE 9103

# nobody:nogroup — unprivileged; pairs with readOnlyRootFilesystem in the
# Deployment (the exporter writes nothing to disk).
USER 65534:65534

ENTRYPOINT ["python", "-u", "exporter.py"]
