# netscope (hestia)

Per-node network telemetry agent (eBPF / CO-RE / libbpf) for hestia, the
standalone TrueNAS box at `10.42.2.10`. hestia is **not** a Kubernetes node,
so it can't run the cluster `netscope-agent` DaemonSet — this compose runs the
same agent image as a privileged, host-network Docker container instead, and
Prometheus scrapes it directly (`infra/configs/netscope/scrapeconfig.yaml`).

| Attribute | Value |
|---|---|
| Image | `ghcr.io/gjcourt/netscope` (built from [`gjcourt/netscope`](https://github.com/gjcourt/netscope)) |
| Tag | pinned to the **same digest** the cluster DaemonSet uses (`apps/base/netscope/daemonset.yaml`) — bump both together |
| Tag scheme | git short SHA from the source repo (NOT the homelab YYYY-MM-DD convention) |
| Network | `host` (Prometheus scrapes hestia directly on `:9101`) |
| Privileges | `privileged: true` — eBPF tcx attach + BPF map/program pinning |
| Bind mounts | `/sys/fs/bpf` (rw, bpffs), `/sys/kernel/btf` (ro, CO-RE) |
| Job label | `netscope-agent` (forced by the ScrapeConfig relabeling, same job as cluster nodes) |

## Readiness (verified)

hestia is CO-RE-ready: kernel **6.18.13**, BTF present at
`/sys/kernel/btf/vmlinux`, Docker **29**. The libbpf agent will load and
relocate against the running kernel without a prebuilt BTF blob.

Unlike the cluster nodes, hestia runs **no Cilium** — there is no existing tcx
chain to anchor against. The agent attaches at the default-route NIC and
returns `TC_ACT_UNSPEC` (observe-only, never drops).

## Interface selection (multiple NICs)

The agent auto-discovers the IPv4 default-route interface from
`/proc/net/route` at startup. hestia has **multiple NICs** (ASRock
SIENAD8-2L2T). If discovery picks the wrong one, pin it explicitly via the
`NETSCOPE_IFACE` env in `docker-compose.yml`:

```yaml
    environment:
      NETSCOPE_IFACE: eno1
```

Find the right interface first:

```bash
ssh truenas_admin@10.42.2.10 'ip -o -4 route show default'
```

## Deploy

**Automatic (preferred):** once this lands on `master`, `deploy-hestia.yml`
auto-discovers `hosts/hestia/netscope/docker-compose.yml` and rolls it out to
the TrueNAS Apps subsystem via the self-hosted runner on hestia.

**Manual (operator, if deploying before/without the merge):**

```bash
ssh truenas_admin@10.42.2.10
docker compose -f /path/to/homelab/hosts/hestia/netscope/docker-compose.yml up -d
docker logs -f netscope    # confirm BTF load + chosen interface
curl -s localhost:9101/metrics | head   # confirm metrics on :9101
```

The Prometheus `netscope-agent` target for `instance=hestia` stays **DOWN**
until the container is running — that's expected, not a failure.

## Updating

When a new image is published from [`gjcourt/netscope`](https://github.com/gjcourt/netscope):

```bash
TAG=<new-short-sha>
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:gjcourt/netscope:pull" | jq -r .token)
curl -sI -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json,application/vnd.oci.image.manifest.v1+json" \
  "https://ghcr.io/v2/gjcourt/netscope/manifests/$TAG" \
  | grep -i 'docker-content-digest'
```

Bump the `image:` line here **and** `apps/base/netscope/daemonset.yaml` to the
same `:${TAG}@sha256:${digest}` so cluster and hestia stay in lockstep.
