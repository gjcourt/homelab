---
status: planned
last_modified: 2026-05-02
---

# Hestia self-hosted GHA runner — auto-deploy Custom App compose changes

## Context

Today, every change to `hosts/hestia/**/docker-compose*.yml` requires the operator to open SCALE UI → Apps → `<app>` → Edit → paste the new YAML → Save. The compose YAML in git is canonical, but enforcement is human discipline. This plan eliminates the copy-paste step: PR merged → GitHub Actions fires → a self-hosted runner on hestia calls TrueNAS's `app.update` API → the Apps subsystem reconciles the container.

This is the "Option 2a" path from the prior design discussion: the simplest viable architecture given that the GPU and the Custom Apps both already live on hestia. Option 2b (actions-runner-controller in the Talos cluster) is documented as a graduation path, not deployed here.

## Decisions

- **Runner placement** — the runner is a TrueNAS Custom App on hestia itself. Same surface as every other hestia service; no new TrueNAS subsystem.
- **API access** — TrueNAS WebSocket API (`wss://10.42.2.10/api/current`) using a TrueNAS API key created in SCALE UI → Settings → API Keys. We do *not* bind-mount `midclt` into the runner container — fragile across TrueNAS upgrades. The WS API is the supported interface.
- **GitHub registration** — a long-lived PAT (or a GitHub App) on `gjcourt/homelab` with `actions:write` + `metadata:read`. Avoids the 1-hour expiring registration token that comes from the runner UI.
- **Workflow scope** — path filter `hosts/hestia/**/docker-compose*.yml`. Other repo paths (`apps/`, `infra/`, `images/`) do not trigger hestia deploys. Cluster reconciliation stays with Flux.
- **Bootstrap exception** — the runner Custom App itself is the *one* compose that gets pasted into SCALE UI by hand once. From that point on, the runner deploys itself and every other hestia compose change.

## Architecture

```
PR merged on master
        │
        ▼
GHA workflow (deploy-hestia.yml)
        │
        ▼   (path filter on hosts/hestia/**/docker-compose*.yml)
runs-on: [self-hosted, hestia]
        │
        ▼
runner pod on hestia (Custom App)
        │
        ▼   (WS to wss://localhost/api/current with API key)
TrueNAS Apps subsystem
        │
        ▼   (custom_compose_config_string update)
container restarts with new YAML
```

Runner has only LAN visibility into TrueNAS; the API never crosses network boundaries.

## Deliverables

Each row is one execution PR after this plan merges. Sequencing in [Bootstrap order](#bootstrap-order).

### D1 — `hosts/hestia/actions-runner/`

New compose file + README. The compose follows the existing hestia pattern (no `build:`, no `secrets:` block, no `container_name`, host-port binding in SCALE UI).

```yaml
# hosts/hestia/actions-runner/docker-compose.yml (sketch — final form in D1 PR)
services:
  runner:
    image: myoung34/github-runner@sha256:<digest-pinned>
    restart: unless-stopped
    environment:
      REPO_URL: https://github.com/gjcourt/homelab
      RUNNER_NAME: hestia
      LABELS: hestia,truenas
      RUNNER_WORKDIR: /tmp/runner-work
      EPHEMERAL: "false"
      # ACCESS_TOKEN and TRUENAS_API_KEY are set in SCALE UI as masked env vars.
      # Do NOT commit values; YAML in git stays clean.
      ACCESS_TOKEN: ""
      TRUENAS_API_KEY: ""
    volumes:
      - /mnt/main/apps/actions-runner/work:/tmp/runner-work
      - /var/run/docker.sock:/var/run/docker.sock
```

README covers:
- One-time bootstrap: paste this YAML into SCALE UI → Apps → Custom App; set `ACCESS_TOKEN` (PAT) and `TRUENAS_API_KEY` as masked env vars; click Install.
- Token rotation: edit App → update env var → Save.
- Verify online: GitHub repo → Settings → Actions → Runners shows `hestia` with status "Idle".

### D2 — `.github/workflows/deploy-hestia.yml`

```yaml
# sketch — final form in D2 PR
name: Deploy hestia Custom Apps
on:
  push:
    branches: [master]
    paths:
      - 'hosts/hestia/**/docker-compose*.yml'

concurrency:
  group: deploy-hestia
  cancel-in-progress: false

jobs:
  apply:
    runs-on: [self-hosted, hestia]
    strategy:
      matrix:
        include:
          - name: llama
            file: hosts/hestia/llms/docker-compose-llama.yml
          # add new hestia apps here
    steps:
      - uses: actions/checkout@v4
      - name: Apply ${{ matrix.name }}
        env:
          TRUENAS_API_KEY: ${{ env.TRUENAS_API_KEY }}  # injected by runner env
        run: scripts/truenas-update-app.sh "${{ matrix.name }}" "${{ matrix.file }}"
```

Adding a new hestia Custom App later = add one matrix entry. Deleting one = remove the entry (does not delete the running app — that requires `app.delete`, intentionally out of scope).

<!--
GRADUATION PATH (Option 2b — ARC in Talos):
When ≥2 hestia apps actively churn or runner needs scale-to-zero, replace this
workflow's `runs-on: [self-hosted, hestia]` with `runs-on: [self-hosted, arc]`,
install actions-runner-controller as a HelmRelease in infra/controllers/, and
register a runner scale set with the `arc` label. The matrix and script stay.
Decommission the Custom App runner once the new path is verified.
-->

### D3 — `scripts/truenas-update-app.sh`

A small Bash or Python script that:

1. Reads the compose YAML from disk.
2. Builds a JSON-RPC payload: `{"method": "app.update", "params": [<name>, {"custom_compose_config_string": <yaml>}]}`.
3. Opens a WebSocket to `wss://10.42.2.10/api/current` with header `Authorization: Bearer <TRUENAS_API_KEY>`.
4. Sends the payload, waits for response, asserts no error field.
5. Polls `app.query` until status is `RUNNING` or fails after a timeout.

**Method-shape verification** must happen during D3:
```bash
midclt call app.query '[["id", "=", "llama"]]' | jq '.[0] | keys'
midclt call app.query '[["id", "=", "llama"]]' | jq '.[0].custom_compose_config'
```
TrueNAS 26.x quirks (per existing `~/.claude/HOMELAB.md` notes) — confirm the exact field name (`custom_compose_config_string` vs `custom_compose_config`) and whether `app.update` accepts a partial payload or requires the full app spec.

### D4 — Update operator docs

`hosts/hestia/README.md` shifts from "paste YAML manually" to "merge a PR; GHA runner handles it". The manual paste workflow stays documented as the fallback for when the runner is offline or first-time bootstrap of a new app type.

## Bootstrap order

1. Merge **this plan PR** (`docs/plan-hestia-gha-runner`).
2. Merge **D1 PR**. Operator pastes `hosts/hestia/actions-runner/docker-compose.yml` into SCALE UI by hand; sets `ACCESS_TOKEN` + `TRUENAS_API_KEY` env vars; clicks Install. Verify runner online in GitHub Settings → Actions → Runners.
3. Merge **D2 + D3 together**. The workflow now exists but won't fire until a `hosts/hestia/**/docker-compose*.yml` path actually changes.
4. Make a no-op edit to `hosts/hestia/llms/docker-compose-llama.yml` (e.g., comment update). Push as a small PR. On merge, the workflow fires; runner picks up; `truenas-update-app.sh` calls `app.update`; container restarts. Verify via `docker inspect ix-llama-...-1`.
5. Merge **D4** once step 4 succeeds.

After step 4, every future hestia compose edit is hands-off. The only manual-paste cases that remain: (a) creating a brand-new Custom App for the first time, and (b) the runner itself.

## Graduation path — Option 2b (actions-runner-controller in Talos)

Out of scope for this plan, but the path is:

1. Add `actions-runner-controller` HelmRelease under `infra/controllers/`.
2. Configure a `RunnerScaleSet` with label `arc` (or reuse `hestia` if convenient) targeting the same `gjcourt/homelab` repo.
3. Provide GitHub App credentials via SOPS-encrypted Secret in the controller namespace.
4. Update `deploy-hestia.yml` to `runs-on: [self-hosted, arc]`. Pods scale-to-zero when idle.
5. Verify a no-op deploy round-trips through the new runner.
6. Stop the Custom App runner on hestia; remove `hosts/hestia/actions-runner/` from the repo.

**When to graduate:** ≥2 self-hosted-runner workflows exist, or the runner is offline often enough to be a reliability problem, or the SCALE UI Custom App for the runner becomes a bootstrapping/upgrade headache. Not before.

## Verification

- **D1**: GitHub repo → Settings → Actions → Runners shows `hestia` with status "Idle". Killing the Custom App and restarting it brings the runner back online without re-registration (registration is cached in `/mnt/main/apps/actions-runner/work`).
- **D2 + D3**: a no-op edit to `docker-compose-llama.yml` triggers the workflow. Workflow logs show successful `app.update` response. `docker inspect ix-llama-...-1 --format '{{.Config.Cmd}}'` reflects any flag changes.
- **D4**: README states the new flow; old paste fallback present and accurate.

## Out of scope

- **Option 2c** (cloud GHA runner + Tailscale): adds Tailscale dependency; deferred unless we want to retire self-hosted runners entirely.
- **Option 3** (systemd + git-pull on TrueNAS): bypasses SCALE Apps entirely; loses the Apps UI for status/logs. Strictly worse than 2a for this use case.
- **Auto-rotation** of the TrueNAS API key. Manual for now.
- **Removing apps** via `app.delete`. The matrix only adds/updates.
- **Reconciliation of drift** introduced by manual SCALE UI edits. The script blindly applies the YAML in git; if someone edited via UI, those edits are clobbered. This is the desired behavior — git is the source of truth.

## Open questions

- Exact `app.update` payload shape on TrueNAS 26.x — resolve in D3 PR by inspecting `app.query` output and reading the [TrueNAS API docs](https://www.truenas.com/docs/api/scale_websocket_api.html) for the deployed version.
- Whether the runner should also handle non-`hosts/hestia/` paths in future (e.g., a future `hosts/synology/`). Defer until a second host appears.
- Whether to use a GitHub App vs. PAT for runner registration. PAT is simpler; GitHub App is more rotatable. Defer to D1 implementation.

## Cross-references

- Companion plan: [`2026-05-02-hermes-bot-k8s.md`](2026-05-02-hermes-bot-k8s.md). Hermes-bot is k8s-native and Flux-managed, so it does *not* depend on this runner. Mentioned for context only.
- Operator manual paste fallback: [`hosts/hestia/README.md`](../../hosts/hestia/README.md).
