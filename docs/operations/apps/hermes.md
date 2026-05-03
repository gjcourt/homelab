# Hermes

## 1. Overview
Hermes is the [Hermes Agent](https://hermes-agent.nousresearch.com/) from NousResearch deployed in Signal-only mode. It runs as a long-lived gateway that listens for direct messages from allow-listed phone numbers, forwards each conversation to a local LLM, and replies back over Signal. It is the "always-on" companion to the operator's laptop-bound Hermes CLI — the laptop instance is for interactive use; this cluster instance is the one a phone can DM at any hour.

## 2. Architecture
Single-replica `Deployment` (Recreate strategy — sessions don't cluster, only one process should write checkpoints). The container does not expose any HTTP listener; readiness/liveness use a `pgrep -f 'hermes gateway'` exec probe.

```
phone (Signal) ──► signal-cli (k8s pod) ──► signal-bridge (sidecar :8080) ──► hermes-bot (k8s pod)
                                                                                       │
                                                                                       ▼
                                                                       llama.cpp on hestia (10.42.2.10:8000)
                                                                                       │
                                                                                       ▼
                                                                                response back through the same chain
```

- **Image**: upstream `nousresearch/hermes-agent` (Debian 13.4, Python 3.13 via uv, runs as UID 10000), digest-pinned in `apps/base/hermes/deployment.yaml`.
- **Storage**: 5 GiB iSCSI PVC mounted at `/opt/data` (`HERMES_HOME`) for sessions, checkpoints, memory, skills, cron, logs, and the upstream-seeded `config.yaml`. `securityContext.fsGroup: 10000` lets the non-root process write to the volume.
- **Inference**: direct to llama.cpp on hestia at `http://10.42.2.10:8000/v1`. No llmux hop — the `hermes` chat template emits clean tool calls.
- **Signal transport**: in-cluster ClusterIP at `http://signal-cli-bridge.signal-cli.svc.cluster.local:8080`. Auth boundary is the network layer (no public ingress on signal-bridge), not HTTP — the Hermes Signal adapter does not currently send `Authorization: Bearer …`.
- **No HTTPRoute / Gateway entry**: bot has no public surface.

## 3. URLs
None. Hermes has no HTTP listener and no Gateway entry. To talk to the bot, DM `+16179397251` on Signal from an allow-listed number.

## 4. Configuration

### Environment variables (ConfigMap `hermes-config`)
Loaded via `envFrom`. Edit `apps/base/hermes/configmap.yaml`.

| Var | Purpose |
|---|---|
| `SIGNAL_HTTP_URL` | signal-bridge endpoint inside the cluster |
| `SIGNAL_ACCOUNT` | bot's Signal phone number (`+16179397251`) |
| `SIGNAL_HOME_CHANNEL` | E.164 Hermes treats as the bot's "home" thread |
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

## 5. Usage Instructions

### Talking to the bot
DM `+16179397251` on Signal from an allow-listed number (currently `+16179397251` and `+14153089014` per `SIGNAL_ALLOWED_USERS`). The bot replies in the same DM.

### Adding an allowed user
1. Edit the `SIGNAL_ALLOWED_USERS` CSV in `apps/base/hermes/configmap.yaml` (E.164 format, `+1...`).
2. Commit + PR + merge.
3. Flux reconciles → restart hermes pod (`kubectl rollout restart -n hermes deployment/hermes`) — env-var changes don't auto-roll.

### Switching the model
1. Edit `model.default` in the `hermes-config-yaml` ConfigMap (`apps/base/hermes/configmap.yaml`).
2. If the new model needs a different `base_url` (e.g., switching from llama.cpp to vLLM), update that too.
3. Commit, merge, restart.

### Managing the bot's Signal account
The bot doesn't manage its own Signal account — `signal-cli` does, and registering/unregistering Signal accounts is a separate operation against the `signal-cli` pod's PVC. See `docs/operations/2026-05-03-signal-cli-account-management.md` for the linking/registration procedure.

## 6. Testing

```bash
# Pod up and steady?
kubectl get pod -n hermes

# Has it logged a successful Signal SSE registration?
kubectl logs -n hermes deploy/hermes | grep -i "signal\|connected\|registered"

# PVC utilization (should stay <50% with auto_prune on)
kubectl exec -n hermes deploy/hermes -- df -h /opt/data
```

Round-trip: DM the bot from your phone, confirm a reply lands within ~30s for a trivial prompt. If the reply takes longer or never arrives, walk down the chain in section 9.

## 7. Monitoring & Alerting
- **Metrics**: Hermes does not expose Prometheus metrics. The pod's CPU/memory show up in the generic Application Health dashboard via the `app: hermes` label.
- **Logs**: structured to stdout.
  ```bash
  kubectl logs -n hermes deploy/hermes -f
  ```
  Hot signals to grep for: `ERROR`, `signal`, `model`, `tool_use`. The agent emits a turn-by-turn structure that's verbose under `verbose: true`.
- **Restart count**: a non-zero `RESTARTS` in `kubectl get pod -n hermes` over a 24h window suggests OOM (bump memory limit) or signal-bridge connectivity flapping.

## 8. Disaster Recovery
- **Backup strategy**:
  - `apps/base/hermes/configmap.yaml` (env + `config.yaml`) is in Git.
  - The PVC at `/opt/data` holds session history, checkpoints, and memory. With `checkpoints.auto_prune: true` + `retention_days: 7` it stays bounded; underlying iSCSI snapshots on Synology are the recovery surface for catastrophic loss.
- **Restore procedure**:
  - PVC loss: delete the pod and PVC, re-apply Flux. The pod re-seeds `/opt/data/config.yaml` from the mounted ConfigMap key on first boot. Conversation history and any cron-built artifacts are gone — bot returns with a clean memory.
  - ConfigMap regression: revert the offending commit, Flux reconciles, restart the pod.

## 9. Troubleshooting

- **Bot offline / not responding to DMs**: walk the chain.
  1. Is `hermes` pod `Running`? `kubectl get pod -n hermes`. If `CrashLoopBackOff`, check logs.
  2. Is `signal-cli` pod `Running` in the `signal-cli` namespace? The bot can't receive without it.
  3. Is llama.cpp on hestia reachable? `kubectl exec -n hermes deploy/hermes -- curl -s http://10.42.2.10:8000/v1/models | head`. If empty, GPU host is down.
  4. Are you on the allow-list? `kubectl get cm -n hermes hermes-config -o jsonpath='{.data.SIGNAL_ALLOWED_USERS}'`.

- **Slow replies (>60s on trivial prompts)**: usually llama.cpp queue depth or model swap. Check hestia GPU utilization (`ssh truenas_admin@10.42.2.10 'nvidia-smi'`).

- **PVC full**: check `df -h /opt/data` in the pod. If approaching 100%, `checkpoints.max_snapshots` may be too high or `auto_prune` got disabled. Edit the `hermes-config-yaml` ConfigMap to tighten retention; restart.

- **`hermes gateway` process not detected by probe**: the readiness probe greps for `hermes gateway` in the process table. If the binary crashed and tini hasn't restarted it, the pod will be marked NotReady within `30 * 3 = 90s`. Logs will show the crash cause; livenessProbe (5min grace) will then trigger a restart.

- **Auth issues to signal-bridge**: `HERMES_AUTH_TOKEN` is currently *not* sent by the upstream Hermes Signal adapter. The signal-bridge accepts unauthenticated requests from in-cluster traffic (NetworkPolicy is the only barrier). If a non-Hermes caller is added later that requires auth, that's a future patch on both sides.
