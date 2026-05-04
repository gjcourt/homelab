# Hermes

## 1. Overview

Hermes is the [Hermes Agent](https://hermes-agent.nousresearch.com/) from NousResearch deployed in Signal-only mode. It runs as a long-lived gateway that listens for direct messages from allow-listed phone numbers, forwards each conversation to a local LLM, and replies back over Signal.

Two bots run in parallel today, one per linked Signal account:

| App | Listens on | Namespace (prod) | Namespace (stage) |
|---|---|---|---|
| `hermes` | `+16179397251` (operator's primary) | `hermes-prod` | `hermes-stage` |
| `hermes-callee` | `+14153089014` (operator's spouse) | `hermes-callee-prod` | `hermes-callee-stage` |

The pattern is symmetric — every bot is identical except for `SIGNAL_ACCOUNT` and namespace. New bots for additional linked accounts follow the same template (see [§5](#5-usage-instructions)).

The cluster instances are the "always-on" companions to the operator's laptop-bound Hermes CLI — the laptop instance is for interactive use; cluster instances are what a phone can DM at any hour.

## 2. Architecture

Single-replica `Deployment` per bot (Recreate strategy — sessions don't cluster, only one process should write checkpoints). The container does not expose any HTTP listener; readiness/liveness use a `pgrep -f 'hermes gateway'` exec probe.

```
phone (Signal) ──► signal-cli (k8s pod) ──► signal-bridge (sidecar :8080) ──► hermes ┐
                       │                                                   ──► hermes-callee
                       └── multi-account daemon, one PVC, two linked devices
                                                                                       │
                                                                                       ▼
                                                                   llama.cpp on hestia (10.42.2.10:8000)
                                                                                       │
                                                                                       ▼
                                                                            response back through the same chain
```

- **Image**: upstream `nousresearch/hermes-agent` (Debian 13.4, Python 3.13 via uv, runs as UID 10000), digest-pinned in `apps/base/hermes/deployment.yaml`.
- **Storage**: each bot has its own 5 GiB iSCSI PVC mounted at `/opt/data` (`HERMES_HOME`) for sessions, checkpoints, memory, skills, cron, logs, and the upstream-seeded `config.yaml`. `securityContext.fsGroup: 10000` lets the non-root process write to the volume. PVCs in different namespaces are different volumes — bots have isolated state.
- **Inference**: direct to llama.cpp on hestia at `http://10.42.2.10:8000/v1`. No llmux hop — the `hermes` chat template emits clean tool calls.
- **Signal transport**: in-cluster ClusterIP at `http://signal-cli-bridge.signal-cli.svc.cluster.local:8080`. Auth boundary is the network layer (no public ingress on signal-bridge), not HTTP — the Hermes Signal adapter does not currently send `Authorization: Bearer …`.
- **Multi-bot Kustomize structure**: `apps/base/hermes-callee/` overlays `apps/base/hermes/` and patches only what differs (namespace + 2 ConfigMap fields). Image bumps and probe tweaks to the base propagate to both bots — zero per-bot drift to maintain.

## 3. URLs

None. Hermes has no HTTP listener and no Gateway entry. To talk to either bot, DM its account number on Signal from an allow-listed sender.

## 4. Configuration

### Environment variables (ConfigMap `hermes-config`)

Loaded via `envFrom`. Edit `apps/base/hermes/configmap.yaml`. Per-bot overrides for `SIGNAL_ACCOUNT` and `SIGNAL_HOME_CHANNEL` live in each bot's overlay (e.g., `apps/base/hermes-callee/kustomization.yaml`).

| Var | Purpose |
|---|---|
| `SIGNAL_HTTP_URL` | signal-bridge endpoint inside the cluster |
| `SIGNAL_ACCOUNT` | bot's Signal phone number (per-bot) |
| `SIGNAL_HOME_CHANNEL` | E.164 Hermes treats as the bot's "home" thread (per-bot) |
| `SIGNAL_HOME_CHANNEL_NAME` | display name for the home channel |
| `SIGNAL_IGNORE_STORIES` | drop Signal stories from the inbox stream |
| `SIGNAL_ALLOWED_USERS` | CSV E.164 allow-list — only these can DM the bot |
| `SIGNAL_GROUP_ALLOWED_USERS` | CSV E.164 — empty means group messages are ignored entirely |
| `HERMES_ACCEPT_HOOKS` | `1` — auto-approve unseen shell hooks (no operator at the keyboard) |
| `PYTHONUNBUFFERED` | `1` — flush stdout/stderr immediately for responsive logs |

### Hermes core config (ConfigMap `hermes-config-yaml`)

Mounted at `/opt/data/config.yaml`. Edit `apps/base/hermes/configmap.yaml` (second document). Defines the model + provider, toolset list, agent limits, checkpoint retention, prompt caching, code-execution mode, logging.

The upstream image seeds `/opt/data/config.yaml` from `cli-config.yaml.example` on first boot if the file is absent. Mounting our own ConfigMap key here ensures fixed configuration regardless of PVC state.

### Toolsets (in `config.yaml`)

- `hermes-signal` — Signal platform toolset (DM listener)
- `file` — read-only file ops
- `web` — web browsing

`terminal` is **intentionally omitted** in v1 to limit blast radius. To opt in, add it to the `toolsets:` list in the ConfigMap and reconcile.

### Secrets

None at present. Inference is local to the LAN; no API keys are needed. If a remote provider is added later, it goes in a SOPS-encrypted Secret consumed via env (e.g., `OPENAI_API_KEY`).

### signal-bridge allow-list

Each bot's `SIGNAL_ACCOUNT` must also appear in signal-bridge's `HERMES_ALLOWED_ACCOUNTS` env (`apps/base/signal-cli/deployment.yaml`). The bridge filters which accounts external clients can subscribe to; if `SIGNAL_ACCOUNT` isn't in that list, the bot will fail to connect. Both `+16179397251` and `+14153089014` are currently allowed.

## 5. Usage Instructions

### Talking to a bot

DM the bot's account from an allow-listed sender. Currently:
- `+16179397251` → primary bot (`hermes-prod`)
- `+14153089014` → spouse's bot (`hermes-callee-prod`)

Allow-listed senders today: `+16179397251`, `+14153089014`. Either spouse can DM either bot.

Note-to-self works for both: when you message your own number from your own phone, signal-cli (linked secondary device on that account) sees the message; the bot listening on that account picks it up; sender is on the allow-list, so the bot replies. UX is "open Signal, type, get answer" — no separate contact required.

### Adding an allowed sender

1. Edit the `SIGNAL_ALLOWED_USERS` CSV in `apps/base/hermes/configmap.yaml` (E.164 format, `+1...`). Both bots inherit this from base.
2. Commit + PR + merge.
3. Flux reconciles → restart each affected hermes pod (`kubectl rollout restart -n <namespace> deployment/hermes`) — env-var changes don't auto-roll.

### Adding a new bot for a newly linked account

This is the "second person joins the family" workflow. Pre-requisite: the Signal account must already be linked to signal-cli (see [signal-cli runbook](../2026-05-03-signal-cli-account-management.md), Flow A or the helper-pod fallback).

1. **Update `apps/base/signal-cli/deployment.yaml`** — extend `HERMES_ALLOWED_ACCOUNTS` to include the new E.164.
2. **Create `apps/base/<new-bot>/kustomization.yaml`** that overlays `../hermes/` and patches the namespace + `SIGNAL_ACCOUNT` + `SIGNAL_HOME_CHANNEL`. Use `apps/base/hermes-callee/` as the template.
3. **Create `apps/staging/<new-bot>/kustomization.yaml` and `apps/production/<new-bot>/kustomization.yaml`** with the namespace patch (`<new-bot>` → `<new-bot>-stage`, `<new-bot>-prod`).
4. **Wire into `apps/{staging,production}/kustomization.yaml`** alphabetically.
5. PR + merge. Flux reconciles, signal-bridge restarts (briefly disrupts existing bots' SSE; they reconnect automatically), new bot's namespace + PVC + Pod come up.
6. Send a note-to-self from the newly linked phone — bot should reply.

Resource cost per bot: ~256Mi memory + 100m CPU request, 1Gi/1 CPU limit.

### Switching the model

1. Edit `model.default` in the `hermes-config-yaml` ConfigMap (`apps/base/hermes/configmap.yaml`).
2. If the new model needs a different `base_url` (e.g., switching from llama.cpp to vLLM), update that too.
3. Commit, merge, restart **both** bots.

### Managing signal-cli accounts

The bots don't manage their own Signal accounts — `signal-cli` does, and registering/unregistering Signal accounts is a separate operation against the `signal-cli` pod's PVC. See [`docs/operations/2026-05-03-signal-cli-account-management.md`](../2026-05-03-signal-cli-account-management.md) for the full procedure (linking via JSON-RPC, helper-pod fallback, troubleshooting).

## 6. Testing

```bash
# Per-bot pod state (replace <bot> with hermes or hermes-callee, <env> with prod/stage)
kubectl get pod -n <bot>-<env>

# Successful Signal SSE registration?
kubectl logs -n <bot>-<env> deploy/hermes | grep -iE "signal|connected|registered"

# PVC utilization (should stay <50% with auto_prune on)
kubectl exec -n <bot>-<env> deploy/hermes -- df -h /opt/data
```

Round-trip per bot: DM the bot's number from its target sender, confirm a reply lands within ~30s for a trivial prompt. If the reply takes longer or never arrives, walk down the chain in [§9](#9-troubleshooting).

## 7. Monitoring & Alerting

- **Metrics**: Hermes does not expose Prometheus metrics. Pod CPU/memory show up in the generic Application Health dashboard via the `app: hermes` label (multi-namespace).
- **Logs**: structured to stdout.
  ```bash
  kubectl logs -n hermes-prod deploy/hermes -f
  kubectl logs -n hermes-callee-prod deploy/hermes -f
  ```
  Hot signals to grep for: `ERROR`, `signal`, `model`, `tool_use`. The agent emits a turn-by-turn structure that's verbose under `verbose: true`.
- **Restart count**: a non-zero `RESTARTS` over a 24h window in any bot's namespace suggests OOM (bump memory limit), signal-bridge connectivity flapping, or signal-cli daemon instability (see [§9](#9-troubleshooting)).
- **Signal SSE health from bridge side**: `kubectl logs -n signal-cli deploy/signal-cli -c signal-bridge | grep "SSE: client"` shows connect/disconnect events for each bot.

## 8. Disaster Recovery

- **Backup strategy**:
  - `apps/base/hermes/configmap.yaml` (env + `config.yaml`) is in Git.
  - The PVCs at `/opt/data` (one per bot) hold session history, checkpoints, and memory. With `checkpoints.auto_prune: true` + `retention_days: 7` they stay bounded; underlying iSCSI snapshots on Synology are the recovery surface for catastrophic loss.
- **Restore procedure**:
  - PVC loss for one bot: delete the pod and PVC for the affected namespace, re-apply Flux. The pod re-seeds `/opt/data/config.yaml` from the mounted ConfigMap key on first boot. Conversation history and any cron-built artifacts are gone — bot returns with a clean memory.
  - ConfigMap regression: revert the offending commit, Flux reconciles, restart all bots that consumed the bad config.
- **Whole-cluster signal-cli loss**: bots can't function. Recovery is at the signal-cli layer (re-link accounts via runbook); bots come back when bridge is reachable again.

## 9. Troubleshooting

### Bot offline / not responding to DMs

Walk the chain:

1. Is the bot's pod `Running`? `kubectl get pod -n <bot>-prod`. If `CrashLoopBackOff`, check logs.
2. Is `signal-cli` pod `Running` in the `signal-cli` namespace? Bot can't receive without it.
3. Is the bot's account in `HERMES_ALLOWED_ACCOUNTS` on signal-bridge? `kubectl logs -n signal-cli deploy/signal-cli -c signal-bridge | grep accounts=`. Should list the bot's `SIGNAL_ACCOUNT`.
4. Is llama.cpp on hestia reachable? `kubectl exec -n <bot>-prod deploy/hermes -- curl -s http://10.42.2.10:8000/v1/models | head`. If empty, GPU host is down.
5. Are you on the sender allow-list? `kubectl get cm -n <bot>-prod hermes-config -o jsonpath='{.data.SIGNAL_ALLOWED_USERS}'`.
6. Does signal-cli actually have the target account linked? `kubectl exec -n signal-cli deploy/signal-cli -c signal-cli -- bash -c 'exec 3<>/dev/tcp/127.0.0.1/7583; printf "%s\n" "{\"jsonrpc\":\"2.0\",\"id\":99,\"method\":\"listAccounts\"}" >&3; timeout 3 cat <&3' 2>/dev/null` → should list both `+16179397251` and `+14153089014`.

### `Signal SSE: HTTP error: All connection attempts failed` (in hermes logs)

Hermes-bot's SSE subscription to signal-bridge is broken. Common causes:

- **signal-bridge sidecar restarted recently** — gives a brief outage; hermes' built-in retry loop reconnects on the next attempt. Wait 1-2 min.
- **signal-cli pod restarted** — bridge sidecar comes back up first, then hermes reconnects. Same 1-2 min wait.
- **hermes-bot stuck in exponential backoff** after a long signal-cli outage — restarting the hermes pod clears the backoff state immediately: `kubectl rollout restart deployment/hermes -n <bot>-prod`.

### `Signal: SSE idle for 120s` (hermes is "connected" but no events flow)

The SSE TCP connection is up but signal-bridge isn't pushing events. Causes:

- **`rpc error -1: Receive command cannot be used if messages are already being received`** in bridge logs — signal-cli daemon thinks something else is already receiving. Almost always caused by a leftover RPC subscription from manual `receive`/`subscribeReceive` JSON-RPC calls. **Fix:** `kubectl rollout restart deployment/signal-cli -n signal-cli`.
- **Signal-side delivery delay after a fresh device link** — Signal sometimes takes 1-5 min to resume delivering messages to a newly linked-device set. Wait it out before assuming a bug.
- **signal-cli daemon's WebSocket to Signal servers is broken** — diagnose via JSON-RPC `version` and `receive` (see signal-cli runbook §"Receive-mode quirk"). If `receive` returns nothing for 30+s when a recent message is queued, the daemon's subscription is stale; restart signal-cli.

### Slow replies (>60s on trivial prompts)

Usually llama.cpp queue depth or model swap. Check hestia GPU utilization (`ssh truenas_admin@10.42.2.10 'nvidia-smi'`).

### PVC full

Check `df -h /opt/data` in the affected pod. If approaching 100%, `checkpoints.max_snapshots` may be too high or `auto_prune` got disabled. Edit the `hermes-config-yaml` ConfigMap to tighten retention; restart.

### `hermes gateway` process not detected by probe

The readiness probe greps for `hermes gateway` in the process table. If the binary crashed and tini hasn't restarted it, the pod will be marked NotReady within `30 * 3 = 90s`. Logs will show the crash cause; livenessProbe (5min grace) will then trigger a restart.

### One bot Ready, the other Pending forever

If one bot's namespace shows `0/1` and stays Pending:

- Check `kubectl describe pod -n <bot>-<env> -l app=hermes` for scheduling/PVC events.
- Common cause for `<bot>-stage`: `signal-cli-stage` has no Signal account linked, hermes Gateway can't connect to its platform at startup, exits, restart-loops. Acceptable until staging signal-cli gets a separate account; the stage bot is purely a structural canary in the meantime.
- For prod: most often a brief signal-cli sidecar restart racing the bot startup. The bot's "no graceful degradation" startup behavior means it crashes if it can't connect immediately. Restart again after signal-cli stabilizes.

### Auth issues to signal-bridge

`HERMES_AUTH_TOKEN` is currently *not* sent by the upstream Hermes Signal adapter. Signal-bridge accepts unauthenticated requests from in-cluster traffic (NetworkPolicy is the only barrier). If a non-Hermes caller is added later that requires auth, that's a future patch on both sides.

## Related runbooks

- [signal-cli account management](../2026-05-03-signal-cli-account-management.md) — linking, registration, removal, JSON-RPC tricks.
- [hermes-bot k8s plan](../../plans/2026-05-02-hermes-bot-k8s.md) — the design rationale for everything above.
