---
status: planned
last_modified: 2026-05-02
---

# Hermes-bot — always-on Signal agent on melodic-muse

## Context

Today, Hermes (the [NousResearch agent](https://hermes-agent.nousresearch.com/)) runs as a long-lived process on the operator's laptop. The Signal-bot platform (`platform_toolsets.signal: [hermes-signal]`) is bound to that local process — when the laptop sleeps, the bot goes dark. This plan deploys a separate Hermes instance to the Talos cluster (`melodic-muse`) in Signal-only mode so the bot persona is always-on, independent of the laptop.

The local Hermes CLI continues to run on the laptop for interactive use. The two instances share the LLM backend (llama.cpp on hestia) but **own separate session/checkpoint state** — they are different personas with different memories.

This is the "Option A" path from the prior design discussion. "Option D" — laptop hermes hands off tasks to the bot via Signal DM — is the integration pattern between the two instances and works on day 1 with no extra code. "Option E" (proper RPC delegation via an MCP shim) is documented as a future graduation, deferred.

## Note on supersession

The existing [`signal-cli-hermes-rollout.md`](signal-cli-hermes-rollout.md) (status: `planned`) called for a TrueNAS Custom App for the signal-cli + signal-bridge stack. The implementation went k8s-native instead: `apps/base/signal-cli/` exists with both containers as sidecars in a single Pod, exposed via the `signal-cli-bridge` ClusterIP service. That earlier plan is **partially superseded** — its D1 (productionize signal-bridge) and D3 (repo restructure under `hosts/`) landed; its D2 (TrueNAS Custom App for the stack) was replaced by the k8s deployment.

The hermes-bot D2 PR (below) is where we flip `signal-cli-hermes-rollout.md` to `status: superseded` and remove the now-orphaned `hosts/hestia/signal/` directory.

## Decisions

- **Single replica**, `Recreate` strategy — sessions don't cluster, only one process should be writing checkpoints.
- **PVC at `/home/hermes/.hermes/`** — sessions, checkpoints, memory; 5 GiB iSCSI per repo convention. Checkpoint retention tuned down (`max_snapshots: 10`, `auto_prune: true`) to keep the PVC reasonable.
- **Inference**: `http://10.42.2.10:8000/v1` — llama.cpp directly. No llmux hop; llama.cpp's `hermes` chat template emits clean tool calls. (Local laptop hermes can drop llmux for the same reason; that's a config change, not infra.)
- **Signal**: `http://signal-cli-bridge.signal-cli.svc.cluster.local:8080` — in-cluster ClusterIP from `apps/base/signal-cli/service.yaml`. Bearer token (`HERMES_AUTH_TOKEN`) in a SOPS-encrypted Secret.
- **No HTTP ingress** — Signal-only bot. No HTTPRoute, no Gateway entry.
- **Namespace**: `hermes` (production), `hermes-stage` (staging) per repo convention.
- **Container image**: pip-install `hermes-agent` from PyPI in a Python base, published as `ghcr.io/gjcourt/hermes-bot:YYYY-MM-DD[-N]` via a GHA workflow mirroring `build-signal-bridge.yml`. **If NousResearch publishes an official image**, use it directly and skip the build PR.
- **Toolsets** — bot agent runs with the Signal platform toolset (`hermes-signal`) plus a conservative agent toolset (`web` + `file` for read-only browsing; **no `terminal`** in v1 to limit blast radius). Operator can opt in to terminal access later by editing the ConfigMap.

## Architecture

```
phone (Signal) ──► signal-cli (k8s pod) ──► signal-bridge (sidecar, :8080) ──► hermes-bot (k8s pod)
                                                                                       │
                                                                                       ▼
                                                                       llama.cpp on hestia (10.42.2.10:8000)
                                                                                       │
                                                                                       ▼
                                                                                response back through the same chain
```

All traffic stays on the LAN. Bot state lives on iSCSI PVC; nothing in-pod is durable.

## Deliverables

Each row is one execution PR after this plan merges.

### D1 — `images/hermes-bot/` (skip if upstream image exists)

- `Dockerfile` — Python base, `pip install hermes-agent`, entrypoint runs `hermes signal --account +16179397251` (or whatever the upstream signal-mode invocation is).
- `README.md` — purpose, env vars, how to run locally.
- `.github/workflows/build-hermes-bot.yml` — multi-arch build to `ghcr.io/gjcourt/hermes-bot:YYYY-MM-DD[-N]` on push to `master` touching `images/hermes-bot/`. Mirror `.github/workflows/build-signal-bridge.yml`.

**Skip condition**: if `nousresearch/hermes-agent` (or similar) is published, point straight at it in D2.

### D2 — `apps/base/hermes/` + cleanup

New k8s base app:

- `namespace.yaml` — `hermes` namespace, `http-ingress: "false"` label.
- `deployment.yaml` — 1 replica, `Recreate`, image from D1 (or upstream), env from ConfigMap + Secret, PVC mount at `/home/hermes/.hermes/`, readiness probe checking the hermes process is up (no HTTP probe; bot has no listener).
- `pvc.yaml` — 5 GiB iSCSI, RWO.
- `configmap.yaml` — hermes config sans secrets: model selection, signal endpoint URL, toolset list, checkpoint tuning.
- `kustomization.yaml`.

Same PR cleanup:
- Delete `hosts/hestia/signal/` — superseded by k8s signal-cli.
- Flip `docs/plans/signal-cli-hermes-rollout.md` front-matter to `status: superseded` + add a one-line note pointing here.

### D3 — `apps/staging/hermes/`

- Overlay with `hermes-stage` namespace.
- SOPS-encrypted Secret: `HERMES_AUTH_TOKEN` (signal-bridge bearer), any LLM provider keys if used.
- Env-specific tweaks: lower checkpoint retention, possibly debug logging.
- Wire into `apps/staging/kustomization.yaml`.

### D4 — `apps/production/hermes/`

Promote after staging soak (≥48h, no OOM/restart, end-to-end Signal round-trip verified). Same shape as D3.

Wire into `apps/production/kustomization.yaml`.

### D5 — `docs/apps/hermes.md` runbook

- Purpose, architecture diagram, dashboards (if any).
- How to switch model: edit ConfigMap → `flux reconcile kustomization apps-production -n flux-system`.
- How to add allowed Signal accounts: edit signal-cli's `HERMES_ALLOWED_ACCOUNTS`, restart that pod (separate from hermes-bot).
- Common failures: bot offline (check llama.cpp on hestia), Signal disconnect (check signal-cli pod), PVC full (`auto_prune` misconfigured).

## Bootstrap order

1. Merge **this plan PR** (`docs/plan-hermes-bot-k8s`).
2. Merge **D1** (image build infra). First successful GHA build appears in `ghcr.io/gjcourt/hermes-bot`. Skip if upstream image is used.
3. Merge **D2 + D3 together**. Staging overlay deploys via Flux. Verify in `kubectl logs -n hermes-stage deploy/hermes`.
4. **Soak ≥48h** in staging. Send DMs to the bot's Signal number; observe behavior; check PVC growth.
5. Merge **D4** (production overlay).
6. Merge **D5** (runbook) once production is stable.

## Option D — laptop ↔ k8s delegation today

The "delegate via Signal" pattern works on day 1 with no extra code. Convention:

- Laptop hermes user issues a `/handoff <task>` slash command (or a one-line `signal-send` script).
- That sends a Signal message to `+16179397251`.
- The k8s hermes-bot picks it up via signal-bridge → SSE → its agent loop.
- Bot replies via Signal; laptop user reads on phone or in laptop hermes if it's tailing the same number.

Latency-bounded by Signal delivery (sub-second on LAN, a few seconds external). Good enough for fire-and-forget background tasks. Multi-message threads / attachments work natively because Signal supports them.

This is not an MCP-style RPC — it's two agents communicating through a chat channel. Intentional simplicity.

## Graduation path — Option E (MCP delegation)

Out of scope for this plan. Tracked for when Option D becomes inconvenient (multi-message tasks with structured payloads, attachment-heavy delegation, latency-sensitive handoffs).

Sketch:

1. Wrap k8s hermes-bot's session API as an MCP server. Likely a small Python or Go shim that exposes `hermes.delegate(prompt) → session_id` and `hermes.poll(session_id) → status, output`.
2. Expose the MCP endpoint via in-cluster Service (no public ingress — laptop reaches it via Tailscale or the existing kubeconfig).
3. Configure local hermes `mcp:` toolset to point at the cluster endpoint.
4. Verify bidirectional: laptop hermes spawns a k8s subagent, polls for completion, receives structured output.

**When to graduate:** when Option D's chat-shaped delegation becomes a meaningful UX bottleneck. Not before — the MCP shim is real engineering and the fallback (DM the bot) already works.

## Verification

### Staging (after D2 + D3)

- `kubectl logs -n hermes-stage deploy/hermes` — process started, registered SSE stream with signal-bridge, no auth errors.
- `kubectl get pods -n hermes-stage` — single pod, ready, no restarts after 5 min.
- DM the staging bot's number from a phone → reply received.
- `kill` the laptop hermes process → bot continues to respond. Confirms laptop independence.
- First-token latency < 5s for short prompts; total round-trip < 30s for typical request.
- `kubectl exec -n hermes-stage deploy/hermes -- df -h /home/hermes/.hermes/` — PVC < 50% full after 48h.

### Production (after D4)

- All staging checks pass.
- `flux get kustomizations -n flux-system | grep hermes` — both `hermes-base` and `hermes-production` healthy.
- 48h soak — zero OOM, zero CrashLoopBackOff, zero unexpected pod restarts.
- Operator can issue `/handoff` from the laptop and receive a useful reply.

## Out of scope

- **Multi-account hermes** — single Signal number for now (`+16179397251`). The bridge already supports multi-account via the `account` query param if we ever want to add a second number.
- **Other platforms** — no Discord, Telegram, Slack, Mattermost, WhatsApp. Signal-only.
- **Voice / TTS / STT** — `tts:` and `stt:` blocks left at defaults; not exercised by Signal.
- **HTTP / MCP API** — Option E graduation path. Not in this plan.
- **Shared sessions or memory between laptop and k8s bot** — explicitly separate. Each has its own `~/.hermes/` directory and its own conversation history. Crossing them is an Option E concern.
- **In-cluster llmux** — explicitly decided against. llama.cpp's hermes template emits clean tool calls; llmux is unnecessary on the bot path. Local laptop llmux can be retired separately when the operator drops the localhost:9090 provider from their laptop config.
- **Replacing local Hermes CLI** — laptop hermes stays. Different role, different state.

## Open questions for execution phase

- **Upstream container?** — does NousResearch publish a `hermes-agent` image? If yes, use it; D1 collapses to just a Dockerfile-free pull. If no, build from PyPI.
- **Toolset opt-ins** — the bot ships with `web` + `file` (read-only) by default. Does the operator want `terminal` enabled from day 1, or is that earned after a soak period? Decision before D2.
- **Checkpoint retention tuning** — `max_snapshots: 10` is a guess. Revisit once we see real PVC usage in staging.
- **Personality default** — does the bot inherit the operator's `display.personality: kawaii`, or pick something else? Decision before D2; cosmetic, not load-bearing.
- **Prompt caching** — `prompt_caching.cache_ttl: 5m` works for a short-session bot but may cost more LLM tokens than caching a longer TTL. Tune in staging.

## Cross-references

- Companion plan: [`2026-05-02-hestia-gha-runner.md`](2026-05-02-hestia-gha-runner.md). The GHA runner deploys hestia Custom Apps; hermes-bot is k8s-native and Flux-managed, so it does *not* depend on that work. Mentioned for context.
- Superseded by this plan's D2: [`signal-cli-hermes-rollout.md`](signal-cli-hermes-rollout.md) D2 (TrueNAS Custom App for signal stack).
- Referenced infra: [`apps/base/signal-cli/`](../../apps/base/signal-cli/), [`hosts/hestia/llms/docker-compose-llama.yml`](../../hosts/hestia/llms/docker-compose-llama.yml).
