# Talos Kernel Log Shipping

> **Status (2026-11-14): this pipeline never worked, from install (2026-03-05)
> until the `role: Agent` fix.** Two separate faults, each enough on its own to
> stop it:
>
> 1. **No Vector workload.** `values.yaml` set `role: DaemonSet`. The chart
>    accepts only `Agent` (DaemonSet), `Aggregator` (StatefulSet) or
>    `Stateless-Aggregator` (Deployment). Any other value renders a
>    ServiceAccount, a ConfigMap and two Services and **no workload**. The chart
>    does not fail and the HelmRelease reported `Ready` throughout. The vector
>    Services had no endpoints, and Loki held no `{job="talos-kernel"}` stream.
> 2. **Talos was never pointed at it.** On 2026-11-14 none of the four nodes
>    had `machine.logging` set, a `KmsgLogConfig` document, or a
>    `talos.logging.kernel` kernel argument. The patch in
>    [§3](#talos-machineconfig) has to be applied by an operator. Merging the
>    repo change does not do it.
>
> So `BTRFSCorruptionDetected` and `ISCSIKernelError` were inactive for eight
> months with no data behind them, and that looked the same as "no errors".
> Three alerts now make this failure visible: `VectorAgentNotReady`,
> `TalosLogsAbsent` and `TalosKernelLogsAbsent` ([§4](#4-alerting-rules)).
> **`TalosLogsAbsent` fires from the moment it loads until the Talos patch is
> applied**, and `TalosKernelLogsAbsent` fires about a day later.
>
> **2026-11-15: working.** The [§3](#talos-machineconfig) patch is applied on
> all four nodes. Each one sends 7,000–10,500 lines per 5 minutes, kernel lines
> included, and both absent alerts are inactive.

## 1. Overview

Talos nodes produce kernel (`dmesg`) and service logs that Promtail's container
log scraping does not capture. This pipeline sends them to Loki through a
dedicated **Vector** DaemonSet. That allows log-based alerting for failure modes
that produce no Prometheus metric (e.g. BTRFS corruption in state `EA`, iSCSI
session errors).

## 2. Architecture

```
Talos node <ip> (host network)
  ├─ machine.logging.destinations  tcp://<ip>:30600   service logs (json_lines)
  └─ KmsgLogConfig                 tcp://<ip>:30600   kernel logs (json)
        │
        ▼  NodePort 30600, externalTrafficPolicy: Local
Vector DaemonSet pod on that node (monitoring namespace)
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

**Two Talos settings, not one.** `machine.logging.destinations` carries Talos
*service* logs (machined, kubelet, etcd, …). Kernel messages, which include
every BTRFS and iSCSI error the alerts look for, are a separate stream: a
`KmsgLogConfig` document or the `talos.logging.kernel` kernel argument. An
earlier version of this page described `machine.logging` alone as shipping
kernel logs. It does not.

### Why Vector, not Promtail?

Talos sends log events as **newline-delimited JSON**, not RFC 5424 syslog.
Promtail's syslog receiver expects RFC 5424 framing. Vector has a native
`socket` source with `decoding.codec: json` that reads this format.

### Why a NodePort?

Talos runs on the host network, outside the pod network. A NodePort is the
simplest way to reach a pod from the host. NodePort `30600` maps to Vector's TCP
socket on port `6000`.

`externalTrafficPolicy: Local` sends a connection to `<node-ip>:30600` only to
the Vector pod on that node. So **each node must send to its own IP**, and that
node must have a ready Vector pod. An earlier version of this page pointed every
node at `10.42.2.20`. With `Local`, that would have sent all four nodes' logs to
one pod and lost them whenever that node was down.

Vector records the peer address in each event's `host` field (in the JSON body).
It is **not** a Loki label: the only labels are `job`, `talos_service` and
`level`.

## 3. Configuration

### Manifests

| File | Purpose |
|---|---|
| `infra/controllers/vector/repository.yaml` | HelmRepository for `https://helm.vector.dev` |
| `infra/controllers/vector/release.yaml` | HelmRelease: `vector/vector` chart 0.50.0 |
| `infra/controllers/vector/values.yaml` | `role: Agent` (DaemonSet), trimmed defaults, and the full Vector pipeline |
| `infra/controllers/vector/nodeport.yaml` | `Service` type NodePort, port 30600 → 6000, selects `talos-log-receiver: "true"` |
| `infra/controllers/loki/alerting-rules.yaml` | Loki ruler rules ConfigMap (`BTRFSCorruptionDetected`, `ISCSIKernelError`, `TalosLogsAbsent`, `TalosKernelLogsAbsent`) |
| `infra/controllers/loki/values.yaml` | `singleBinary.rulerConfig` and rules volume mount |
| `infra/configs/alerts/loki-rules.yaml` | PrometheusRule `VectorAgentNotReady` |

### What the `Agent` role renders

`helm template` at chart 0.50.0 with these values renders a ServiceAccount, the
`vector` ConfigMap, two ClusterIP Services and a DaemonSet. The Agent role is
built to scrape container logs from the host. Its defaults are switched off
because this pipeline only listens on a socket:

| Agent default | Here | Why |
|---|---|---|
| hostPath mounts `/var/log`, `/var/lib`, `/proc`, `/sys` | `defaultVolumes: []`, `defaultVolumeMounts: []` | Only the `kubernetes_logs` / host-metrics sources read them |
| ClusterRole + Binding (list/watch pods, nodes, namespaces) | `rbac.create: false` | Only `kubernetes_logs` needs it |
| Data dir on hostPath `/var/lib/vector` | `persistence.hostPath.enabled: false` → emptyDir | The socket source keeps no checkpoints; the Loki sink buffers in memory |
| Default `vector.yaml` (kubernetes_logs, host_metrics, …) | replaced entirely by `customConfig` | The rendered ConfigMap contains only the pipeline below |

The pod has no hostPath volumes, so Talos's read-only root filesystem does not
affect it. It runs to the `restricted` Pod Security profile: uid 65534,
`runAsNonRoot`, read-only root filesystem, all capabilities dropped,
`allowPrivilegeEscalation: false`, `RuntimeDefault` seccomp, and no
ServiceAccount token. The `monitoring` namespace enforces the more permissive
`privileged` level, so admission does not depend on any of this. The chart's
Agent branch still sets `PROCFS_ROOT` / `SYSFS_ROOT` env vars. They point at
paths that are no longer mounted and nothing in this config reads them.

The DaemonSet tolerates every `NoSchedule` taint, so a tainted node still ships
its logs. A custom `NoExecute` taint would still evict the pod, and
`VectorAgentNotReady` would then fire. Its `readinessProbe` is a TCP check on
port 6000, so a pod counts as ready once the socket is listening.

### Talos MachineConfig

Apply this to **each** node, with that node's own IP in both places. Nodes as of
2026-11-14: `10.42.2.20` (talos-ykb-uir), `10.42.2.21` (talos-2mz-rfj),
`10.42.2.23` (talos-fpd-h0t), `10.42.2.25` (talos-kot-7x7). Apply it **after**
the Vector DaemonSet is ready.

```yaml
# vector-logging-<ip>.yaml
machine:
  logging:
    destinations:
      - endpoint: tcp://<ip>:30600/
        format: json_lines
---
apiVersion: v1alpha1
kind: KmsgLogConfig
name: vector
url: tcp://<ip>:30600/
```

```bash
talosctl -n <ip> patch mc --patch @vector-logging-<ip>.yaml --dry-run   # review the diff
talosctl -n <ip> patch mc --patch @vector-logging-<ip>.yaml
```

Whether either setting applies without a reboot on v1.12 was **not verified**
(the `--dry-run` output reports the apply mode). Confirm after applying with
`talosctl -n <ip> get kmsglogconfigs` and a `{job="talos-kernel",
talos_service="kernel"}` query. The `talos.logging.kernel` kernel argument also
works, but `extraKernelArgs` only take effect on a Talos upgrade.

When a node joins the cluster, apply the same patch to it with its own IP.

### Vector pipeline (values.yaml summary)

Talos field names, from the Talos logging docs:

- **Service log:** `msg`, `talos-level`, `talos-service`, `talos-time`
- **Kernel log:** `msg`, `talos-level`, `talos-time`, `facility`, `priority`,
  `seq`, `clock`. There is no `talos-service` field.

```yaml
sources:
  talos_logs:
    type: socket
    mode: tcp
    address: "0.0.0.0:6000"
    framing.method: newline_delimited
    decoding.codec: json

transforms:
  normalize_talos:
    type: remap
    source: |
      .talos_service = string(del(."talos-service")) ?? "kernel"   # kmsg → "kernel"
      .level = string(del(."talos-level")) ?? string(.level) ?? "info"
      .msg = string(.msg) ?? ""
      .node = get_env_var("VECTOR_SELF_NODE_NAME") ?? "unknown"
      ts, err = parse_timestamp(string(."talos-time") ?? "", "%+")
      if err == null { .timestamp = ts; del(."talos-time") }

sinks:
  loki_output:
    type: loki
    labels:
      job: talos-kernel
      talos_service: "{{ talos_service }}"
      level: "{{ level }}"
      node: "{{ node }}"
  prom_exporter:            # Vector's own metrics, for TalosNodeLogsSilent
    type: prometheus_exporter
    address: "0.0.0.0:9598"
```

**`node` label.** Each node sends to its own IP, and the NodePort's
`externalTrafficPolicy: Local` routes that only to the Vector pod on the same
node, so the receiving pod's node (`VECTOR_SELF_NODE_NAME`, set by the chart from
`spec.nodeName`) is the sender. Before this, the only clue was the `host` field
inside each line, which holds the node's pod-network address (`10.244.<n>.x`),
not its node IP.

**Timestamps.** Loki's timestamp is the time Talos logged the line
(`talos-time`), not the time Vector received it. This matters because Talos
sends its in-memory log buffer from boot when a destination first connects. On
2026-11-15, applying the patch replayed old service lines with lags of up to
about 149 days, and old kernel lines up to about 17 days. Stamped on arrival, a
replayed old iscsid error made `ISCSIKernelError` fire. Loki rejects lines too
far behind its out-of-order window and keeps the rest of the batch, which is the
right result for a replay. **Extension services such as `ext-iscsid` are the
exception.** They log plain stdout with no time of their own, so Talos stamps
those lines when it sends them, and a replay still looks new. Checked locally on
`timberio/vector:0.53.0-distroless-libc`: a line with an old `talos-time` keeps
that time, and a line with a missing or invalid `talos-time` falls back to
arrival time. Either way `node` is set.

The transform used to read `.level`, which Talos does not send, so every line
would have been labelled `info`. The pipeline was checked on 2026-11-14 by
running `timberio/vector:0.53.0-distroless-libc` locally on the rendered config
(Loki sink swapped for a console sink) and sending a sample of each event type:

```text
service → talos_service="machined", level="info"
kmsg    → talos_service="kernel",   level="error"
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

As of 2026-11-14 the ruler loads the rules (`health: ok`) and has discovered one
Alertmanager, but `loki_prometheus_notifications_sent_total` is 0: it has never
sent an alert. `TalosLogsAbsent` will be the first, which is a useful end-to-end
check of that path.

## 4. Alerting Rules

### BTRFSCorruptionDetected (critical, Loki ruler)

```logql
sum(count_over_time({job="talos-kernel"} |= "parent transid verify failed" [5m])) > 0
```

Fires immediately (`for: 0m`) when BTRFS `parent transid verify failed` appears
in kernel logs. This indicates an iSCSI volume serving stale data after a
session reconnect. The filesystem stays mounted **read-write** in error state
`EA`, so `NodeFilesystemReadOnly` does **not** fire for this failure mode.
**Needs the `KmsgLogConfig` half of the Talos patch.**

**Response**: identify the affected device via `talosctl dmesg`, map it to a
PVC, and recycle the pod and PVC. See the [2026-02-27 incident](../operations/incidents/2026-02-27-homeassistant-staging-iscsi-io-error.md) for an example.

### ISCSIKernelError (warning, Loki ruler)

```logql
sum(count_over_time({job="talos-kernel"} |~ "(?i)(iscsid|iscsi).*(error|failed|timeout|lost)" [5m])) > 0
```

Fires after 2 minutes when iSCSI session errors appear in the logs. These may
come before a BTRFS corruption event or point to a storage connectivity problem.

### TalosLogsAbsent (critical, Loki ruler)

```logql
absent_over_time({job="talos-kernel"}[1h])
```

`for: 5m`. A healthy node sends service logs constantly, so an hour with no line
from any node means the pipeline is broken. The two alerts above evaluate to
nothing in that state. Checked live 2026-11-14: returns `1` for
`{job="talos-kernel"}` and nothing for a present stream (`{job="monitoring/grafana"}`).
It is cluster-wide: other nodes' lines satisfy it, so it does not catch a single
node that has stopped sending. `TalosNodeLogsSilent` (below) covers that case.

### TalosKernelLogsAbsent (warning, Loki ruler)

```logql
absent_over_time({job="talos-kernel", talos_service="kernel"}[24h])
```

`for: 30m`. `TalosLogsAbsent` is satisfied by service logs alone. This alert
covers the kernel half, which is the half the BTRFS and iSCSI alerts actually
match. If only `machine.logging` is applied, or the `KmsgLogConfig` is lost,
this is the alert that fires. **The 24h window is a guess.** No kernel stream
existed to measure when it was written. Calibrate it against a few days of real
data, then tighten it or raise it to critical.

### VectorAgentNotReady (critical, Prometheus)

```promql
kube_daemonset_status_number_ready{namespace="monitoring", daemonset="vector"}
  < on() group_left() count(kube_node_info)
or absent(kube_daemonset_status_number_ready{namespace="monitoring", daemonset="vector"})
```

`for: 15m`. Ready pods are compared with the node count, not with
`desiredNumberScheduled`, so a DaemonSet kept off a node still counts as short.
`absent()` covers the pre-fix state, where no DaemonSet existed. Checked live
2026-11-14: returns 1 for `vector` (absent) and nothing for `loki-canary` (4/4).
With `>=` it returns 4 for `loki-canary`, and with one ready pod subtracted it
returns 3, so the join binds.

### TalosNodeLogsSilent (critical, Prometheus)

```promql
count by (node) (
  kube_pod_info{namespace="monitoring", created_by_kind="DaemonSet", created_by_name="vector"}
)
unless on (node)
sum by (node) (
  rate(vector_component_received_events_total{namespace="monitoring", component_id="talos_logs"}[30m]) > 0
)
```

`for: 15m`. Fires for each node whose Vector pod received no Talos line in 30
minutes. It uses Vector's own counters, scraped through the chart's PodMonitor,
whose relabeling sets `node`. Loki can't do this check itself: a Loki rule has
no list of nodes to compare against, and a "seen recently" window stops firing
once the node has been quiet for longer than the window. The rule starts from
`kube_pod_info` because Vector creates the counter only at its first event.
Without that, a node that never sends after a pod restart would have no series,
and `== 0` would miss it. If every node fires at once, check the scrape
(`up{job="vector"}`) before the nodes. Unit-tested in
`scripts/prom-rules-test/talos-node-logs.test.yaml`.

## 5. Querying Logs

```bash
# All Talos logs from the last hour
logcli query '{job="talos-kernel"}' --limit=50

# Kernel messages only
logcli query '{job="talos-kernel", talos_service="kernel"}'

# BTRFS errors only
logcli query '{job="talos-kernel"} |= "btrfs"'

# One service (e.g. kubelet)
logcli query '{job="talos-kernel", talos_service="kubelet"}'

# One node
logcli query '{job="talos-kernel", node="talos-kot-7x7"}'

# Via Grafana: Explore → Loki → {job="talos-kernel"}
```

## 6. Testing

### Verify the workload rendered

```bash
helm -n monitoring get manifest vector | grep '^kind:'      # must include DaemonSet
kubectl -n monitoring get ds vector                         # READY == node count
kubectl -n monitoring get pods -l talos-log-receiver=true -o wide
kubectl -n monitoring get endpointslices -l kubernetes.io/service-name=vector-talos-logs
```

### Verify logs flow into Loki

```bash
kubectl -n monitoring exec loki-0 -c loki -- \
  wget -qO- 'http://localhost:3100/loki/api/v1/query?query=count_over_time(%7Bjob%3D%22talos-kernel%22%7D%5B5m%5D)'
```

### Verify Loki ruler loaded the rules

```bash
kubectl -n monitoring exec loki-0 -c loki -- wget -qO- http://localhost:3100/prometheus/api/v1/rules
```

Expected output includes `BTRFSCorruptionDetected`, `ISCSIKernelError`,
`TalosLogsAbsent` and `TalosKernelLogsAbsent`.

## 7. Troubleshooting

### No logs appearing in Loki

1. Check that the DaemonSet exists and is ready on every node (§6). If
   `helm get manifest` has no DaemonSet, check `role:` in `values.yaml`. It must
   be `Agent`.
2. Check each node's Talos logging config:
   ```bash
   talosctl -n <ip> get machineconfig -o yaml | grep -A4 'logging:'
   talosctl -n <ip> get kmsglogconfigs
   ```
3. Check Vector pod logs for socket or Loki sink errors:
   ```bash
   kubectl -n monitoring logs -l talos-log-receiver=true --tail=50 | grep -iE 'error|warn'
   ```

### Loki ruler not evaluating rules

1. Confirm the ConfigMap is mounted:
   ```bash
   kubectl -n monitoring exec loki-0 -c loki -- ls /var/loki/rules/fake/
   ```
2. Check Loki logs for ruler errors:
   ```bash
   kubectl -n monitoring logs loki-0 -c loki | grep -i ruler
   ```
3. Verify the ruler API reports the loaded rules (§6).

### Adding a Talos node

1. Apply the [§3 patch](#talos-machineconfig) with the new node's own IP.
2. The Vector DaemonSet schedules a pod on the new node automatically.
3. `VectorAgentNotReady` compares against the node count, so it fires if the
   new node does not get a ready pod.
