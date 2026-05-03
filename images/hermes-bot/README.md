# hermes-bot

Always-on Signal agent built on the [Hermes Agent](https://hermes-agent.nousresearch.com/) from NousResearch. Deployed to the Talos cluster (`melodic-muse`) in Signal-only mode so the bot persona stays online when the operator's laptop sleeps.

This directory **does not build a container image** — NousResearch publishes an official multi-arch image on Docker Hub and we use it directly. See [Upstream image](#upstream-image) below.

Plan: [`docs/plans/2026-05-02-hermes-bot-k8s.md`](../../docs/plans/2026-05-02-hermes-bot-k8s.md).
Architecture sibling: [`images/signal-bridge/`](../signal-bridge/) — the HTTP bridge between signal-cli's JSON-RPC TCP daemon and the SSE+JSON-RPC shape the Hermes signal adapter expects.

## Upstream image

| | |
|---|---|
| Registry | Docker Hub |
| Repository | `nousresearch/hermes-agent` |
| Recommended tag | `v2026.4.30` (pin to a dated release; avoid `latest` in cluster manifests) |
| Architectures | `linux/amd64`, `linux/arm64` |
| Source Dockerfile | [`hermes-agent/Dockerfile`](https://github.com/NousResearch/hermes-agent) (Debian 13.4, Python 3.13 via uv, `/opt/data` volume, `tini` init, drops to `hermes` UID 10000) |

D2 should pin by digest. To resolve the current digest:

```bash
docker manifest inspect nousresearch/hermes-agent:v2026.4.30 | \
  jq -r '.manifests[] | select(.platform.architecture=="amd64") | .digest'
```

Tag listing: `curl -sL "https://hub.docker.com/v2/repositories/nousresearch/hermes-agent/tags?page_size=20" | jq '.results[].name'`.

## Runtime contract

Hermes runs as a long-lived gateway: `hermes gateway run`. The upstream entrypoint defaults to `hermes` with no args, so the k8s `command:` should override to `["hermes", "gateway", "run"]`.

### Volume mount

| Path | Purpose |
|------|---------|
| `/opt/data` | `HERMES_HOME` — sessions, checkpoints, memory, skills, cron, logs, `config.yaml`. Must be writable by UID `10000` (the `hermes` user inside the image). The PVC mounts here. |

The upstream entrypoint seeds `/opt/data/config.yaml` from `cli-config.yaml.example` on first boot. To inject our own config, the D2 Deployment will mount a ConfigMap key into `/opt/data/config.yaml` (or write through an `initContainer`). See the plan's D2 section.

### Signal env vars (read by `gateway/platforms/signal.py`)

| Variable | Required | Value for hermes-bot | Description |
|----------|----------|----------------------|-------------|
| `SIGNAL_HTTP_URL` | yes | `http://signal-cli-bridge.signal-cli.svc.cluster.local:8080` | Base URL of signal-bridge (our shim around signal-cli) |
| `SIGNAL_ACCOUNT` | yes | `+16179397251` | E.164 number the bot listens on |
| `SIGNAL_HOME_CHANNEL` | optional | `+16179397251` | "Home" channel for proactive messages (matches `SIGNAL_ACCOUNT` for self-DM) |
| `SIGNAL_HOME_CHANNEL_NAME` | optional | `Home` | Display name for the home channel |
| `SIGNAL_IGNORE_STORIES` | optional | `true` | Ignore inbound Signal stories (default `true`) |
| `SIGNAL_GROUP_ALLOWED_USERS` | optional | _(unset)_ | Comma-separated E.164 numbers allowed to talk to the bot in groups. Empty = ignore all group messages. |

If the deployment turns on `HERMES_AUTH_TOKEN` on the signal-bridge side later, hermes-agent will need a corresponding upstream change to send `Authorization: Bearer …`; today the signal adapter does not. Treat the bridge as in-cluster-only for now (no Authorization header).

### LLM env vars

Hermes reads its model and provider from `config.yaml`, not env. The D2 ConfigMap will set:

```yaml
model:
  default: <model-name>
  provider: custom
  base_url: http://10.42.2.10:8000/v1   # llama.cpp on hestia
```

No API key is required for the local llama.cpp endpoint; if a future provider requires one, mount it via a SOPS-encrypted Secret as `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` etc.

### Toolsets (set in `config.yaml`, not env)

Per the plan's "Decisions":

- Platform toolset: `hermes-signal`
- Agent toolsets: `web` + `file` (read-only)
- **No `terminal`** in v1 — operator can opt in by editing the ConfigMap later

### Other env knobs

| Variable | Purpose |
|----------|---------|
| `HERMES_HOME` | Home directory (default `/opt/data`; do not override) |
| `HERMES_UID` / `HERMES_GID` | Remap the in-container `hermes` user to a host UID/GID. Not needed when the PVC is provisioned fresh. |
| `HERMES_ACCEPT_HOOKS` | Auto-approve unseen shell hooks without a TTY. Set to `1` in the bot deployment — there is no operator at the keyboard. |
| `PYTHONUNBUFFERED` | Already `1` in the upstream image; keep it for clean logs. |

## Run locally (operator dev loop)

```bash
# Pull the image
docker pull nousresearch/hermes-agent:v2026.4.30

# Start a one-shot signal-cli + signal-bridge stack (skip if you already have one)
# See images/signal-bridge/README.md.

# Run hermes-bot pointed at it
docker run --rm -it \
  -v "$PWD/.hermes-bot-data:/opt/data" \
  -e SIGNAL_HTTP_URL=http://host.docker.internal:8080 \
  -e SIGNAL_ACCOUNT=+16179397251 \
  -e SIGNAL_HOME_CHANNEL=+16179397251 \
  -e HERMES_ACCEPT_HOOKS=1 \
  nousresearch/hermes-agent:v2026.4.30 \
  hermes gateway run -v
```

On first boot the image seeds `/opt/data/config.yaml` from the upstream example. Edit that file (or replace it with the cluster ConfigMap content) to point `model.base_url` at your llama.cpp instance, then restart.

To send a test DM, message the bot's Signal number from your phone. Logs land in `/opt/data/logs/`.

## Why no Dockerfile here?

The plan's D1 specifies a build-from-PyPI image only "if upstream image exists" is false. NousResearch publishes `nousresearch/hermes-agent` to Docker Hub (multi-arch, dated tags, e.g. `v2026.4.30`), so we skip the build infrastructure and consume upstream directly. If upstream stops publishing or we need a custom variant (extra system packages, pre-baked skills, etc.), revisit by adding a `Dockerfile` here and a `.github/workflows/build-hermes-bot.yml` mirroring `build-signal-bridge.yml`.
