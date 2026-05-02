# signal-bridge

HTTP bridge between signal-cli's JSON-RPC TCP daemon and the SSE+JSON-RPC shape expected by [Hermes](https://hermes-agent.nousresearch.com/).

## Architecture

```
Hermes ──HTTP──► signal-bridge:8080 ──TCP:7583──► signal-cli daemon
                   ├── GET /api/v1/events   (SSE, per-account filtered)
                   ├── POST /api/v1/rpc     (JSON-RPC relay)
                   ├── GET /api/v1/check    (health — Hermes)
                   ├── GET /v1/health       (health — legacy)
                   └── GET /metrics         (Prometheus)
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SIGNAL_CLI_HOST` | `signal-cli` | Hostname of the signal-cli JSON-RPC daemon |
| `SIGNAL_CLI_PORT` | `7583` | TCP port of the signal-cli daemon |
| `LISTEN_ADDR` | `0.0.0.0` | Address to bind the HTTP server |
| `LISTEN_PORT` | `8080` | Port to bind the HTTP server |
| `HERMES_ALLOWED_ACCOUNTS` | _(none)_ | Comma-separated E.164 numbers allowed to use the bridge. Empty = deny all. Set `HERMES_ALLOW_ALL_USERS=true` to skip this check. |
| `HERMES_ALLOW_ALL_USERS` | `false` | Bypass the account allowlist (useful for development) |
| `HERMES_AUTH_TOKEN` | _(none)_ | Bearer token required on `/api/v1/events` and `/api/v1/rpc`. Empty = no auth. |
| `POLL_INTERVAL` | `2s` | How often to poll signal-cli for new messages per account |
| `HEARTBEAT_INTERVAL` | `30s` | SSE heartbeat cadence |

## Endpoints

### `GET /api/v1/events?account=+1xxx`

SSE stream of inbound Signal messages for the given account. Requires `Authorization: Bearer <token>` if `HERMES_AUTH_TOKEN` is set. The `account` must be in `HERMES_ALLOWED_ACCOUNTS` (or `HERMES_ALLOW_ALL_USERS=true`).

### `POST /api/v1/rpc`

JSON-RPC relay to signal-cli. The request body is forwarded as-is after method-name normalization (`signal.send` → `send`, etc.). Validates `params.account` against the allowlist.

### `GET /api/v1/check` / `GET /v1/health`

Health check. Returns `{"status":"ok"}` when signal-cli is reachable, `{"status":"degraded"}` with HTTP 503 otherwise.

## Multi-account setup

1. Register each account on the signal-cli daemon (link device or provision directly).
2. Set `HERMES_ALLOWED_ACCOUNTS=+16179397251,+1<second-number>` in the SCALE UI env-var section.
3. Restart the signal-bridge container. The bridge polls each account independently.

To link a second account (e.g. a family member's phone):

```bash
# On hestia — generates a QR-code URI the phone scans in Signal → Linked Devices
docker exec ix-signal-signal-cli-1 \
  signal-cli --config /var/lib/signal-cli link --name "Hestia Bridge"
```

After linking, the phone number appears in `/var/lib/signal-cli/data/`. Add it to `HERMES_ALLOWED_ACCOUNTS` and update the SCALE UI app config.

## Dev loop

```bash
# Run signal-cli daemon locally (adjust the number)
docker run --rm -p 7583:7583 \
  -v "$PWD/testdata:/var/lib/signal-cli" \
  ghcr.io/asamk/signal-cli \
  daemon --tcp=0.0.0.0:7583 --receive-mode=on-connection

# Build and run the bridge
go build -o signal-bridge . && \
  SIGNAL_CLI_HOST=localhost \
  HERMES_ALLOWED_ACCOUNTS=+16179397251 \
  ./signal-bridge

# Test health
curl http://localhost:8080/api/v1/check

# Test SSE stream
curl -N "http://localhost:8080/api/v1/events?account=+16179397251"
```

## Image build

Images are published to `ghcr.io/gjcourt/signal-bridge` by the GHA workflow
`.github/workflows/build-signal-bridge.yml` on every push to `master` that touches this directory.

Tag format: `YYYY-MM-DD` (first build of the day) or `YYYY-MM-DD-N` (reruns).
