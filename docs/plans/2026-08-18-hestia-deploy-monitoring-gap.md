---
status: planned
last_modified: 2026-08-18
summary: "hestia has no per-container metrics and no signal when the GitHub runner dies, so deploys failed silently for days; close it with an off-cluster canary, post-apply verification, and a container probe on the heartbeat that already exists"
---

# Plan: close the hestia deploy blind spot

## Context — the incident that exposed it

On 2026-08-18 a merge to `master` touching `hosts/hestia/**` produced a
`deploy-hestia.yml` run that sat **queued indefinitely**. The self-hosted runner
on hestia was crash-looping:

```
Current runner version: '2.334.0'
An error occurred: Runner version v2.334.0 is deprecated and cannot receive messages.
RestartCount = 11894
```

The runner registered with GitHub successfully every time, was told its version
was too old, exited, and was restarted by `unless-stopped`. GitHub reported it
`status=offline`. An earlier Renovate deploy run had been sitting queued,
unnoticed, since well before that.

**Nothing alerted.** Not the crash loop, not the offline runner, not the queued
run, not the fact that the deployed image had drifted several digests behind
`master`.

## The blind spot is four gaps, not one

Every layer that could have caught this was scoped to Kubernetes or to
scheduled jobs.

**1. Monitoring on hestia is per-process, not per-container.** There are exactly
four Prometheus scrape targets on hestia — node-exporter (`:9100`), netscope
(`:9101`), thermalscope (`:9102`), ipmi-exporter (`:9290`). Verified live,
`count({__name__=~"container_.*", instance="hestia"})` returns an **empty
vector**. There is no cAdvisor and no docker exporter. The node-exporter runs
`--collector.disable-defaults --collector.textfile`, so there are not even
`node_*` series for hestia — it is a textfile courier, deliberately.

**2. The Kubernetes-shaped alerts create false confidence.**
`ContainerCrashLoopBackOff`, `ContainerOOMKilled` and `ContainerHighCPU` exist
in `infra/configs/alerts/prometheus-rules.yaml` and work correctly — for pods.
Their `expr` is `kube_pod_container_status_*` / `container_cpu_usage_seconds_total`,
i.e. kube-state-metrics and cAdvisor. hestia is not a node in this cluster. The
alert *names* read as coverage the cluster does not have.

**3. homelabscope's model does not fit an event-driven pipeline.** The
`homelabscope_job_*` family is "last success plus a max-age budget", which is
exactly right for the 11 scheduled jobs it covers. `deploy-hestia.yml` fires on
push: no cadence, no expected freshness, no natural `max_age` to compare
against. The deploy path was structurally ineligible for the one general
mechanism that would otherwise have covered it.

**4. A queued run is not a failed run.** GitHub emails on workflow *failure*. A
run nobody picks up never fails and never notifies. Runner `status=offline` is
visible only in a Settings UI that nothing polls. GitHub's own tooling is
structurally silent about precisely this state.

### The failure was self-concealing

> **Correction (2026-08-19).** The variable's *value* was never the mechanism.
> The image entrypoint tests it with `[ -n "${DISABLE_AUTO_UPDATE}" ]` — a
> presence test — so every non-empty value (`"true"`, `"false"`, `"0"`) turns
> the flag ON, identically. The flag is disabled only by REMOVING the variable,
> which this repo now does. See `hosts/hestia/actions-runner/docker-compose.yml`.

Setting `DISABLE_AUTO_UPDATE` at all means the runner cannot upgrade past
GitHub's minimum version on its own. The fix is a digest bump, which Renovate
does open and merge — but `hosts/hestia/actions-runner/` is **unconditionally excluded**
from `deploy-hestia.yml`. So the repair for the broken deploy path was itself
gated behind a manual step nobody was nagged about, and the runner could not
deploy its own fix.

### Drift is already real, and already invisible

`thermalscope` carries `x-deploy.archived: true` in
`hosts/hestia/monitoring/docker-compose.yml` — archived 2026-05-16 with the note
"will crash-loop without nvidia-smi". It is **`up=1` and being scraped right
now.** Repo state and box state have already diverged. Nothing noticed. This
also means `x-deploy.archived` is not a trustworthy source for any
"should-be-running" watchlist.

## Proposal

Three mechanisms for three genuinely different detection problems.

### A. A hestia container is down or crash-looping

Extend **`homelabscope-heartbeat`**, which already runs on hestia as a
`privileged: true` bash loop writing `.prom` textfiles. Mount
`/var/run/docker.sock:ro` and add a probe emitting a new family alongside the
existing one:

```
homelabscope_container_running{name}
homelabscope_container_restarts_total{name}
homelabscope_container_health{name}
```

Then one alert pair in `infra/configs/homelabscope/prometheus-rule.yaml`,
structurally identical to the existing `Stale` / `MetricAbsent` pair:
`running == 0 for 10m`, and `increase(restarts_total[1h]) > 5`. Both
**`severity: critical`**.

Why extend rather than add an exporter: the heartbeat container is already
privileged with `/dev/zfs`, so the socket mount raises no privilege bar; it is
already scraped, already alerted on, and the Grafana dashboard is generic over
the family so new series appear with zero dashboard work. Most importantly
**the watcher is already watched** — if the heartbeat dies, the existing
`HomelabscopeJobMetricAbsent` guard on the ZFS snapshot jobs it writes will
fire. A new exporter would need its own liveness story built from scratch.

An `absent()` guard over an explicit must-exist list is required: a *removed*
container emits nothing at all, and `== 0` cannot see that. This is the same
lesson `HomelabscopeJobMetricAbsent` already encodes.

Would have caught this incident: **yes** — 11894 restarts trips the restart-rate
arm within the hour. Covers `libation` and every future hestia container for
free.

### B. The runner is offline or a run is stuck queued

A new **hourly canary workflow**: `on: schedule`, one job on
`runs-on: [self-hosted, hestia]`, whose only step is a `curl` to a second
healthchecks.io check (Period 1h, Grace 1h).

This is strictly stronger than "the container is running" — it proves GitHub can
dispatch, the runner picks up, and the runner executes: the entire path that was
broken. It is also the only proposal here that lives **off-cluster**, so it
survives the 2026-08-13 failure mode where Prometheus kept evaluating while
ingesting nothing and every in-cluster alert froze.

Would have caught this incident: **yes** — red within ~2h.

**Rejected alternative:** polling `GET /actions/runs?status=queued` and
`/actions/runners` into a textfile. It needs `actions:read` and
`administration:read`; the existing hestia PAT is fine-grained `Contents:
read-only`. That is a new high-privilege credential with a 1-year expiry tail,
bought to detect "queued while the runner is healthy" — a case that barely
exists, because a healthy runner drains the queue.

### C. A deploy reported success and did nothing

**C1 — post-apply verification in `scripts/truenas-update-app.sh`.** After
`app.update` returns `SUCCESS`, poll `docker inspect` on the target container
and assert the running image matches the compose. Exit non-zero on mismatch.
The script already runs inside the runner container, which already has
`/var/run/docker.sock` mounted — no new access needed.

This matters because the silent no-op is a *known, documented* bug
(`docs/operations/2026-05-14-truenas-app-update-quirk.md`): `app.update` returns
`SUCCESS` without taking effect against a crash-looping container. C1 converts
that into a **red workflow run, which GitHub does email about** — reusing a
notification path that already works.

Needs a bounded poll (~60–120s), not a single check, to absorb the race between
`app.update` returning and TrueNAS finishing the recreate.

**C2 — drift check, appended to the canary workflow.** Extend
`truenas-update-app.sh --dry-run` to exit non-zero on a non-empty diff, run
hourly across all apps. Two distinct drifts must both be covered:

- repo → `app.config` — catches merged-but-never-applied changes (the queued
  Renovate deploy)
- `app.config` → `docker inspect` — catches the no-op

`--dry-run` never calls `app.update`, so it is safe to run against
`hosts/hestia/actions-runner/` **even though that path is excluded from apply**
— include it, and the manual runner upgrade finally gets nagged.

Main implementation risk: benign normalisation differences between repo YAML and
what the Apps subsystem stores (key ordering, injected defaults). Without a
normalising comparison this cries wolf hourly and gets ignored, which is worse
than not having it.

## Recommended order

**Precondition: the runner must be healthy first**, or step 1's check is born
red. That is a manual redeploy at the current pinned digest — the runner cannot
deploy its own fix.

1. **Canary workflow** — `.github/workflows/runner-canary.yml`, a second
   healthchecks.io check, ping URL as a repo secret. Update
   `docs/reference/monitoring.md` §7 and `hosts/hestia/actions-runner/README.md`.
   Smallest change, biggest coverage, off-cluster.
2. **Post-apply verification** — `scripts/truenas-update-app.sh`. Cross-reference
   from the app-update-quirk runbook so it stops being manual folklore.
3. **Container probe** — heartbeat image, compose, prometheus-rule. Ships as two
   PRs per the established pattern: build the image, then pin the digest and add
   the `absent()` guards once the series are confirmed live. Guarding a
   not-yet-present series is a guaranteed false critical.
4. **Drift check** — last, highest false-positive risk, and it wants (2)'s
   comparison logic proven first.
5. `docs/STATUS.md` — Known issues entry and follow-ups.

## Explicitly not doing

- **cAdvisor on hestia.** A heavy scrape target with known cardinality problems,
  to answer up/down/restarts for ~8 containers. Prometheus here has 10d
  retention on iSCSI storage that has already gone read-only once (2026-08-13).
  A 30-line bash probe answers the question at a fraction of the series count.
- **Enabling node-exporter default collectors on hestia.** Different concern
  (host metrics), doubles the scrape, the disable is a deliberate documented
  choice, and it would not have caught this. Its own PR if wanted.
- **Shipping hestia docker logs to Loki.** It would have matched the literal
  deprecation string, but it is a new agent and pipeline on hestia, and
  string-matching alerts rot the moment upstream rewords the message. Restart
  count is a far more durable invariant than a log line.
- **A dedicated GitHub Actions Prometheus exporter.** New component, new
  credential, new upgrade surface, for a case the canary already covers.
- **Alerting on Docker `unhealthy` alone.** The runner *had* a healthcheck
  (`pgrep -f Runner.Listener`, 60s) and it bought nothing — nothing queries it.
  Worse, a crash-looping container reports `starting` or vanishes rather than
  `unhealthy`, so the one signal it emits is the wrong one.
- **Any of this at `severity: warning`.** Warnings route to `+alerts@` behind a
  skip-inbox filter this repo documents as effectively unread, on a 24h repeat.
  The deploy path being dead is critical or it is nothing. Note also the default
  route receiver is `null` — an alert with no `severity` label is silently
  discarded.

## Open questions

- ~~**`DISABLE_AUTO_UPDATE: "true"` — keep or flip?**~~ **RESOLVED 2026-08-19:
  removed.** Keeping it guaranteed a repeat at the next deprecation, which is
  not an acceptable trade for digest determinism on the one container that
  gates every deploy. Note there is no "flip" — the entrypoint presence-tests
  the variable, so `"false"` keeps the flag ON. The variable is now absent from
  both runner composes. The runner self-updating past its pinned digest is
  expected; exempt `gha-runner` from the C2 drift check rather than reading it
  as drift.
- **Should `hosts/hestia/actions-runner/` stay excluded from auto-apply?**
  Recommended: keep manual, rely on the C2 nag. The alternative is a two-phase
  detached self-deploy with a real risk of bricking the deploy path.
- **Watchlist for A.** Which containers are "must be running"? `x-deploy.archived`
  is not trustworthy — see thermalscope above. Explicit list, or reconcile the
  archived flags first?
- **healthchecks.io concentration.** The canary and the existing Watchdog check
  would share one free account. A lapsed account silently kills the deadman with
  nothing in-cluster noticing; two checks doubles that blast radius.
- **Does alcatraz's runner get the same canary now**, or wait for its operator
  bootstrap? It has the identical blind spot by construction.
