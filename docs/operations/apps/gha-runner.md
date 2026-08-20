# GHA Runner

## 1. Overview
`gha-runner` is the self-hosted GitHub Actions runner that gives this repo hands on the two boxes that are **not** Kubernetes nodes. It is the mechanism by which a merge to `master` becomes a change on a physical host:

- **hestia** (TrueNAS SCALE, `10.42.2.10`) — [`hosts/hestia/actions-runner/`](../../../hosts/hestia/actions-runner/). Runs [`deploy-hestia.yml`](../../../.github/workflows/deploy-hestia.yml), which applies `hosts/hestia/**/docker-compose*.yml` changes through the TrueNAS WebSocket API (`app.update`) via [`scripts/truenas-update-app.sh`](../../../scripts/truenas-update-app.sh).
- **alcatraz** (Synology DSM, `10.42.2.11`) — [`hosts/alcatraz/actions-runner/`](../../../hosts/alcatraz/actions-runner/). Runs [`alcatraz-deploy.yaml`](../../../.github/workflows/alcatraz-deploy.yaml), which applies `hosts/alcatraz/**` changes with a direct `docker compose up -d` against a bind-mounted Docker socket (Synology has no TrueNAS API).

Both register against `gjcourt/homelab` at repo scope, both are the *one* compose on their host that an operator brings up by hand, and both are labelled by hostname (`hestia,truenas` / `alcatraz`).

Design docs: [`docs/plans/2026-05-02-hestia-gha-runner.md`](../../plans/2026-05-02-hestia-gha-runner.md), [`docs/plans/2026-06-26-alcatraz-gitops-docker.md`](../../plans/2026-06-26-alcatraz-gitops-docker.md). Per-host bootstrap: [`hosts/hestia/actions-runner/README.md`](../../../hosts/hestia/actions-runner/README.md), [`hosts/alcatraz/actions-runner/README.md`](../../../hosts/alcatraz/actions-runner/README.md).

### The runner is deliberately excluded from auto-deploy
This is the single most important fact about operating it.

`deploy-hestia.yml` **unconditionally excludes** `hosts/hestia/actions-runner/` from its discover matrix, and `alcatraz-deploy.yaml` does the same for `hosts/alcatraz/actions-runner/`. The reason is chicken-and-egg: a runner cannot reliably deploy a compose change that recreates its own container — it would be killing the process executing the job that is doing the killing, mid-apply, with no way to report the result.

**Consequence: runner upgrades are manual.** Renovate will happily open and merge a digest bump for the runner image, CI will go green, and *nothing will apply it*. The repo says one thing and the box runs another until a human opens the SCALE UI. This is not a bug to fix; it is a trade-off that has to be *watched*, which is what §7 is for.

The self-concealing shape of it: when the runner is broken, the fix for the runner is itself gated behind the broken thing. See §8.

## 2. Architecture
```
   git push master
        │
        ▼
   GitHub Actions ──────dispatch──────▶ ┌──────────────────────────────┐
   (deploy-hestia.yml)                  │ hestia (TrueNAS SCALE)       │
        ▲                               │                              │
        │  runner long-polls for work   │  ┌────────────────────────┐  │
        └───────────────────────────────┼──│ gha-runner (Custom App)│  │
                                        │  │ myoung34/github-runner │  │
                                        │  │ restart: unless-stopped│  │
                                        │  └───────────┬────────────┘  │
                                        │              │ docker.sock   │
                                        │              ▼               │
                                        │  truenas-update-app.sh       │
                                        │  → midclt app.update         │
                                        │  → 9 other Custom Apps       │
                                        └──────────────────────────────┘
```

- **Image**: `myoung34/github-runner`, pinned **tag + digest** — `:ubuntu-noble@sha256:de596f58...`. Both halves matter; see §9.
- **Deployment shape on hestia**: a TrueNAS SCALE **Custom App** named `gha-runner`, container `ix-gha-runner-runner-1`. SCALE stores the compose in an app database (`app.config`) and renders it to disk; the rendered file is not the source of truth (§6).
- **Restart policy**: `restart: unless-stopped`. This is what turns a persistent startup failure into an unbounded crash loop rather than a stopped container — a stopped container is at least visible in `docker ps -a`, a crash loop looks "running" at any given instant.
- **Persistence**: `/mnt/main/apps/actions-runner/work` (registration state `.runner` lives here).
- **Privilege**: `/var/run/docker.sock` is bind-mounted. The runner can do anything Docker can do on hestia.
- **Healthcheck**: `pgrep -f Runner.Listener`, 60s interval. Note this bought us **nothing** during the incident — nothing queries the healthcheck result, and a crash-looping container reports `starting` rather than `unhealthy`, so the one signal it emits is the wrong one.
- **Auto-update**: `DISABLE_AUTO_UPDATE: "false"` (see §9).

## 3. URLs
- **Runner status**: https://github.com/gjcourt/homelab/settings/actions/runners — the authoritative view of `Idle` / `Active` / `Offline`.
- **Workflow runs**: https://github.com/gjcourt/homelab/actions/workflows/deploy-hestia.yml
- **TrueNAS SCALE UI**: https://10.42.2.10 → Apps → `gha-runner`
- **Prometheus**: https://prometheus.burntbytes.com — `homelab_gha_runner_up`, `homelab_gha_runner_restart_count`, `homelab_gha_runner_last_check_timestamp_seconds`
- **Alertmanager**: https://alertmanager.burntbytes.com

## 4. Configuration
Canonical compose lives in the repo; the values of the two secrets never do.

| Variable | Purpose | Where the value lives |
|----------|---------|-----------------------|
| `ACCESS_TOKEN` | PAT / GitHub App token used to mint a short-lived registration token at startup. Needs repo **Administration: read/write** (or classic `repo`) — `Contents` alone yields `curl: (22) ... 403`. | Masked env var, SCALE UI (hestia) / `.env` (alcatraz) |
| `TRUENAS_API_KEY` | Used by `truenas-update-app.sh` to call the WebSocket API. hestia only. | Masked env var, SCALE UI |
| `REPO_URL`, `RUNNER_NAME`, `RUNNER_SCOPE`, `LABELS` | Registration identity. `LABELS` is what `runs-on:` matches. | Compose, committed |
| `EPHEMERAL` | `"false"` — the runner is long-lived, not job-scoped. | Compose, committed |
| `DISABLE_AUTO_UPDATE` | `"false"` — the runner self-updates its *software* when GitHub requires a newer version. | Compose, committed |

### Updating the image
Find the digest for the **tag you intend**, never for `latest`:
```bash
docker manifest inspect myoung34/github-runner:ubuntu-noble \
  | jq -r '.manifests[] | select(.platform.architecture=="amd64" and .platform.os=="linux") | .digest'
```
Edit the `image:` line in both `hosts/hestia/actions-runner/docker-compose.yml` and `hosts/alcatraz/actions-runner/docker-compose.yml`, keeping `:ubuntu-noble@sha256:...` intact. Merge — then **apply it by hand** (§6), because nothing will apply it for you.

## 5. Usage Instructions
The runner has no UI. You interact with it through GitHub and through `docker` on the host.

```bash
# Is it registered and idle? (authoritative)
open https://github.com/gjcourt/homelab/settings/actions/runners

# What is it actually running?
ssh truenas_admin@10.42.2.10 'docker inspect ix-gha-runner-runner-1 \
  --format "{{.Config.Image}} restarts={{.RestartCount}} state={{.State.Status}}"'

# Recent log — "Listening for Jobs" is the healthy steady state
ssh truenas_admin@10.42.2.10 'docker logs --tail 50 ix-gha-runner-runner-1'

# What TrueNAS believes the app should be (the source of truth)
ssh truenas_admin@10.42.2.10 'sudo midclt call app.config gha-runner' | jq .
```

Pause it: SCALE UI → Apps → `gha-runner` → Stop. Workflows queue at GitHub until it comes back — they do **not** fail.

Re-register from scratch: delete `/mnt/main/apps/actions-runner/work/.runner`, restart the app.

## 6. Testing
A runner is working when all three of these hold. Checking only the first is how a day of queued deploys goes unnoticed.

1. **Registered**: the runner shows `Idle` at the settings URL above, with the expected labels.
2. **Stable**: `RestartCount` is flat across two observations a minute apart, and the log's last lines are `Listening for Jobs` with no `deprecated` line.
3. **Actually executing**: re-run the most recent `deploy-hestia.yml` run and watch it pick up. This is the only check that proves the whole path — GitHub dispatch → runner accept → apply → report. A container that is `running` proves none of it.

```bash
gh run list --workflow=deploy-hestia.yml --limit 5
gh run rerun <run-id> --repo gjcourt/homelab
```

Verify the running image matches the repo after any change:
```bash
grep image: hosts/hestia/actions-runner/docker-compose.yml
ssh truenas_admin@10.42.2.10 'docker inspect ix-gha-runner-runner-1 --format "{{.Image}} {{.Config.Image}}"'
```

## 7. Monitoring & Alerting
Three alerts in the `gha-runner` group of [`infra/configs/alerts/prometheus-rules.yaml`](../../../infra/configs/alerts/prometheus-rules.yaml), over a metric family emitted by the container probe on hestia and exported via the node-exporter textfile collector:

```
homelab_gha_runner_up{runner}                            1 = healthy, 0 = not
homelab_gha_runner_restart_count{runner}                 docker RestartCount, gauge
homelab_gha_runner_last_check_timestamp_seconds{runner}  unix seconds
```

**Routing.** `severity: critical` → `email-critical` → `gjcourt+critical@gmail.com`, 4h repeat, no Gmail skip-inbox filter — it reaches the inbox, i.e. it **pages**. `severity: warning` → `email-warning` → `gjcourt+alerts@gmail.com`, 24h repeat, filtered out of the inbox — an opt-in nag. An alert with **no** `severity` label falls through to the default `null` receiver and is discarded silently. A genuinely broken runner is therefore critical by construction.

| Alert | Expression | Severity | Meaning and response |
|-------|-----------|----------|----------------------|
| `GHARunnerDown` | `homelab_gha_runner_up == 0` for 10m | **critical** (pages) | The runner is not healthy. Every workflow targeting it is now **queued, not failed** — GitHub will never email about this, so this alert is the only signal. **Response:** confirm at the runners settings page (`Offline`?); `docker logs --tail 50 ix-gha-runner-runner-1`; if the container is gone or stopped, SCALE UI → Apps → `gha-runner` → Start. If it comes up and immediately dies, treat it as a crash loop below. Once healthy, **re-run every queued/skipped `deploy-hestia.yml` run** — the queue does not drain retroactively for runs GitHub already cancelled. |
| `GHARunnerCrashLooping` | `increase(homelab_gha_runner_restart_count[15m]) > 5` for 5m | **critical** (pages) | The container is exiting and being restarted forever by `unless-stopped`. This is the 2026-08-19 detector; it fires in ~6 minutes. **Response:** `docker logs --tail 50 ix-gha-runner-runner-1 \| grep -iE 'deprecated\|error'`. If it is the deprecation signature (§8), the runner image is too old *and* cannot self-update — go to §8's recovery. Otherwise look for a bad `ACCESS_TOKEN` (403 at registration) or a missing `/mnt/main/apps/actions-runner/work`. |
| `GHARunnerMetricStale` | `time() - homelab_gha_runner_last_check_timestamp_seconds > 900` for 10m | warning (filtered mailbox) | The probe has stopped reporting. The runner may be perfectly fine — what has failed is our **ability to see it**, which means the two critical alerts above are now blind. **Response:** check the probe container on hestia and the node-exporter textfile collector at `:9100`; confirm the `.prom` file is being written and is fresh. Until it is fixed, verify the runner by hand (§6) rather than trusting silence. |

### What this does not cover yet
- **Absent series.** All three expressions need the series to exist. If the emitter vanishes entirely rather than going stale, `== 0` matches nothing and the staleness maths has nothing to evaluate. The fix is an `absent()` guard mirroring `HomelabscopeJobMetricAbsent`; it is deliberately deferred until the series are confirmed live in Prometheus, because `absent()` over a not-yet-existing series is a guaranteed false critical the moment it lands. Same two-PR pattern the homelabscope guards already use.
- **A run stuck queued while the runner looks healthy.** Covered by the proposed hourly canary workflow (an off-cluster healthchecks.io check driven by a job that actually runs on the runner), not by these metrics. See [`docs/plans/2026-08-18-hestia-deploy-monitoring-gap.md`](../../plans/2026-08-18-hestia-deploy-monitoring-gap.md) §B.
- **A deploy that reports success and does nothing.** That is the separate, documented `app.update` no-op quirk — [`docs/operations/2026-05-14-truenas-app-update-quirk.md`](../2026-05-14-truenas-app-update-quirk.md).
- **The Kubernetes container alerts do not apply here.** `ContainerCrashLoopBackOff`, `ContainerOOMKilled` and `ContainerHighCPU` read `kube_pod_container_status_*` / cAdvisor. hestia is not a node in this cluster and runs no cAdvisor. Those alert *names* read as coverage that does not exist for this host.

## 8. Disaster Recovery

### The 2026-08-19 incident
A merge to `master` touching `hosts/hestia/**` produced a `deploy-hestia.yml` run that sat **queued for over a day**. The runner had restarted **13,173 times**. Nothing alerted — not the crash loop, not the offline runner, not the queued run.

Log signature:
```
Current runner version: '2.334.0'
An error occurred: Runner version v2.334.0 is deprecated and cannot receive messages.
```

The loop: the runner registered with GitHub successfully, was told its version was too old, exited 1, and `restart: unless-stopped` started it again. Forever. It could not self-heal because `DISABLE_AUTO_UPDATE` was `"true"`, pinning it to whatever runner binary the image shipped. And it could not deploy its own fix, because `hosts/hestia/actions-runner/` is excluded from `deploy-hestia.yml` (§1). Every layer of repair was downstream of the broken thing.

`ACCESS_TOKEN` and registration were fine throughout. **A runner that registers successfully can still be completely unable to do work** — do not stop at "it's registered".

### The trap that cost the most time: bare digest, no tag
Fixing the deprecation uncovered a second fault, and this is the part worth remembering.

The compose pinned a **bare digest with no tag**. Renovate therefore had no tag to track and defaulted to `:latest` — and for `myoung34/github-runner`, `:latest` is **Ubuntu focal**. So a routine "digest bump" PR, which looks like a no-op to any reviewer, silently moved the runner's **base OS back two LTS releases**:

```
old 420562d4   Ubuntu 24.04 noble   Python 3.12.3   pip 24.0     runner 2.334.0
new 56e9f5c5   Ubuntu 20.04 focal   Python 3.8.10   pip 20.0.2   runner 2.336.0
```

`scripts/truenas-update-app.sh` hardcoded `--break-system-packages`, a pip flag introduced in **pip 23.0.1** (PEP 668) and required on noble. focal's pip 20.0.2 does not merely ignore it — it **rejects** it, exits 2, and fails the apply. Every one of the nine hestia apps failed to deploy, with a misleading trace (the script's `echo` printed a command that did not match the one it actually ran).

Two fixes, belt and braces, both in `c168ce9b` (#1318):

1. **Pin tag + digest** — `myoung34/github-runner:ubuntu-noble@sha256:de596f58...`. The tag is what stops Renovate wandering between base-OS variants; the digest still gives reproducibility. **Never pin a bare digest.** A digest alone tells Renovate nothing about which lineage you meant to be on.
2. **Detect the flag instead of assuming it** — probe `python3 -m pip install --help` for `--break-system-packages` and pass it only when supported. The script is now correct on both old and new pip, so a future variant drift degrades instead of breaking.

The general lesson: **a digest bump is not a safe change when the tag is implicit.** The reviewable unit was a hex string, and the actual change was the operating system.

### Recovery when the runner is down and cannot deploy its own fix
This is the deadlock. The change that repairs the runner lives in a repo whose only path to hestia is the runner. Break it by hand.

⚠️ **Read the warning at the end of this section before you start.**

```bash
ssh truenas_admin@10.42.2.10

# 1. Confirm the failure mode
docker inspect ix-gha-runner-runner-1 --format '{{.Config.Image}} restarts={{.RestartCount}}'
docker logs --tail 50 ix-gha-runner-runner-1 | grep -iE 'deprecated|error'

# 2. Patch the RENDERED compose in place
sudo vi /mnt/.ix-apps/app_configs/gha-runner/versions/1.0.0/templates/rendered/docker-compose.yaml
#    → set image: to the intended tag+digest, e.g.
#      myoung34/github-runner:ubuntu-noble@sha256:de596f58...
#    → set DISABLE_AUTO_UPDATE: "false"

# 3. Bring it up from that file
cd /mnt/.ix-apps/app_configs/gha-runner/versions/1.0.0/templates/rendered
sudo docker compose -p ix-gha-runner up -d

# 4. Verify: 2.336.0+ and "Listening for Jobs", no deprecation line, restarts flat
docker logs --tail 30 ix-gha-runner-runner-1
docker inspect ix-gha-runner-runner-1 --format '{{.Config.Image}} restarts={{.RestartCount}}'
```

Then re-run the failed/queued workflow runs (`gh run rerun <id>`) and confirm the applies go green.

> ⚠️ **This is a TEMPORARY layer, not the fix.**
> TrueNAS's stored `app.config` is **authoritative**. The rendered compose you just edited is an output, and SCALE regenerates it from `app.config` on any redeploy — clicking Edit → Save, an app update, or in some cases a reboot will **revert your patch and restore the crash loop**, with no warning.
>
> The durable fix is **SCALE UI → Apps → `gha-runner` → Edit**: update the compose YAML there (image tag+digest, `DISABLE_AUTO_UPDATE`) and Save. Confirm it took with:
> ```bash
> ssh truenas_admin@10.42.2.10 'sudo midclt call app.config gha-runner' | jq -r '.. | .image? // empty'
> ```
> If that still shows the old digest, you are running on borrowed time. Note also that `app.update` is known to return `SUCCESS` without recreating a crash-looping container — see [`2026-05-14-truenas-app-update-quirk.md`](../2026-05-14-truenas-app-update-quirk.md) — so always verify against `docker inspect`, never against the job status.
>
> Finally: mirror whatever you set into the repo compose. If the box and `hosts/hestia/actions-runner/docker-compose.yml` disagree, git has stopped describing reality and the next person debugging this starts from a lie.

### `DISABLE_AUTO_UPDATE` is now `false`
This is the durable half of the fix, and the reason a future deprecation should not become an outage.

Pinning a current image clears **today's** deprecation. Auto-update is what stops the **next** one becoming an incident. The outage was not caused by the runner being old — it was caused by the runner being **unable to stop being old**. With `DISABLE_AUTO_UPDATE: "false"`, GitHub's runner self-updates its own binary in place when a minimum version is enforced, and the crash loop never starts.

The cost is honest and worth naming: the running runner *software* version will now drift from the pinned image, so any strict image↔runtime drift check must exempt the runner binary. What auto-update replaces is the runner software inside the container — **not** the image, not its OS packages. That matters for alcatraz specifically, whose apply step needs the `docker-compose-plugin` layer from the image; a self-update leaves that layer intact.

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Workflow runs sit **queued** forever, never fail | Runner offline or crash-looping. GitHub does not email on queued runs. | §8 recovery. Check `RestartCount` first — it is the fastest discriminator. |
| `Runner version vX.Y.Z is deprecated and cannot receive messages` | Image ships a runner GitHub no longer accepts, and auto-update is off. | Confirm `DISABLE_AUTO_UPDATE: "false"`; bump image tag+digest; §8 recovery to apply. |
| Runner registers, then exits 1 immediately, repeatedly | Same as above. Registration succeeding proves nothing about job dispatch. | Read the log line *after* registration, not the registration line. |
| `--break-system-packages: no such option` / pip exits 2 during apply; all hestia apps fail | Image drifted to focal (pip 20.0.2). Almost always a bare-digest Renovate bump. | Verify with `docker exec ix-gha-runner-runner-1 cat /etc/os-release`. Re-pin `:ubuntu-noble@sha256:...`. The script now detects the flag, so this should degrade rather than break. |
| Repo compose and running container disagree | The runner is excluded from auto-deploy — a merged change was never applied. | Apply by hand via SCALE UI → Edit. Expect this after every Renovate runner bump. |
| Fix applied, works, then silently reverts later | You patched the *rendered* compose only; `app.config` overwrote it. | Set it in SCALE UI → Apps → `gha-runner` → Edit. See the warning in §8. |
| `app.update` returns `SUCCESS` but nothing changed | Known TrueNAS quirk with crash-looping containers. | [`2026-05-14-truenas-app-update-quirk.md`](../2026-05-14-truenas-app-update-quirk.md) — stop and start the app explicitly. |
| Runner never appears in GitHub; log shows `curl: (22) ... 403` | `ACCESS_TOKEN` lacks repo **Administration** (or classic `repo`). `Contents` is not enough. | Regenerate the PAT with the right scopes, update the masked env var. |
| `truenas-update-app.sh` returns 401 | Bad or rotated `TRUENAS_API_KEY`. | SCALE UI → Settings → API Keys; update the masked env var. |
| Runner offline after a host reboot | Persistence path missing. | Confirm `/mnt/main/apps/actions-runner/work` exists and is writable. |
| Alerts silent while the runner is visibly broken | `GHARunnerMetricStale` firing (or the series is absent entirely — not yet guarded). | Fix the probe / textfile collector. Verify the runner by hand meanwhile (§6). |
