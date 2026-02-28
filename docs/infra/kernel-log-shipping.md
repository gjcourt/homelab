# Talos Kernel Log Shipping

## 1. Overview

Talos nodes produce kernel (`dmesg`) and service logs that are not captured by
Promtail's standard container log scraping. This pipeline forwards those logs to
Loki via a dedicated **Vector** DaemonSet, enabling log-based alerting for
failure modes that produce no Prometheus metrics (e.g. BTRFS corruption in state
`EA`, iSCSI session errors).

## 2. Architecture

```
Talos node (host network)
  └─ machine.logging.destinations: tcp://192.168.5.1:30600
        │   json_lines over TCP
        ▼
Vector DaemonSet (monitoring namespace)
  ├─ TCP socket source  :6000
  ├─ VRL transform: normalise field names
  └─ Loki sink  →  http://loki.monitoring.svc.cluster.local:3100
        │
        ▼
Loki SingleBinary  (monitoring namespace)
  └─ Ruler: evaluates LogQL alerting rules every 1m
        │
        ▼
Alertmanager
```

### Why Vector, not Promtail?

Talos `machine.logging` sends log events as **newline-delimited JSON** (`json_lines`
format), not RFC 5424 syslog. Promtail's syslog receiver expects RFC 5424 framing
and will reject or mangle Talos events. Vector has a native `socket` source with
`decoding.codec: json` that handles this format correctly.

### Why a NodePort?

Talos runs on the host network, outside the Kubernetes pod network. The only way
to reach a pod from the host is via a `NodePort` (or a `hostPort`). NodePort
`30600` maps to Vector's TCP socket on port `6000`.

`externalTrafficPolicy: Local` ensures:
- Each Talos node's logs go to the Vector pod **on that same node** (no cross-node
  hops), preserving local context (hostname, etc.).
- No SNAT — the source IP is the node IP, which Vector uses to set the `host` label.

## 3. Configuration

### Manifests

| File | Purpose |
|---|---|
| `infra/controllers/vector/repository.yaml` | HelmRepository for `https://helm.vector.dev` |
| `infra/controllers/vector/release.yaml` | HelmRelease: `vector/vector` v0.50.0, DaemonSet role |
| `infra/controllers/vector/values.yaml` | Full Vector pipeline (source → transform → sink) |
| `infra/controllers/vector/nodeport.yaml` | `Service` type NodePort, port 30600 → 6000 |
| `infra/controllers/loki/alerting-rules.yaml` | Loki ruler rules ConfigMap (`BTRFSCorruptionDetected`, `ISCSIKernelError`) |
| `infra/controllers/loki/values.yaml` | `singleBinary.rulerConfig` and rules volume mount |

### Talos MachineConfig

`machine.logging.destinations` must be set on each Talos node. For the single
node cluster (`talos-519-vmy`):

```bash
talosctl -n talos-519-vmy patch mc --patch \
  '[{"op":"add","path":"/machine/logging","value":{"destinations":[{"endpoint":"tcp://192.168.5.1:30600","format":"json_lines"}]}}]'
```

This does **not** require a reboot. Talos applies the logging config live.

If the cluster gains additional nodes, repeat the `talosctl patch mc` for each
node IP (`-n <node-ip>`).

### Vector pipeline (values.yaml summary)

```yaml
# Receive Talos json_lines over TCP
sources:
  talos_logs:
    type: socket
    mode: tcp
    address: "0.0.0.0:6000"
    framing.method: newline_delimited
    decoding.codec: json

# Normalise field names (Talos uses "talos-service" with a hyphen)
transforms:
  normalize_talos:
    type: remap
    source: |
      .talos_service = string(del(."talos-service")) ?? "kernel"

# Forward to Loki with structured labels
sinks:
  loki_output:
    type: loki
    labels:
      job: talos-kernel
      talos_service: "{{ talos_service }}"
      level: "{{ level }}"
```

### Loki ruler

`rulerConfig` is set in `infra/controllers/loki/values.yaml`:

```yaml
loki:
  rulerConfig:
    storage:
      type: local
      local:
        directory: /var/loki/rules   # mounted from ConfigMap below
    rule_path: /var/loki/rules-temp
    alertmanager_url: http://kube-prometheus-stack-alertmanager.monitoring.svc.cluster.local:9093
    ring:
      kvstore:
        store: inmemory
    enable_api: true
    enable_alertmanager_v2: true

singleBinary:
  extraVolumes:
    - name: loki-rules
      configMap:
        name: loki-alerting-rules
  extraVolumeMounts:
    - name: loki-rules
      mountPath: /var/loki/rules/fake   # auth_enabled: false → tenant is "fake"
      readOnly: true
```

## 4. Alerting Rules

Defined in `infra/controllers/loki/alerting-rules.yaml` (ConfigMap `loki-alerting-rules`).

### BTRFSCorruptionDetected (critical)

```logql
sum(count_over_time({job="talos-kernel"} |= "parent transid verify failed" [5m])) > 0
```

Fires immediately (`for: 0m`) when BTRFS `parent transid verify failed` appears
in kernel logs. This indicates an iSCSI volume is serving stale data after a
session reconnect. The filesystem remains mounted **read-write** in error state
`EA` — `NodeFilesystemReadOnly` does **not** fire for this failure mode.

**Response**: identify the affected device via `talosctl dmesg`, map it to a
PVC, and recycle the pod + PVC. See [2026-02-27 incident](../incidents/2026-02-27-homeassistant-staging-iscsi-io-error.md) for an example.

### ISCSIKernelError (warning)

```logql
sum(count_over_time({job="talos-kernel"} |~ "(?i)(iscsid|iscsi).*(error|failed|timeout|lost)" [5m])) > 0
```

Fires after 2 minutes when iSCSI session errors appear in kernel logs. May
precede a BTRFS corruption event or indicate a storage connectivity issue.

## 5. Querying Logs

Once Vector is deployed and Talos MachineConfig is patched, logs appear in the
`talos-kernel` job in Loki.

```bash
# All kernel logs from the last hour
logcli query '{job="talos-kernel"}' --limit=50

# BTRFS errors only
logcli query '{job="talos-kernel"} |= "btrfs"'

# Logs from a specific service (e.g. kubelet)
logcli query '{job="talos-kernel", talos_service="kubelet"}'

# Via Grafana: Explore → Loki → {job="talos-kernel"}
```

## 6. Testing

### Verify Vector is running and receiving logs

```bash
# Should show DaemonSet pods in Running state
kubectl get pods -n monitoring -l talos-log-receiver=true

# Check for incoming events (look for "talos_logs" source stats)
kubectl logs -n monitoring -l talos-log-receiver=true --tail=30
```

### Verify logs flow into Loki

```bash
# Port-forward Loki
kubectl port-forward -n monitoring svc/loki 3100:3100

# Query via logcli
logcli query '{job="talos-kernel"}' --limit=5 --addr=http://localhost:3100
```

### Verify Loki ruler loaded the rules

```bash
kubectl port-forward -n monitoring svc/loki 3100:3100
curl -s http://localhost:3100/loki/api/v1/rules | python3 -m json.tool
```

Expected output includes `BTRFSCorruptionDetected` and `ISCSIKernelError`.

## 7. Troubleshooting

### No logs appearing in Loki

1. Check Talos MachineConfig has `machine.logging` set:
   ```bash
   talosctl -n talos-519-vmy get machineconfig -o yaml | grep -A5 logging
   ```
2. Check the NodePort is reachable from the node:
   ```bash
   talosctl -n talos-519-vmy exec -- nc -zv 192.168.5.1 30600
   ```
3. Check Vector pod logs for connection errors:
   ```bash
   kubectl logs -n monitoring -l talos-log-receiver=true | grep -i error
   ```

### Loki ruler not evaluating rules

1. Confirm the ConfigMap is mounted:
   ```bash
   kubectl exec -n monitoring deploy/loki -- ls /var/loki/rules/fake/
   ```
2. Check Loki logs for ruler errors:
   ```bash
   kubectl logs -n monitoring sts/loki | grep -i ruler
   ```
3. Verify the ruler API reports the loaded rules (see Testing section above).

### Adding a new Talos node

When a new Talos node joins the cluster:

1. Patch its MachineConfig to add logging destinations (same command, different `-n` target).
2. The Vector DaemonSet will automatically schedule a pod on the new node.
3. No Loki/Alertmanager changes are needed.
