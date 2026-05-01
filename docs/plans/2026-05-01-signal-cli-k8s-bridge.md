---
status: planned
last_modified: 2026-05-01
---

# Signal-cli K8s Deployment with SSE Bridge

Replace the TrueNAS `signal-cli-rest-api` container with a native Kubernetes deployment of `signal-cli` + a lightweight SSE bridge, so the **unmodified** Hermes Signal adapter works without any source code changes.

## Current State

| Component | Status |
|:----------|:-------|
| `signal-cli-rest-api` | Running on TrueNAS at `10.42.2.10:30295` (container `ix-signal-cli-rest-api-signal-cli-rest-api-1`) |
| Registered number | `+161****7251` |
| Hermes Signal adapter | Patched to REST polling (`GET /v1/receive/{account}`) instead of SSE |
| `signal-cli` k8s manifests | In PR #350 (`feat/signal-cli-k8s`), deploy raw `signal-cli` with `--tcp 0.0.0.0:7583` |
| TrueNAS compose file | Archived at `~/src/homelab/misc/docker-compose.yml` |

**Problem:** The Hermes adapter was patched to use REST polling because the original SSE adapter doesn't work with `signal-cli --tcp` on Talos (no D-Bus). Patching hermes source is fragile — changes are lost on every upgrade.

**Goal:** Deploy `signal-cli` on k8s with a bridge that provides the exact HTTP/SSE interface the **original, unmodified** Hermes adapter expects, so zero hermes source changes are needed.

## Target State

```
┌──────────────────────────────────────────────────────────────────────┐
│  Kubernetes (flux-managed, signal-cli namespace)                     │
│                                                                      │
│  ┌──────────────────┐    JSON-RPC     ┌──────────────────────────┐  │
│  │  signal-cli      │◄───────────────►│  SSE bridge              │  │
│  │  (tcp :7583)     │   (TCP)         │  (http :8080)            │  │
│  │                  │                 │                          │  │
│  │  --tcp 0.0.0.0   │                 │  GET /api/v1/events      │  │
│  │  --tcp-port 7583 │                 │    ?account={account}    │  │
│  │  --receive-mode  │                 │    Accept: text/event-   │  │
│  │  on-connection   │                 │    stream (SSE)          │  │
│  │  --ignore-stories│                 │                          │  │
│  │                  │                 │  POST /api/v1/rpc        │  │
│  │                  │                 │    (JSON-RPC 2.0)        │  │
│  │                  │                 │                          │  │
│  │                  │                 │  GET /v1/health          │  │
│  └──────────────────┘                 └──────────┬───────────────┘  │
│                                                  │                   │
│                                         ClusterIP :8080               │
└────────────────────────────────────────────────┼───────────────────┘
                                                 │
                                                 ▼
                                        signal-cli.signal-cli.svc:8080
                                                 │
                                                 ▼
                                        ┌──────────────────┐
                                        │  Hermes Agent    │
                                        │  (unmodified)    │
                                        │  signal.py       │
                                        └──────────────────┘
```

## Bridge Design

### Responsibilities

The bridge is a small Go service (single binary, ~150-200 lines) with three jobs:

1. **SSE endpoint** — `GET /api/v1/events?account={account}`
   - Accepts `Accept: text/event-stream`
   - Polls signal-cli's JSON-RPC `receive` method over TCP
   - Streams results as SSE events to the client
   - Implements exponential backoff on errors (matching Hermes' retry behavior)
   - Sends `retry:` SSE directive for auto-reconnect

2. **JSON-RPC relay** — `POST /api/v1/rpc`
   - Accepts JSON-RPC 2.0 requests (e.g., `signal.send`, `signal.getContacts`)
   - Forwards them to signal-cli over TCP
   - Returns the JSON-RPC response

3. **Health check** — `GET /v1/health`
   - Returns `200 OK` with `{ "status": "ok" }`
   - Optionally includes signal-cli connection status

### JSON-RPC Protocol

The bridge communicates with signal-cli using signal-cli's built-in JSON-RPC over TCP protocol. The same protocol that the `signal-cli-rest-api` image uses internally.

Key methods:
- `receive` — Get recent messages (poll-based)
- `send` — Send a message
- `getInfo` — Get account info (used for health check)
- `getContacts` — List contacts
- `getGroups` — List groups

### SSE Event Format

The bridge converts signal-cli JSON-RPC responses into SSE events:

```
event: message
data: {"envelope": {...}, "timestamp": 1234567890}

event: heartbeat
data: {"type": "heartbeat", "timestamp": 1234567890}
```

Heartbeats are sent every 30 seconds to keep the connection alive (prevents proxy/load balancer timeouts).

### Polling Strategy

The bridge polls signal-cli's `receive` method on a configurable interval (default: 2 seconds). This matches the original SSE adapter's behavior where signal-cli pushes events via SSE — the bridge synthesizes the same push model by polling the JSON-RPC endpoint.

Deduplication is handled by the Hermes adapter (sliding window of message timestamps), not the bridge.

## Components Required

### 1. signal-cli Deployment

**Image:** `ghcr.io/asamk/signal-cli:latest`

**Command:** `signal-cli daemon --tcp 0.0.0.0:7583 --receive-mode on-connection --ignore-stories`

**Volume:** PVC for config directory (`/var/lib/signal-cli`) — carry over from PR #350

**Ports:** TCP 7583 (JSON-RPC) — internal only, not exposed as a service

### 2. SSE Bridge Deployment

**Image:** `gjcourt/signal-bridge:latest` (to be built)

**Ports:** TCP 8080 (HTTP — SSE + JSON-RPC relay + health)

**Configuration:**
- `SIGNAL_CLI_HOST` — signal-cli service hostname (default: `signal-cli.signal-cli.svc.cluster.local`)
- `SIGNAL_CLI_PORT` — signal-cli JSON-RPC port (default: `7583`)
- `POLL_INTERVAL` — seconds between polls (default: `2`)
- `HEARTBEAT_INTERVAL` — seconds between heartbeats (default: `30`)

**Resources:**
- Requests: 25m CPU, 64Mi memory
- Limits: 100m CPU, 128Mi memory

### 3. Service

**Name:** `signal-cli-bridge`
**Type:** ClusterIP
**Port:** 8080 → 8080
**Selector:** `app: signal-cli-bridge`

### 4. Docker Image

Build a multi-arch (amd64 + arm64) Docker image for the bridge.

```dockerfile
FROM golang:1.23-bookworm AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o signal-bridge .

FROM scratch
COPY --from=builder /app/signal-bridge /signal-bridge
EXPOSE 8080
ENTRYPOINT ["/signal-bridge"]
```

## Implementation Phases

### Phase 1: Build the SSE bridge

Create `images/signal-bridge/` with:
- `Dockerfile` (multi-arch, scratch base)
- `main.go` — HTTP server with SSE, JSON-RPC relay, and health endpoints
- `go.mod` — minimal dependencies (`net/http`, `encoding/json`, `net`)
- `config.go` — environment variable configuration

### Phase 2: Update k8s manifests in PR #350

Replace the single-container deployment with a two-container deployment:
- `signal-cli` container (existing, from PR #350)
- `signal-bridge` sidecar container (new)
- Update `service.yaml` to expose the bridge's port 8080
- Update `kustomization.yaml` references

### Phase 3: Revert Hermes source changes

Revert the SSE → REST polling changes in `~/.hermes/hermes-agent/gateway/platforms/signal.py`:
- Restore `_sse_listener` to use `GET /api/v1/events?account={account}` with SSE
- Restore `_rpc` to use `POST /api/v1/rpc` (already correct)
- Remove REST polling deduplication logic (SSE handles ordering)
- Remove `_force_reconnect` (SSE auto-reconnects)

### Phase 4: Configure Hermes to point to k8s service

Update Hermes configuration:
- Set `SIGNAL_HTTP_URL` to `http://signal-cli-bridge.signal-cli.svc.cluster.local:8080`
- Set `SIGNAL_ACCOUNT` to the registered number
- Remove any TrueNAS-specific config

### Phase 5: Deploy to staging

- Build and push bridge image
- Apply kustomize staging overlay
- Verify health check: `GET /v1/health`
- Verify SSE stream: `curl -H "Accept: text/event-stream" http://<svc>/api/v1/events?account=+161...7251`
- Test inbound message delivery
- Test outbound message sending

### Phase 6: Promote to production

- Apply kustomize production overlay
- Verify Hermes reconnects and starts receiving messages
- Monitor for 24 hours
- Retire TrueNAS container

## Checklist

- [ ] Build SSE bridge (`images/signal-bridge/`)
- [ ] Push `gjcourt/signal-bridge:latest` (multi-arch)
- [ ] Update `deployment.yaml` — add bridge as sidecar container
- [ ] Update `service.yaml` — expose bridge port 8080
- [ ] Remove raw signal-cli service (port 7583 not needed externally)
- [ ] Revert Hermes `signal.py` to original SSE adapter
- [ ] Configure Hermes `SIGNAL_HTTP_URL` → k8s service
- [ ] Deploy to staging and verify
- [ ] Promote to production
- [ ] Retire TrueNAS signal container
- [ ] Update `docs/apps/signal-cli.md` with new architecture

## Notes

- **Why a bridge instead of signal-cli-rest-api?** The original Hermes adapter expects SSE, not REST polling. `signal-cli-rest-api` only provides REST polling. A bridge synthesizes SSE from signal-cli's JSON-RPC, keeping the Hermes adapter unmodified.
- **Why not run signal-cli-rest-api on k8s?** Same reason — the Hermes adapter would still need modification to use REST polling instead of SSE.
- **Why Go for the bridge?** Minimal dependencies, single static binary, small attack surface, fast startup. Python would work too but Go is more idiomatic for a simple HTTP server.
- **SSE vs. polling:** The bridge polls signal-cli on a 2s interval and pushes results as SSE. This gives the same real-time feel as true SSE while working with signal-cli's JSON-RPC protocol.
- **Config directory:** The PVC from PR #350 (`signal-cli-config`) persists the signal-cli registration state. The same PVC can be reused for the k8s deployment.
- **Account registration:** The existing registration on TrueNAS (`+161****7251`) uses a local config directory. When migrating to k8s, the config directory must be migrated to the PVC. Steps:
  1. Export config from TrueNAS: `docker cp ix-signal-cli-rest-api-signal-cli-rest-api-1:/var/lib/signal-cli /tmp/signal-cli-config`
  2. Import to k8s PVC (via `kubectl cp` or direct PVC mount)
  3. Verify registration: `kubectl exec -n signal-cli deploy/signal-cli -- signal-cli --config /var/lib/signal-cli info`
