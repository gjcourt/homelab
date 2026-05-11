# Netscope

## 1. Overview
Netscope is a custom eBPF-based network traffic analyzer running as a DaemonSet across every node in the homelab cluster (3 control-plane + 3 worker = 6 pods total). It exposes per-node Prometheus metrics for traffic depth signals that Cilium's Hubble does not surface natively:

- **rx bytes** at the primary interface (tcx ingress hook)
- **TCP retransmits** (fentry on `tcp_retransmit_skb`)
- **TCP smoothed RTT** as a histogram (fentry on `tcp_rcv_established`)
- **DNS query latency** as a histogram (fentry `udp_sendmsg` / fexit `udp_recvmsg` pair)

Source: https://github.com/gjcourt/netscope. Project plan: https://github.com/gjcourt/brainstorm/blob/main/03-homelab-automation/03-001-ebpf-based-network-traffic-analyzer.md.

The homelab is a single-cluster environment with no separate production environment for netscope; everything ships into the `netscope-stage` namespace and that is the operational target.

## 2. Architecture
Netscope is deployed as a single Kubernetes `DaemonSet` (`netscope-agent`) in the `netscope-stage` namespace, scheduled on every node (control-plane tolerations included).

- **Image**: `ghcr.io/gjcourt/netscope`, pinned by tag + digest in `apps/base/netscope/daemonset.yaml`.
- **Network**: `hostNetwork: true` so the agent observes the host's primary NIC, not the pod network namespace. The metrics port (9101) is exposed as a `hostPort` so each node is independently scrapeable.
- **eBPF programs**: five programs attached at startup against the IPv4 default-route interface (discovered from `/proc/net/route`, override via `NETSCOPE_IFACE`):
  - `tcx ingress` — rx byte counter, anchored after Cilium's tcx programs via `bpf(BPF_PROG_GET_NEXT_ID)` discovery
  - fentry `tcp_retransmit_skb` — retransmit counter
  - fentry `tcp_rcv_established` — SRTT histogram (microsecond resolution, exported in seconds)
  - fentry `udp_sendmsg` — DNS query start timestamp
  - fexit `udp_recvmsg` — DNS query latency histogram (matches query on 5-tuple + transaction ID)
- **Metrics**: exposed at `:9101/metrics` on each host, scraped via `ServiceMonitor` labeled `release: kube-prometheus-stack`. Service is headless (`clusterIP: None`) so Endpoints carry host IPs.
- **No persistent storage.** All state is in BPF maps; pod restart drops counters and they begin again from zero.

```
┌────────────────────────────────────────────────┐
│ Node (×6: 3 CP + 3 worker)                     │
│                                                │
│  ┌───────────────┐    attach    ┌───────────┐  │
│  │ netscope-     │─────────────▶│ kernel    │  │
│  │ agent (pod)   │              │  tcx rx   │  │
│  │ hostNetwork   │              │  fentry×3 │  │
│  │ UID 0 / BPF + │              │  fexit×1  │  │
│  │ PERFMON +     │◀─────────────│           │  │
│  │ NET_ADMIN +   │  BPF maps    └───────────┘  │
│  │ SYS_ADMIN     │                             │
│  └───────┬───────┘                             │
│          │ :9101/metrics (hostPort)            │
└──────────┼─────────────────────────────────────┘
           │
           ▼
   ┌───────────────┐       ┌─────────────┐
   │ Prometheus    │──────▶│ Grafana     │
   │ (kps)         │       │ dashboard   │
   └───────────────┘       └─────────────┘
```

## 3. URLs
- **Dashboard**: https://grafana.burntbytes.com/d/netscope-overview
- **Prometheus targets**: https://prometheus.burntbytes.com/targets?search=netscope
- **Source**: https://github.com/gjcourt/netscope
- **Project plan / brainstorm**: https://github.com/gjcourt/brainstorm/blob/main/03-homelab-automation/03-001-ebpf-based-network-traffic-analyzer.md
- **SRTT verifier postmortem**: https://github.com/gjcourt/netscope/blob/main/docs/postmortems/2026-05-10-srtt-verifier-iterations.md

## 4. Configuration
- **Image**: pinned by tag + digest in `apps/base/netscope/daemonset.yaml` (currently `:2e20747@sha256:75d159d...`). Bump the value there; CI tag convention is `<git-short-sha>` so the digest must be updated in lockstep. Image tags are strictly increasing per the repo invariant.
- **Environment variables**:
  - `NETSCOPE_IFACE` — interface to attach BPF programs to. **Intentionally unset.** The agent discovers the IPv4 default-route interface from `/proc/net/route` at startup. Set explicitly (e.g. `value: "eno1"`) to pin to a specific NIC and skip discovery; only useful on a node where the default-route NIC is wrong for observability purposes.
- **Linux capabilities** (`add`): `BPF`, `PERFMON`, `NET_ADMIN`, `SYS_ADMIN`. `ALL` is dropped first. `SYS_ADMIN` is the unfortunate one — it's required for `bpf(BPF_PROG_GET_NEXT_ID)` discovery so the agent can locate Cilium's ingress program and anchor tcx after it.
- **Security context**: `privileged: false`, `allowPrivilegeEscalation: false`, `runAsUser: 0`, `seccompProfile: RuntimeDefault`. UID 0 is required because Talos does not expose ambient capabilities; eBPF tcx attach and BPF program discovery need root.
- **Volumes**: `hostPath` mounts for `/sys/fs/bpf` (BPF filesystem, `HostToContainer` propagation so pinned programs survive pod restart) and `/sys/kernel/btf` (read-only, for CO-RE relocation).
- **Probes**: `readinessProbe` and `livenessProbe` both hit `127.0.0.1:9101/healthz`. The liveness probe has a 30s initial delay and 6-failure threshold because BPF attach can take several seconds on a cold start.
- **ConfigMaps/Secrets**: none. The agent has no runtime configuration outside env vars.

### Updating the image
1. Find the new tag + digest (the upstream CI prints both on each successful build of `gjcourt/netscope` main).
2. Edit `apps/base/netscope/daemonset.yaml`, update the `image:` field with both `:tag@sha256:digest` parts.
3. `kustomize build apps/staging/netscope` must pass.
4. PR, merge, Flux reconciles within 10 minutes.

## 5. Usage Instructions
Netscope has no UI. It is observed through Prometheus and Grafana.

### Inspect metrics live on a single node
```bash
# Pick a node and exec into its agent pod
kubectl -n netscope-stage get pods -o wide
kubectl -n netscope-stage exec <pod> -- wget -qO- http://127.0.0.1:9101/metrics | head -50
```

### Useful PromQL
```promql
# Cluster-wide ingress bytes/sec (sanity check that all 6 pods publish)
sum(rate(netscope_rx_bytes_total[5m]))

# Per-node ingress bytes/sec
sum by (nodename) (rate(netscope_rx_bytes_total[5m]))

# TCP retransmits per node per second
sum by (nodename) (rate(netscope_tcp_retransmits_total[5m]))

# 24h retransmit budget (cluster-wide)
sum(increase(netscope_tcp_retransmits_total[24h]))

# TCP SRTT p50 / p95 / p99 (cluster-wide, seconds)
histogram_quantile(0.50, sum(rate(netscope_tcp_srtt_microseconds_bucket[5m])) by (le))
histogram_quantile(0.95, sum(rate(netscope_tcp_srtt_microseconds_bucket[5m])) by (le))
histogram_quantile(0.99, sum(rate(netscope_tcp_srtt_microseconds_bucket[5m])) by (le))

# DNS query rate per node
sum by (nodename) (rate(netscope_dns_query_microseconds_count[5m]))

# DNS query latency p95 / p99 (cluster-wide, seconds)
histogram_quantile(0.95, sum(rate(netscope_dns_query_microseconds_bucket[5m])) by (le))
histogram_quantile(0.99, sum(rate(netscope_dns_query_microseconds_bucket[5m])) by (le))
```

### Metric reference
| Metric | Type | Meaning |
|---|---|---|
| `netscope_rx_bytes_total` | counter | Bytes received at tcx ingress on the node's primary interface |
| `netscope_tcp_retransmits_total` | counter | Calls to `tcp_retransmit_skb` (one per retransmitted segment) |
| `netscope_tcp_srtt_microseconds_bucket` | histogram | TCP smoothed RTT samples, recorded each time `tcp_rcv_established` fires |
| `netscope_dns_query_microseconds_bucket` | histogram | DNS query→response wall-clock latency observed at `udp_sendmsg` / `udp_recvmsg` |

All metrics are labeled with `nodename` (promoted from `__meta_kubernetes_pod_node_name` in the `ServiceMonitor`). The per-pod `pod` label is dropped to keep cardinality flat — under hostNetwork the pod identity is the node, and pod names churn on restart.

## 6. Testing
1. **All 6 pods Ready**: `kubectl get ds netscope-agent -n netscope-stage` — `DESIRED == CURRENT == READY == 6`.
2. **Targets up in Prometheus**: open the targets page and confirm 6 scrape endpoints with `up == 1`.
3. **Metric freshness**: run `sum(rate(netscope_rx_bytes_total[1m]))` and confirm a non-zero result. Idle clusters still see Cilium VXLAN and kube-apiserver traffic, so this should never be zero.
4. **Dashboard renders**: the netscope-overview dashboard shows six lines on the per-node panels.

## 7. Monitoring & Alerting
- **Metrics**: scraped every 30s via `ServiceMonitor` (`release: kube-prometheus-stack`). The headless Service backs per-host endpoints.
- **Dashboard**: https://grafana.burntbytes.com/d/netscope-overview — overview, retransmits, SRTT heatmap, DNS latency heatmap.
- **Alerts**: `PrometheusRule` lives in `infra/configs/prometheus/netscope-rules.yaml` (added in a parallel PR — see PRs labeled `feat/netscope-alerts`). Alerts cover at minimum:
  - `NetscopeAgentDown` — fewer than 6 scrape targets `up` for 5 minutes
  - `NetscopeTCPRetransmitSpike` — sustained per-node retransmit rate above baseline
  - `NetscopeDNSLatencyHigh` — DNS p99 latency exceeds threshold
  - Each alert sets `runbook_url` to the troubleshooting section of this document.
- **Logs**: `kubectl logs -n netscope-stage ds/netscope-agent --all-pods=true --prefix=true | head -100`. Useful at startup to confirm "attached tcx ingress" / "attached fentry ..." lines.

## 8. Disaster Recovery
There is no data to restore — the agent is stateless. Recovery means getting the DaemonSet back to healthy.

### Rolling back the image
1. Find the prior digest in `git log -- apps/base/netscope/daemonset.yaml`.
2. Open a rollback PR that re-pins the image to the older tag + digest. Image tags must be strictly increasing per the repo invariant, **so a rollback is an explicit-intent PR, not a silent revert** — call it out in the title (`fix(netscope): roll back image to <sha>`).
3. Merge; Flux reconciles. Verify all 6 pods are Ready and metrics resume.

### Disabling the DaemonSet
`kubectl scale ds/netscope-agent --replicas=0` **does not work** — DaemonSets have no `replicas` field. To take the agent offline without removing it from Git:

```bash
# Make the node selector unmatchable so no pods schedule
kubectl -n netscope-stage patch ds netscope-agent --type=strategic \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"netscope/disabled":"true"}}}}}'
```

Existing pods are terminated. To re-enable, remove the patch (Flux will restore the manifest on its next reconcile, or `kubectl edit` it back). Alternatively, delete the DaemonSet entirely (`kubectl delete ds netscope-agent -n netscope-stage`) — Flux recreates it on the next reconcile cycle (≤10 min). Use deletion if you need an immediate stop and don't mind Flux fighting you on the next cycle (suspend the Kustomization with `flux suspend kustomization apps-staging` if you need a longer outage).

### Kernel upgrade breaks BPF program load
If a Talos upgrade ships a kernel version where one or more BPF programs fail the verifier, **all 6 pods will CrashLoopBackOff simultaneously** (the agent fails closed during attach).

1. Disable the DaemonSet (above) so it stops crash-looping.
2. File an issue against `gjcourt/netscope` with the verifier log from `kubectl logs <pod> -p`.
3. Fix forward in the netscope repo (verifier rewrites, helper-based fallback, etc. — see the SRTT iteration postmortem for the canonical example). Ship a new image, bump in `apps/base/netscope/daemonset.yaml`, re-enable.

Do not pin Talos back to dodge the issue. The agent is the broken thing, not the kernel.

## 9. Troubleshooting

### Pod CrashLoopBackOff
1. `kubectl -n netscope-stage logs <pod> -p` (previous container). Look for `BPF program load failed` / `verifier rejected` / `permission denied`.
2. If it's a verifier rejection, this is the postmortem path — read https://github.com/gjcourt/netscope/blob/main/docs/postmortems/2026-05-10-srtt-verifier-iterations.md. The SRTT probe has gone through three verifier iterations; the known traps (unbounded loops, signed-bounds inference loss across helpers, register-spill width inference) are catalogued there. Fix forward in the netscope repo.
3. If it's `permission denied` on the BPF syscall, recheck capabilities — `BPF`, `PERFMON`, `NET_ADMIN`, `SYS_ADMIN` must all be present, and `runAsUser: 0`.
4. If it's `failed to mount bpffs`, the `/sys/fs/bpf` hostPath mount is wrong — verify `mountPropagation: HostToContainer`.

### Metric absent from Prometheus
1. **ServiceMonitor labeled correctly?** Must have `release: kube-prometheus-stack`. Without it, kube-prometheus-stack's Prometheus instance silently ignores the SM.
   ```bash
   kubectl -n netscope-stage get servicemonitor netscope-agent -o yaml | grep -A2 labels:
   ```
2. **Service Endpoints populated?** The Service is headless and selects DaemonSet pods.
   ```bash
   kubectl -n netscope-stage get endpoints netscope-agent
   # Expect 6 addresses (host IPs, not pod IPs).
   ```
3. **Prometheus target page**: filter for `netscope`. State should be `UP` for all six. A `down` state with `connection refused` means the agent crashed or the hostPort isn't bound. A `down` state with `context deadline exceeded` usually means the host firewall (Cilium HostFW / Talos) is blocking 9101.
4. **Metric exists but no labels**: confirm the `relabelings` block in `apps/base/netscope/servicemonitor.yaml` is intact — it promotes `__meta_kubernetes_pod_node_name` to `nodename`.

### Counter stuck at 0 (rx_bytes or retransmits)
This is almost always a **tcx attach-order problem** with Cilium. tcx programs run in attach order on the same hook; if netscope attaches *before* Cilium and Cilium drops or redirects the packet, netscope never sees it.

1. From any node, dump the tcx program chain on the primary interface:
   ```bash
   kubectl -n kube-system exec ds/cilium -- bpftool net show dev eno1
   ```
   Look at the `tcx/ingress` section. The expected order is **Cilium first, netscope after**. The agent uses `BPF_PROG_GET_NEXT_ID` discovery at startup to anchor itself after Cilium's program, so the wrong order means discovery failed.
2. If netscope is listed first or missing entirely, delete the netscope pod on that node so it re-attaches:
   ```bash
   kubectl -n netscope-stage delete pod -l app.kubernetes.io/name=netscope --field-selector spec.nodeName=<node>
   ```
3. **Anchor any Cilium-side network debugging before blaming netscope.** Cilium owns the tcx hook order, and an upgrade can rewrite the chain. Check the Cilium HelmRelease for recent reconciles before assuming netscope regressed.

### Counter still stuck at 0 after the attach-order check
Confirm the interface the agent picked. The default-route interface (`ip route show default`) should match what the agent attached to:

```bash
NODE=<node>
kubectl -n netscope-stage logs ds/netscope-agent --all-pods=true --prefix=true \
  | grep -E "attached|iface" | grep "$NODE"
```

If the agent picked the wrong NIC (e.g. a tailscale interface on a node that has one), set `NETSCOPE_IFACE` explicitly in a staging overlay patch.

### Histogram quantiles look wrong (SRTT or DNS)
Histogram buckets are denominated in **seconds**, not microseconds, despite the metric suffix. The eBPF probe samples in microseconds and the collector converts before bucketing — this is the Prometheus convention. If you're computing latency in PromQL, do not multiply by 1e6.

## 10. Related Documents
- SRTT verifier iteration postmortem (canonical eBPF verifier debugging story for this project): https://github.com/gjcourt/netscope/blob/main/docs/postmortems/2026-05-10-srtt-verifier-iterations.md
- Brainstorm / project plan: https://github.com/gjcourt/brainstorm/blob/main/03-homelab-automation/03-001-ebpf-based-network-traffic-analyzer.md
- Source repo: https://github.com/gjcourt/netscope
