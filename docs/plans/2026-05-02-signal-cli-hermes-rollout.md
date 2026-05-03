---
status: planned
last_modified: 2026-05-02
---

# Signal-cli + Hermes Rollout — TrueNAS Custom App

Stand up a Signal stack on TrueNAS (`truenas_admin@10.42.2.10`) that gives the [Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/signal) agent platform the SSE + JSON-RPC integration it expects. Replace the existing REST-only `signal-cli-rest-api` deployment, keeping the same Signal account `+16179397251` (no re-linking required).

Two related housekeeping items roll into this work:
1. Formalize where TrueNAS-resident, non-IaC artifacts live in this repo.
2. Define a structure for prompt logs / planning context under `~/src/config/` (out-of-repo; tracked here for reference).

## Decisions

- **Host**: TrueNAS at `10.42.2.10`, SSH as `truenas_admin@10.42.2.10`. No canonical hostname configured (`hestia` is informal).
- **Architecture**: two services — `signal-cli` (upstream `ghcr.io/asamk/signal-cli`) running in JSON-RPC daemon mode + `signal-bridge` (custom Go service translating signal-cli RPC into the SSE+JSON-RPC shape Hermes expects).
- **Deployment surface**: **TrueNAS Custom App** (paste compose YAML into SCALE UI), not raw `docker compose` + systemd. TrueNAS handles auto-restart, status UI, ZFS snapshot integration, and managed upgrades. Trade-off accepted: operator manually copy/pastes the compose YAML from this repo into the SCALE UI on each change. Source of truth is git; enforcement is human discipline.
- **Existing signal-cli-rest-api**: replaced. Same number `+16179397251`. Data dir is migrated, not re-registered (avoids re-linking the user's primary device).

## Current footprint

| Path | State | Notes |
|---|---|---|
| `images/signal-bridge/` | UNTRACKED | Go SSE+RPC bridge. Existing Dockerfile + Dockerfile.amd64. Includes pre-built binaries (do not commit). |
| `misc/docker-compose.yml` | UNTRACKED | Draft signal-cli compose using `--dbus-system` mode (wrong shape — superseded by this plan). |
| `misc/docker-compose-{llama,vllm,nvtop}.yaml` | UNTRACKED | TrueNAS-resident services for the GPU box. Folded into the new `hosts/` layout. |
| `images/{go-librespot,snapcast}/` | tracked | Established image-build precedent: `Dockerfile + README.md`. |

### Hermes endpoint expectations

Per the Hermes [Signal integration docs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/signal):

- `SIGNAL_HTTP_URL=http://<host>:8080`
- `GET /api/v1/check` → health probe
- SSE stream for inbound message envelopes
- JSON-RPC for outbound `send`-style calls
- Allowlists via env: `SIGNAL_ALLOWED_USERS`, `SIGNAL_GROUP_ALLOWED_USERS`, `SIGNAL_ALLOW_ALL_USERS`

### Existing signal-bridge endpoint shape (`images/signal-bridge/main.go`)

| Endpoint | Status |
|---|---|
| `/api/v1/events` (SSE) | ✓ |
| `/api/v1/rpc` (JSON-RPC) | ✓ |
| `/v1/health` | ✗ — Hermes wants `/api/v1/check`; add as alias |
| `/metrics` (Prometheus) | ✓ |
| Hermes-style allowlist envs | ✗ — add |

### Live discovery on TrueNAS (captured during planning)

| Fact | Value |
|---|---|
| Existing container | `ix-signal-cli-rest-api-signal-cli-rest-api-1` |
| Image | `bbernhard/signal-cli-rest-api:0.99` |
| Mode | `native` (signal-cli CLI under a REST shim) |
| Data dir on host | `/mnt/.ix-apps/app_mounts/signal-cli-rest-api/config` |
| Container mount | `/config` |
| UID/GID | 568:568 |
| Managed by | TrueNAS Apps (UI-driven; not raw compose) |
| Host arch | x86_64 |
| Docker version | 29.0.4 |

## Deliverables

### D1 — Productionize signal-bridge (PR)

- Move `images/signal-bridge/` from untracked → tracked.
- Remove pre-built binaries (`signal-bridge`, `signal-bridge-amd64`); add `.gitignore`.
- Replace `Dockerfile.amd64` shortcut with a proper multi-stage Dockerfile (single-arch is fine — TrueNAS is x86_64).
- Add health alias: `/api/v1/check` returns the same body as `/v1/health` (keeps both paths working).
- Add Hermes allowlist envs:
  - `HERMES_ALLOWED_ACCOUNTS=+1...,+1...` — verified against the `account` query param on `/api/v1/events`.
  - `HERMES_AUTH_TOKEN` — bearer-token gate on `/api/v1/rpc` and `/api/v1/events` for defense in depth.
- Add `.github/workflows/build-signal-bridge.yml` mirroring the overture `build-images.yml` pattern: tag `ghcr.io/gjcourt/signal-bridge:YYYY-MM-DD[-N]` on push to main.
- Add `images/signal-bridge/README.md` (purpose, env vars, endpoints, dev-loop commands).

### D2 — TrueNAS Custom App for the Signal stack (PR)

The compose YAML in git is canonical. The operator pastes it into the SCALE UI **Apps → Discover Apps → Custom App** wizard to deploy. TrueNAS handles auto-restart, status, logs, and ZFS snapshot integration. **No systemd unit; no `docker compose` invocation by hand.**

New file: `hosts/hestia/signal/docker-compose.yml` (Custom-App-compatible — no `build:`, no `secrets:` block, no `container_name`):

```yaml
services:
  signal-cli:
    image: ghcr.io/asamk/signal-cli@sha256:<pinned>
    restart: unless-stopped
    user: "568:568"   # matches UID/GID of the existing signal-cli-rest-api data
    command:
      - daemon
      - --tcp=0.0.0.0:7583
      - --receive-mode=on-connection
      - --ignore-stories
    volumes:
      # Custom volume: data lives outside /mnt/.ix-apps/ so deleting the
      # Custom App does NOT wipe Signal identity. Pre-create on TrueNAS:
      #   sudo zfs create tank/apps/signal && sudo mkdir /mnt/tank/apps/signal/data
      - /mnt/tank/apps/signal/data:/var/lib/signal-cli
    networks: [signal-net]

  signal-bridge:
    image: ghcr.io/gjcourt/signal-bridge:<tag>   # <tag> updated in SCALE UI on each upgrade
    restart: unless-stopped
    environment:
      SIGNAL_CLI_HOST: signal-cli
      SIGNAL_CLI_PORT: "7583"
      LISTEN_ADDR: "0.0.0.0"
      LISTEN_PORT: "8080"
      HERMES_ALLOWED_ACCOUNTS: "+16179397251"
      # Bearer token: set in SCALE UI's env-var section as masked input.
      # Do NOT commit a value here; YAML in git stays clean.
      HERMES_AUTH_TOKEN: ""
    ports:
      # SCALE UI accepts host-IP binding via its port-config interface.
      - "10.42.2.10:8080:8080"
    depends_on:
      signal-cli: { condition: service_started }
    networks: [signal-net]

networks:
  signal-net: {}
```

**Why this shape works for SCALE Custom App:**
- No `build:` — Custom App pulls images only. signal-bridge is published to ghcr.io by the GHA workflow from D1.
- No `secrets:` block — secrets in compose are awkward in SCALE UI; bearer token goes through the UI's env-var input (masked) on app create/edit.
- No `container_name` — TrueNAS prefixes containers with `ix-<app-name>-…-1` automatically.
- Volume points to `/mnt/tank/apps/signal/data` (operator-owned dataset) rather than `/mnt/.ix-apps/…`. Deleting/recreating the Custom App does not destroy Signal identity.
- Port binding `10.42.2.10:8080:8080` keeps the bridge LAN-only.

Plus `hosts/hestia/signal/README.md` covering:
- **Sync rule** — when the YAML changes in git, operator opens SCALE UI → Apps → `signal` → Edit → paste new YAML → Save. SCALE diff-applies and restarts containers as needed. **This is the only path for changes to take effect.**
- **Bearer token rotation** — Edit App → update `HERMES_AUTH_TOKEN` env var → Save. Tell the Hermes operator the new value out-of-band.
- **Drift check** — periodic spot-check that the live config matches git:
  ```bash
  ssh truenas_admin@10.42.2.10 'docker inspect ix-signal-signal-bridge-1 \
    --format "{{.Config.Image}} {{range .Config.Env}}{{println .}}{{end}}"'
  ```
  Diff against the YAML in git; flag divergence.
- **Migration playbook** — see Section "Migration" below.

### D3 — Repo restructure for TrueNAS artifacts (PR)

Establish a new top-level directory: `hosts/`.

```
hosts/
  README.md                    # convention doc
  hestia/
    README.md                  # what runs on TrueNAS, dataset paths, IP
    signal/
      docker-compose.yml
      README.md                # sync rule, drift check, bearer rotation, migration playbook
    llms/
      docker-compose-llama.yml
      docker-compose-vllm.yml
      README.md
    monitoring/
      docker-compose-nvtop.yml
      README.md
```

**Migration mechanics:**
- `git mv misc/docker-compose-llama.yaml hosts/hestia/llms/docker-compose-llama.yml`
- `git mv misc/docker-compose-vllm.yaml hosts/hestia/llms/docker-compose-vllm.yml`
- `git mv misc/docker-compose-nvtop.yaml hosts/hestia/monitoring/docker-compose-nvtop.yml`
- Delete `misc/docker-compose.yml` (the dbus draft is superseded by D2).
- `rmdir misc/`.

**Boundary defined in `hosts/README.md`:**
- **`hosts/`** — docker-compose, host-side scripts, dataset-path docs for services that run *on* a specific TrueNAS / VM and aren't managed by Flux.
- **`apps/`** — Kubernetes manifests for services managed by Flux on melodic-muse.
- **`images/`** — build context for container images we author (Dockerfile + source).
- **`infra/`** — cluster-level controllers / CRDs / configs for melodic-muse.

### D4 — Prompt-log structure under `~/src/config/` (out-of-repo)

Tracked here for reference; physical changes happen in `~/src/config/` (separate from this repo).

Adopt:

```
~/src/config/
  README.md                    # this convention
  prompts/
    YYYY-MM-DD-<topic>.md      # verbatim user prompts that kicked off a session
  notes/
    YYYY-MM-DD-<topic>.md      # investigation, decisions, clarifying-question answers
  agents/
    <name>.md                  # durable agent definitions
  designs/
    <topic>.md                 # durable architecture / convention docs
```

For this task: `~/src/config/prompts/2026-05-02-signal-cli-hermes.md` (the verbatim prompt) + `~/src/config/notes/2026-05-02-signal-cli-hermes.md` (Hermes endpoint requirements, IP correction, decisions).

## Migration

Hands-on cutover at `truenas_admin@10.42.2.10`. Two TrueNAS Apps coexist briefly (old `signal-cli-rest-api` stopped, new `signal` running). Once verified, the old App is deleted via the UI.

1. **Pre-cutover ZFS snapshot**
   ```bash
   ssh truenas_admin@10.42.2.10
   sudo zfs list | grep ix-apps        # identify the dataset; usually tank/.ix-apps or pool/.ix-apps
   sudo zfs snapshot <dataset>@pre-signal-migration-$(date +%Y%m%d)
   ```

2. **Create operator-owned dataset for the new volume**
   ```bash
   sudo zfs create tank/apps/signal    # if not already a dataset
   sudo mkdir -p /mnt/tank/apps/signal/data
   ```

3. **Copy existing config** (existing App still running — read-only copy is safe)
   ```bash
   sudo rsync -aHAX /mnt/.ix-apps/app_mounts/signal-cli-rest-api/config/ /mnt/tank/apps/signal/data/
   sudo chown -R 568:568 /mnt/tank/apps/signal/data
   ```

4. **Capture pre-cutover baseline**
   ```bash
   docker exec ix-signal-cli-rest-api-signal-cli-rest-api-1 \
     signal-cli -a +16179397251 listIdentities 2>&1 | wc -l    # contact count
   ```
   Record the number for comparison post-cutover.

5. **Stop the existing TrueNAS App**
   - SCALE UI → **Apps** → `signal-cli-rest-api` → **Stop**.
   - Confirm: `docker ps --filter name=signal-cli-rest-api` returns nothing.

6. **Create the new Custom App in SCALE UI**
   - SCALE UI → **Apps** → **Discover Apps** → **Custom App**.
   - **Application Name**: `signal`.
   - **Compose YAML**: paste contents of `hosts/hestia/signal/docker-compose.yml` from git, with `<pinned>` and `<tag>` substituted.
   - **Environment** → add `HERMES_AUTH_TOKEN` to the `signal-bridge` service with the bearer token (masked input).
   - Click **Install**. Watch the App status until **Running**.

7. **Verify signal-cli identity intact**
   ```bash
   docker exec ix-signal-signal-cli-1 signal-cli -a +16179397251 listIdentities 2>&1 | wc -l
   ```
   Compare against step 4 baseline. Numbers must match.

8. **Verify Hermes endpoint shape**
   ```bash
   curl -fsS http://10.42.2.10:8080/api/v1/check
   curl -fsS -N "http://10.42.2.10:8080/api/v1/events?account=+16179397251" \
     -H "Authorization: Bearer $TOKEN"
   ```
   First returns OK; second opens a streaming SSE connection (will hang — Ctrl-C after seeing the heartbeat).

9. **Functional smoke** — send a Signal message TO `+16179397251` from a phone. The SSE stream from step 8 should emit the message envelope. Reply via:
   ```bash
   curl -fsS -X POST http://10.42.2.10:8080/api/v1/rpc \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"send","params":{"recipient":["+1<test-number>"],"message":"migration smoke test"},"id":1}'
   ```

10. **Decommission** — SCALE UI → **Apps** → `signal-cli-rest-api` → **Delete**. ZFS snapshot from step 1 remains as safety net (90-day retention).

**Rollback** at any step before 10: SCALE UI → **Apps** → `signal` → **Stop**, then **Apps** → `signal-cli-rest-api` → **Start**. Data on `/mnt/tank/apps/signal/data` is unchanged (we copied, not moved); the old App's data under `/mnt/.ix-apps/...` is untouched. After step 10: restore from ZFS snapshot before retrying.

## Phased order of work

| # | PR / Phase | What | Depends on |
|---|---|---|---|
| 1 | **PR-A** — repo restructure (D3) | Create `hosts/` skeleton, move `misc/` contents, delete `misc/`. No build artifacts; easy review. | — |
| 2 | **PR-B** — productionize signal-bridge (D1) | Track the directory, clean up, add Dockerfile + GHA workflow + README + endpoint alias + allowlist envs. First GHA build → tag in `ghcr.io/gjcourt/signal-bridge`. | PR-A (directory layout) |
| 3 | **PR-C** — Custom App YAML (D2) | Add `hosts/hestia/signal/docker-compose.yml` referencing the tag from PR-B + README covering sync rule, drift check, rotation, migration. | PR-B (image tag) |
| 4 | **Migration** | Hands-on cutover (~30 min including ZFS snapshot). | PR-C merged |
| 5 | **PR-D** — `~/src/config/` restructure (D4) | Independent of homelab. | — |
| 6 | **Hermes wiring** | Hand the operator `http://10.42.2.10:8080` and the bearer token. | Migration done |

## Verification

- **PR-A**: `git log --follow hosts/hestia/llms/docker-compose-llama.yml` shows the move from `misc/`. `find misc -type f` returns nothing.
- **PR-B**: GHA workflow produces `ghcr.io/gjcourt/signal-bridge:<tag>`; `docker manifest inspect` succeeds. `curl localhost:8080/api/v1/check` works in a local test container.
- **PR-C**: `docker compose -f hosts/hestia/signal/docker-compose.yml config` validates without error.
- **Migration**: `signal-cli listIdentities` post-cutover matches the captured baseline exactly.
- **Hermes**: end-to-end — phone → SSE inbound → Hermes → JSON-RPC outbound → phone receives.

## Out of scope

- **Hermes server-side configuration** — user supplies the bearer token and points Hermes at `http://10.42.2.10:8080`.
- **TLS termination on signal-bridge** — LAN-only deployment; if Hermes runs off-LAN later, add a reverse proxy (Caddy / Traefik) on TrueNAS.
- **Multi-account signal-cli** — single `+16179397251` for now. The bridge's `account` query param already supports multi-account if the daemon does.
- **Backup automation for `/mnt/tank/apps/signal/data`** — covered by existing TrueNAS dataset snapshot policy.
- **Migration of pre-existing `~/src/config/2026-05-01-*.md` files** — D4 specifies the convention; mechanical moves can be batched into PR-D.
- **Deletion of the existing signal-cli-rest-api dataset** — keep at least 90 days post-cutover for rollback.

## Open items to resolve mid-execution

- **Allowlist semantics**: Hermes's `SIGNAL_ALLOW_ALL_USERS=true` short-circuits the per-user list. The bridge should mirror this — confirm with the Hermes operator whether allowlists belong on the bridge, on Hermes, or both. Default to both for defense in depth.
- **ghcr.io credentials on TrueNAS**: ghcr.io public pulls work without login. If signal-bridge is published as a public package, no auth needed. If private, configure registry credentials in the SCALE UI when creating the Custom App.
