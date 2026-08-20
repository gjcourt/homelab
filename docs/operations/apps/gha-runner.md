# gha-runner — self-hosted GitHub Actions runner on hestia

## 1. Overview

`gha-runner` is the self-hosted GitHub Actions runner that applies every
`hosts/hestia/**/docker-compose*.yml` change to the TrueNAS Apps subsystem. It
is the **only** path by which hestia Custom Apps get deployed from Git — if it
is down, every hestia deploy silently queues instead of failing.

| Attribute | Value |
|---|---|
| Host | hestia (`10.42.2.10`), TrueNAS SCALE Custom App |
| Compose | [`hosts/hestia/actions-runner/docker-compose.yml`](../../../hosts/hestia/actions-runner/docker-compose.yml) |
| Bootstrap / token rotation | [`hosts/hestia/actions-runner/README.md`](../../../hosts/hestia/actions-runner/README.md) |
| Image | `myoung34/github-runner` (digest-pinned) |
| Container name | `gha-runner` (docker: `ix-gha-runner-runner-1`) |
| Runner name / labels | `hestia` / `hestia`, `truenas` |
| Consumer | `.github/workflows/deploy-hestia.yml`, job on `runs-on: [self-hosted, hestia]` |
| Design | [`docs/plans/2026-05-02-hestia-gha-runner.md`](../../plans/2026-05-02-hestia-gha-runner.md) |
| Monitoring | [`docs/plans/2026-08-18-hestia-deploy-monitoring-gap.md`](../../plans/2026-08-18-hestia-deploy-monitoring-gap.md) |

## 2. The two facts that make this runner surprising

### 2.1 It is ephemeral, so constant restarting is NORMAL

The image's `/entrypoint.sh` gates on **variable presence, not truth**:

```sh
if [ -n "${EPHEMERAL}" ]; then
  ARGS+=("--ephemeral")
fi
if [ -n "${DISABLE_AUTO_UPDATE}" ]; then
  ARGS+=("--disableupdate")
fi
```

`[ -n "$VAR" ]` is true for *any non-empty string*. `EPHEMERAL: "false"` in the
compose file is a non-empty string, so **`--ephemeral` is active** despite the
value reading `false`.

Consequence: the runner registers, executes **exactly one job**, deregisters,
exits 0, and `restart: unless-stopped` starts a fresh container for the next
job. Measured: `RestartCount` went **0 → 9 across a single 10-job deploy**.

**Do not read a rising `RestartCount` as a fault.** Tens of restarts per hour is
a busy deploy day. Thousands is a crash loop. See §4 for where the line is.

### 2.2 Auto-update is disabled, and "false" will not turn it back on

Same presence-test bug: setting `DISABLE_AUTO_UPDATE: "false"` still passes
`--disableupdate`. **The only way to re-enable auto-update is to REMOVE the
variable from the compose file entirely** — not to set it to `false`.

This matters because GitHub hard-deprecates old runner versions. With
`--disableupdate` the runner cannot climb past the minimum version on its own,
so the fix is a digest bump in Git — and `hosts/hestia/actions-runner/` is
**unconditionally excluded** from `deploy-hestia.yml` (chicken-and-egg: the
runner cannot reliably recreate its own container mid-job). So the runner can
neither self-update nor self-deploy its own fix. Upgrades are manual (§6).

## 3. The 2026-08-18 incident (why this runbook exists)

A merge to `master` touching `hosts/hestia/**` produced a `deploy-hestia.yml`
run that sat **queued indefinitely**. The runner was crash-looping:

```
Current runner version: '2.334.0'
An error occurred: Runner version v2.334.0 is deprecated and cannot receive messages.
RestartCount = 11894
```

It registered successfully every time, was told its version was too old, exited,
and was restarted forever. GitHub reported the runner `status=offline`. An
earlier Renovate deploy run had been sitting queued, unnoticed, for well over a
day.

**Nothing alerted**, for four independent reasons:

1. **hestia has no per-container metrics.** Its four scrape targets are
   node-exporter (`:9100`), netscope (`:9101`), thermalscope (`:9102`),
   ipmi-exporter (`:9290`). There is no cAdvisor and no docker exporter —
   `count({__name__=~"container_.*", instance="hestia"})` returns an empty
   vector. The node-exporter runs `--collector.disable-defaults
   --collector.textfile`; it is a textfile courier, deliberately.
2. **The Kubernetes-shaped alerts create false confidence.**
   `ContainerCrashLoopBackOff` / `ContainerOOMKilled` / `ContainerHighCPU` in
   `infra/configs/alerts/prometheus-rules.yaml` read `kube_pod_container_status_*`
   and cAdvisor. hestia is not a node in this cluster. Those alert *names* read
   as coverage that does not exist.
3. **homelabscope's job model does not fit an event-driven pipeline.**
   `homelabscope_job_*` is "last success + max-age budget". `deploy-hestia.yml`
   fires on push: no cadence, no expected freshness, nothing to be late against.
4. **A queued run is not a failed run.** GitHub emails on workflow *failure*. A
   run nobody picks up never fails. Runner `status=offline` lives only in a
   Settings page that nothing polls.

## 4. Monitoring

Alerts live in
[`infra/configs/homelabscope/prometheus-rule.yaml`](../../../infra/configs/homelabscope/prometheus-rule.yaml),
group `homelabscope.containers`. They are fed by the `homelabscope-heartbeat`
container probe on hestia (docker socket, read-only), written to the
node-exporter textfile collector and scraped through the existing
`hestia-homelabscope` ScrapeConfig.

```
homelabscope_container_running{name="gha-runner"}          1 = running, 0 = not
homelabscope_container_restarts_total{name="gha-runner"}   docker RestartCount (counter)
homelabscope_container_last_exit_code{name="gha-runner"}   docker .State.ExitCode
homelabscope_container_started_seconds{name="gha-runner"}  current instance start, epoch
homelabscope_container_health{name="gha-runner"}           1 healthy / 0 unhealthy /
                                                           2 starting; absent with no
                                                           healthcheck
homelabscope_container_probe_success                       1 = docker socket reachable
```

There is **no** completed-jobs counter and there will not be one. `docker
inspect` exposes only the *last* exit, never a history, so N restarts between
two polls would all be attributed to one observed exit code — the emitter
refuses to fabricate that and publishes `last_exit_code` instead.

| Alert | Expression | For | Severity |
|---|---|---|---|
| `HomelabscopeContainerDown` (`lifecycle: ephemeral`) | `homelabscope_container_running{name="gha-runner"} == 0` | 30m | critical |
| `HomelabscopeContainerRestartLoop` (`lifecycle: ephemeral`) | `increase(homelabscope_container_restarts_total{name="gha-runner"}[1h]) > 100 or (increase(homelabscope_container_restarts_total{name="gha-runner"}[1h]) > 10 and max_over_time(homelabscope_container_last_exit_code{name="gha-runner"}[5m]) > 0)` | 10m | critical |
| `HomelabscopeContainerMetricAbsent` | `absent(homelabscope_container_running{name="gha-runner"}) or absent(...{name="homelabscope-heartbeat"})` | 1h | critical |

Long-lived hestia containers get the same pair with `lifecycle: long-lived`, a
15m `for:`, and a `> 5` restart threshold.

**Why the runner's numbers differ so much from every other container:**

- **`for: 30m` on down.** The real gap between ephemeral jobs is a few *seconds*,
  but the probe latches whatever it sampled for a full emit interval (60s for
  the container probe), so one unlucky mid-restart sample pins `running=0` for a
  minute of series. 30m needs a long unbroken run of such samples — implausible
  for a seconds-wide window — while still catching a dead runner inside the
  hour. Note this rule **cannot** see a crash loop: Docker reports
  `.State.Running = true` while a container sits in restart backoff, so a
  looping runner still reads `running=1`. The restart-loop rule covers that.
- **`increase()`, not `delta()` or `resets()`.** `increase()` applies
  counter-reset detection, so it survives `RestartCount` returning to 0 when the
  container is *recreated* (image bump, SCALE `app.update`). `delta()` has no
  reset detection and goes negative across a recreate, masking exactly the event
  you want. `resets()` counts recreates only and would have scored **zero** on
  2026-08-18, which was restart-policy churn within one container life.
- **Two arms, not one threshold.** For an ephemeral runner a restart is the
  *success* path, so rate alone cannot separate "restarting and doing work" from
  "restarting and completing nothing". The exit code can, exactly:

  | | restarts/hour | `last_exit_code` | arm |
  |---|---|---|---|
  | healthy 10-job deploy | ~9 | `0` | neither — silent |
  | Renovate merge storm | tens | `0` | neither — silent |
  | slow loop at the 60s backoff cap | ~60 | non-zero | **qualified (`> 10`)** |
  | fast loop, ~1 restart/sec | ~3600 | non-zero | **rate-only (`> 100`)** |

- **Why the exit code discriminates.** Docker zeroes `.State.ExitCode` when a
  container enters the running state and sets it to the real code while the
  container sits in restart backoff — so a non-zero sample means "in crash
  backoff right now". The rule does not depend on that reading being exact:
  even if the previous code were latched across the running window, a healthy
  `--ephemeral` runner exits **0** after every completed job (measured:
  `RestartCount` 0 → 9 across a 10-job deploy), so non-zero can only come from
  an abnormal exit. Healthy busy is `0` either way.
- **`> 10` on the qualified arm.** 10/hour is safe *only* because of the
  exit-code conjunction — a 10-job deploy is 9 restarts at exit 0 and fails both
  conjuncts. It is reached ~10 minutes into a 60s-cap loop, so with `for: 10m`
  the alert pages ~20 minutes in.
- **`> 100` on the rate-only arm, kept.** It catches a pathological loop whose
  exits are 0, and it is what the rule degrades to if `last_exit_code` ever
  stops being emitted. 100/hour is ~2× above the busiest plausible legitimate
  hour and ~36× below the observed fast loop (`RestartCount` hit 11894).
- **`max_over_time(...[5m])` with `for: 10m`, window deliberately shorter than
  the `for:`.** A single non-zero sample at time *s* makes the exit-code half
  true only for `[s, s+5m]` — five minutes, which cannot satisfy a ten-minute
  `for:`. So a one-off abnormal exit during a busy deploy hour can never page;
  firing requires abnormal exits *recurring* across >10 minutes. `max_over_time`
  rather than an instant read or `min_over_time` because at the 60s cap roughly
  1 sample in 60 lands mid-exec and reads 0, which would flap the `for:` timer.

**The backoff-cap gap is closed.** The previous bare `> 100` could not see a
slow crash loop pinned at Docker's 60s restart-backoff cap: it tops out near
60/hour and stayed under the threshold forever, leaving the case to the
off-cluster runner-canary workflow (plan §B) and its ~2h latency. The qualified
arm catches it in ~20 minutes. The canary stays — it is off-cluster and
therefore survives failures this rule cannot see — but it is no longer the only
thing standing between a slow loop and a silent deploy queue.

`homelabscope_container_health` is deliberately **not** alerted on. The encoding
is now settled (1 healthy / 0 unhealthy / 2 starting, **absent** when the
container declares no healthcheck), but no hestia container this rule file
targets declares a healthcheck yet, so there is nothing for it to watch.

Routing: `severity: critical` → `email-critical` → `gjcourt+critical@gmail.com`,
`repeat_interval` 4h. It pages.

## 5. Diagnosis

Symptoms in rough order of how you'll notice them:

| Symptom | Check |
|---|---|
| A `deploy-hestia` run is stuck **Queued** | `gh run list --workflow=deploy-hestia.yml` |
| Runner shows **Offline** | `https://github.com/gjcourt/homelab/settings/actions/runners` |
| Deploy "succeeded" but nothing changed | §7 |

```bash
# Is it running, and how many times has it restarted?
ssh truenas_admin@10.42.2.10 \
  'docker inspect ix-gha-runner-runner-1 --format "{{.State.Status}} restarts={{.RestartCount}} image={{.Config.Image}}"'

# What is it saying on each start?
ssh truenas_admin@10.42.2.10 'docker logs --tail 100 ix-gha-runner-runner-1'
```

Log signatures:

| Log line | Meaning | Fix |
|---|---|---|
| `Runner version vX is deprecated and cannot receive messages` | GitHub raised its minimum version; `--disableupdate` blocks self-upgrade | §6 — bump the digest, manual deploy |
| `curl: (22) The requested URL returned error: 403` | `ACCESS_TOKEN` lacks `Administration` (fine-grained) / `repo` (classic) | Rotate the PAT with correct scopes |
| `Listening for Jobs` then immediate exit | Normal ephemeral completion, not a fault | Nothing |
| Nothing at all / container absent | App stopped or removed in SCALE | Start it; check `HomelabscopeContainerMetricAbsent` |

## 6. Recovery — upgrading a deprecated runner

The runner cannot deploy its own fix. This is manual, by design.

1. Find the current digest for the pinned tag:
   ```bash
   docker manifest inspect myoung34/github-runner:ubuntu-noble \
     | jq -r '.manifests[] | select(.platform.architecture=="amd64" and .platform.os=="linux") | .digest'
   ```
2. Update the `image:` pin in `hosts/hestia/actions-runner/docker-compose.yml`,
   branch + PR + merge as normal. **The merge will not deploy it** — the path is
   excluded from `deploy-hestia.yml`.
3. Apply it by hand, either:
   - SCALE UI → Apps → `gha-runner` → Edit → paste the updated compose → Save; or
   - from a machine with network access to hestia:
     ```bash
     TRUENAS_API_KEY=… scripts/truenas-update-app.sh gha-runner hosts/hestia/actions-runner/docker-compose.yml
     ```
     (`--dry-run` first to see the diff without calling `app.update`.)
4. Verify the runner returns to **Idle** at
   `https://github.com/gjcourt/homelab/settings/actions/runners`, then re-run any
   queued deploy: `gh run list --workflow=deploy-hestia.yml`, `gh run rerun <id>`.

**Do not "fix" this by setting `DISABLE_AUTO_UPDATE: "false"`.** It is
presence-tested (§2.2) and will change nothing. Remove the variable if you want
auto-update back — accepting that the runner then drifts from the digest pinned
in Git, which is why it is disabled today.

## 7. Related failure: a deploy that reports success and does nothing

`app.update` can return `SUCCESS` without taking effect against a crash-looping
container — a known, documented quirk
([`docs/operations/2026-05-14-truenas-app-update-quirk.md`](../2026-05-14-truenas-app-update-quirk.md)).
Confirm what is actually running rather than trusting the workflow's green tick:

```bash
ssh truenas_admin@10.42.2.10 'docker inspect ix-<app>-<service>-1 --format "{{.Config.Image}}"'
```

Diff against the compose file in Git. Post-apply verification inside
`scripts/truenas-update-app.sh` is plan §C1; the drift check is §C2.

## 8. Escalation / graduation

When ≥2 self-hosted-runner workflows exist, or this Custom App becomes a
maintenance burden, replace it with
[`actions-runner-controller`](https://github.com/actions/actions-runner-controller)
in `melodic-muse` — see
[the design plan's graduation path](../../plans/2026-05-02-hestia-gha-runner.md#graduation-path--option-2b-actions-runner-controller-in-talos).
That removes both surprises in §2 at once: ARC's ephemeral model is explicit, and
runner version management stops being a manual digest bump on an excluded path.
