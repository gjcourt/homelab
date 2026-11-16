---
status: in-progress
last_modified: 2026-11-09
summary: "Replace the hestia self-hosted GitHub runner with a hestia-side Git-pull deployer (gha-runner-watchdog generalised to hestia-deployer); shadow, cut over, then retire the runner, its token, deploy-hestia.yml and the runner canary"
---

# hestia pull deployer: retire the self-hosted runner

## Decision

The hestia runner is the third-party image `myoung34/github-runner` (one maintainer), and
it bind-mounts `/var/run/docker.sock`, which makes it root on hestia
(`hosts/hestia/actions-runner/docker-compose.yml:3`, `:71`). Asked how to drop it, George
chose (2026-11-09):

> "Remove hestia runner"

That means extending the gha-runner-watchdog into a hestia-side Git-pull deployer for
every hestia app, the way Flux pulls for the cluster, and then retiring the self-hosted
runner, its `ACCESS_TOKEN`, `deploy-hestia.yml` and the runner canary.

Phase 1 (shadow) is implemented in `images/hestia-deployer/` (HOMELAB-111); it reads and reports
and applies nothing. Phases 2-5 are not implemented yet.

## What exists today (verified 2026-11-09, `origin/master` at `d65accd3`)

### The push path

- Two workflows use the hestia runner: `.github/workflows/deploy-hestia.yml:150`
  (`apply` job) and `.github/workflows/runner-canary.yml:69`. Both are
  `runs-on: [self-hosted, hestia]`.
- `deploy-hestia.yml` fires on pushes to `master` that touch
  `hosts/hestia/**/docker-compose*.yml`, the workflow itself, or
  `scripts/truenas-update-app.sh` (`:37-43`). Its `discover` job runs on `ubuntu-latest`.
  It globs every compose file (`:87`) and skips `hosts/hestia/actions-runner/`
  unconditionally (`:89`). It skips files where `x-deploy.archived` is the YAML bool `true`,
  and fails the run on any other non-bool value (`:105-120`). The app name is
  `docker-compose-<name>.yml` → `<name>`, else the parent directory name, and
  `x-deploy.name` overrides both (`:122-127`). The `apply` matrix is `fail-fast: false`
  (`:151-153`).
- Every push applies **every** non-archived app, not only the changed ones. The matrix is
  the whole tree, and `scripts/truenas-update-app.sh` decides per app whether anything
  changed.
- `scripts/truenas-update-app.sh` authenticates with `TRUENAS_API_KEY` over
  `wss://host.docker.internal/api/current` (`:62`, `:80`, `:144`). It refuses apps that are
  missing or are not Custom Apps (`:151-156`). It compares the stored compose **text** with
  the Git file **text** (`:160-176`), strips `x-*` keys (`:194-196`), and calls
  `app.update` with the Git compose as a dict (`:198-203`), then polls the job (`:207-210`).
  It does not stop/start the app, so the `app.update` no-op quirk
  (`docs/operations/2026-05-14-truenas-app-update-quirk.md:6-13`) still applies to it.
- **Secret preservation: the current script has none.** It sends the Git compose verbatim
  (`:188-201`). If Git holds `KEY: ""` and SCALE holds a real value, `app.update` blanks
  it. This has never bitten because the only compose with `""` secrets is the runner's own
  (`hosts/hestia/actions-runner/docker-compose.yml:63`, `:68`), and that file is excluded.
  Every other app with a credential reads it from a host file through a read-only bind
  mount. For example, `hosts/hestia/github-mirror/docker-compose.yml:35`, `:39` mounts
  `/mnt/main/family/admin/keys/github-mirror.token`. So the brief's premise ("Git holds `""`
  for secrets") holds for exactly one app, and that app is the one being deleted. The
  deployer still has to be safe here, because nothing stops the next app from using the
  `""` pattern (see D4).
- **Text compare is suspect.** `:174` compares `custom_compose_config_string` (or
  similar) to the raw Git file, comments included. If SCALE stores a re-serialised dict,
  which seems likely given `:186-188`, then every push re-`app.update`s every app. This is
  unverified, and Phase 1 measures it.

### New apps and archived apps

- A new app still needs an operator `app.create`. See `hosts/hestia/README.md:59` and the
  watchdog's first install (`hosts/hestia/gha-runner-watchdog/docker-compose.yml:22-23`).
- `x-deploy.archived: true` today means "skip". It does not stop the app. Archived:
  `llms/docker-compose-llama-cpp.yml`, `llms/docker-compose-vllm.yml`,
  `llms/docker-compose-vllm-experiment.yml`, `monitoring/docker-compose-nvtop.yml`, and
  `monitoring/docker-compose.yml` (which also claims `name: thermalscope`,
  `:15-19`, and collides with `thermalscope/docker-compose.yml` if ever un-archived).

### The watchdog: the deployer's seed

`images/gha-runner-watchdog/watchdog.py` was merged in #1873/#1874 and is live on hestia as
the Custom App `gha-runner-watchdog`. It already has the shape this plan needs:

- It is privileged with `pid: host` and runs as `user: "0:0"`
  (`hosts/hestia/gha-runner-watchdog/docker-compose.yml:32-34`). `watchdog.sh` nsenters
  into host init every `INTERVAL_SECONDS` and runs the script with hestia's python3
  (`watchdog.sh:19-24`). That python3 ships `truenas_api_client` and talks to the
  middleware as root over its local socket, so it needs no API key (`watchdog.py:13-19`,
  `:440-442`).
- It reads desired state from the public raw URL on `master` (`:73-76`, `:339-342`).
- It treats drift as "stored config ≠ pin OR live container ≠ pin" (`:133-139`), which
  catches the no-op quirk.
- It applies with `app.stop` → `app.update` (stored config, image field only, so the
  masked secrets survive) → `app.start` (`:383-410`). If the update fails after a stop, it
  restarts the app on its previous config (`:404-409`). It then verifies (`:413-426`).
- It writes `homelabscope_job_*{job="gha-runner-image-apply"}` and the token metrics to
  the node-exporter textfile dir (`:77-79`, `:218-248`). HELP strings must match the other
  writers byte for byte (`:97-100`, PR #1864).
- The Dockerfile is alpine plus bash, nsenter and tzdata (`images/gha-runner-watchdog/Dockerfile:8-19`).
  Tests are in `images/gha-runner-watchdog/tests/test_watchdog.py`, run by
  `.github/workflows/lint.yaml:62-80`.

### Alerts that exist only because of the runner

All are in `infra/configs/homelabscope/prometheus-rule.yaml`:

- `HomelabscopeGhaRunnerApplyFailed` (`:272`)
- `HomelabscopeGhaRunnerTokenExpiring` (`:288`)
- `HomelabscopeGhaRunnerTokenInvalid` (`:297`)
- The `gha-runner-image-apply` arm of `HomelabscopeJobMetricAbsent` (`:249`)
- The ephemeral `HomelabscopeContainerDown` (`:441-443`)
- The ephemeral `HomelabscopeContainerRestartLoop` (`:555`)
- `absent(...{name="gha-runner"})` in `HomelabscopeContainerMetricAbsent` (`:603-605`)

The off-cluster runner canary is described in `.github/workflows/runner-canary.yml:18-26`
and `docs/reference/monitoring.md:65`, `:238`.

### alcatraz (out of scope)

`.github/workflows/alcatraz-deploy.yaml:216` runs on `[self-hosted, alcatraz]`.
`hosts/alcatraz/actions-runner/docker-compose.yml:35` uses **the same image at the same
digest** (`myoung34/github-runner:ubuntu-noble@sha256:b2010...`). Retiring the hestia
runner does not remove the third-party image from the estate. See open question 6.

## Design

### D1. Name and shape

The deployer is not an agent, so it gets a plain name: `hestia-deployer`. It replaces
`gha-runner-watchdog` and keeps its proven host-side mechanics:

- privileged with `pid: host`
- nsenter into the host, using host python3 and `truenas_api_client`
- textfile metrics

The code moves:

| From | To |
|---|---|
| `images/gha-runner-watchdog/` | `images/hestia-deployer/` |
| `hosts/hestia/gha-runner-watchdog/` | `hosts/hestia/hestia-deployer/` |
| `build-gha-runner-watchdog.yml` | `build-hestia-deployer.yml` |

The runner-specific logic stays in the watchdog until cutover and is deleted with it:
GitHub runners API, idle sampling, token metrics. The deployer has **no GitHub
credential at all**.

Privilege does not change. The watchdog is already host root, so hestia's root-equivalent
surface goes from two pieces (runner and watchdog) to one, and the remaining one runs code
from this repo, not a third-party image.

Split of work inside one container:

- **Fetch, in the container.** Alpine gains `git`. A bind-mounted host dir
  `/mnt/main/apps/hestia-deployer/` holds a bare cache clone and the state file, so the
  host side sees the same files under `-m`.
- **Apply, in the host namespaces.** The host python3 runs with `truenas_api_client`, as
  today. The apply step reads the checked-out compose files from the shared dir, never
  from the network.

### D2. Reading Git: one commit per tick

1. Resolve `master` once per tick with `git ls-remote https://github.com/gjcourt/homelab
   refs/heads/master`. That is git smart-HTTP, not the REST API, so it does not use the 60
   requests/h unauthenticated REST budget. That budget is shared by every device behind
   the home NAT.
2. If the SHA is unchanged and every app was in sync last tick, do only the cheap drift
   check (D3) and stop. If it changed, `git fetch --depth 1 origin <sha>` into the cache and
   check out `hosts/hestia/` at that SHA.
3. Every app in a tick is evaluated against **that one SHA**. Today's watchdog reads
   `raw.githubusercontent.com` per file (`watchdog.py:73-76`). That URL is CDN-cached for
   minutes and is not commit-consistent across files: two files fetched seconds apart can
   come from different commits. The deployer stops using it.

Integrity:

- **Default (recommended), parity with Flux and today's push path.** Trust TLS to
  github.com plus the fact that `master` only moves through protected-branch PRs.
  `clusters/melodic-muse/flux-system/gotk-sync.yaml:13` pulls the same repo the same way
  with no signature check. Anyone who can move `master` can already deploy to hestia today
  through `deploy-hestia.yml`, so this adds no new trust.
- **Optional hardening.** Require the head commit to be GitHub-verified. Squash-merges
  made in the web UI are signed by GitHub's web-flow key, so normal merges pass. This needs
  a REST call per new SHA (rare, so well inside the anonymous budget), or a local
  `git verify-commit` against GitHub's published web-flow key. It catches a compromised
  token pushing directly, which branch protection should already block. This is open
  question 3.

Tick interval: keep 300 s. Merge-to-apply latency is then up to about 5 minutes plus
apply time. Today a push deploy starts within seconds, so this is the main UX cost. See
open question 4 for a webhook-free way to shorten it.

### D3. Desired state, diff, and drift

- **Discovery** ports `deploy-hestia.yml:86-130`: same glob, same name derivation, same
  strict-bool `archived` check (a bad value marks that app `invalid`; it no longer fails
  everything). It adds a hard error for **duplicate app names** across files (today
  `monitoring/docker-compose.yml` and `thermalscope/` would collide if both were active),
  and a validation step: each file must parse, have `services`, and contain no `x-*` keys
  outside the top level.
- **Desired** = the Git compose dict with top-level `x-*` stripped (same rule as
  `truenas-update-app.sh:194-196`), after the secret merge (D4).
- **Stored** = `app.config <name>`, the dict the watchdog already reads (`watchdog.py:443`).
- **Drift** = `desired != stored` (a semantic dict compare, not text), OR, for any
  service, the live container's image differs from the stored image. The second check is
  the quirk detector, the same rule as `on_pin` (`watchdog.py:133-139`) generalised to
  every service.
- **Diff logging** prints the **paths** that changed (for example
  `services.foo.environment.BAR`, `services.foo.image`), never values. Images are the
  exception: they are always printed, since they are public. Environment values can be
  secrets, so a plain unified diff is not allowed.
- **Normalisation risk.** `app.config` may add defaults or reshape fields such as
  environment list vs map. Phase 1 exists to find these, and the comparator normalises
  them (for example `environment: [K=V]` ↔ `{K: V}`) with unit tests per case. Until the
  shadow diff is empty for every app on an unchanged commit, nothing is applied.

### D4. Secret preservation: never blank a stored secret

Rule, applied before the diff:

- For each service and env key where Git has the empty string and stored has a non-empty
  value:
  - If the key is listed in that app's `x-deploy.preserve-env: [KEY, ...]`, carry the
    stored value into desired.
  - If it is **not** listed, the app is `blocked`. The deployer applies nothing for it,
    sets status `blocked`, and logs the key name (never the value). It never blanks.
- An env key that exists in stored but is absent from Git is a normal removal. It is
  dropped, because Git is truth. A preserved key must be present in Git as `""`.
- Values are carried in memory only. They are never logged and never written to state or
  metrics. This is the same handling the watchdog already gives `ACCESS_TOKEN`
  (`watchdog.py:17-19`).

The rule is an explicit allowlist rather than "every `""` is a secret" because the
implicit form cannot express an env var that is really meant to be empty, and it silently
keeps a value George may have meant to clear. Failing closed costs one line of compose.

Recommended convention for new apps: put credentials in host files under
`/mnt/main/family/admin/keys/`, mounted read-only, as `github-mirror` does. The Git
compose is then the whole config and `preserve-env` is not needed. After the runner is
gone, **no** current app needs `preserve-env`.

### D5. Apply ordering and mechanics

- **One app at a time**, in name order, with `hestia-deployer` itself always last.
- **At most `MAX_APPLIES_PER_TICK` = 3.** A commit that touches 12 apps rolls out over 4
  ticks, about 20 minutes. This bounds how many apps one bad tick can take down, and
  `node-exporter` and `homelabscope-heartbeat` (the deployer's own eyes) are never
  restarted in the same tick as each other. Open question 5 asks whether to drop the cap.
- **Per app:** `app.stop` → `app.update(desired)` → `app.start` (only if not `RUNNING`),
  waiting on each job. If anything fails after a stop, `app.start` on the previous config,
  as `watchdog.py:383-410` does today. Stop/start always runs: it sidesteps the quirk on
  every apply, not only for crash-looping apps. The cost is a full container stop on every
  change, which `app.update` also does for any real change.
- **Verify** within `VERIFY_TIMEOUT_S` (default 300):
  - every service's container is running the desired image
  - it is not `Restarting`
  - its healthcheck is `healthy` if it defines one
  - its restart count is unchanged over a 60 s settle window

  If verify fails, the app is `failed`. There is no automatic rollback (D6).
- **Failure backoff.** A failure on `(app, sha)` is retried at most once per hour. A new
  SHA that touches the app retries at once. This stops a broken compose from bouncing an
  app every 5 minutes.
- **Self-update** (`hestia-deployer`'s own compose). The apply is launched as a transient
  host unit, `systemd-run --unit=hestia-deployer-self-apply` running the same
  stop/update/start script. It therefore runs outside the container's cgroup and survives
  the container being stopped. The next tick, in the new container, verifies it.
  - If systemd-run proves unusable on TrueNAS 26.x, the fallback is a plain `app.update`
    with no stop. The quirk only bites a crash-looping container, and a crash-looping
    deployer runs no ticks at all.
  - A deployer that cannot start is caught by the existing `HomelabscopeContainerDown`
    allowlist (rename `gha-runner-watchdog` → `hestia-deployer` in
    `prometheus-rule.yaml:399`, `:467`). The fix is the operator runbook.

### D6. `x-deploy` semantics

| Key | Today (`deploy-hestia.yml`) | Deployer |
|---|---|---|
| `name` | overrides derived name | same |
| `archived: true` | skip; app left as-is | same: hands-off, never stopped or deleted; metric state `archived` |
| `archived: <non-bool>` | whole run fails | that app `invalid`; others proceed |
| `paused: true` (new) | — | per-app freeze: read and diffed, never applied; state `paused` |
| `preserve-env: [..]` (new) | — | D4 |
| `create: true` (new, phase 5) | — | allow `app.create` when the app is missing (D7) |

**Rollback** = revert the PR. The deployer converges to the revert like any other commit.
There is no automatic rollback. Git stays the only source of truth, as with Flux. A
revert during a `failed` backoff is a new SHA, so it applies at once.

**Unmanaged apps** are SCALE apps with no compose in Git, such as catalog apps. They are
listed in the `unmanaged` metric and **never touched**. The deployer never prunes and
never deletes.

### D7. New apps: `app.create`

Recommendation: **support it, but only per-file opt-in (`x-deploy.create: true`) and only
in phase 5**, after cutover has been stable for two weeks. Without the flag, a missing app
is state `missing` with a warning alert, which is today's operator step made visible.

Risks, honestly:

- **Prerequisites.** A new app usually needs host prerequisites first: a dataset, a key
  file, ownership. Example: `hosts/hestia/github-mirror/docker-compose.yml:12-18`. Created
  without them, it crash-loops.
  - Mitigation: a preflight that every bind-mount source path exists on the host, otherwise
    `blocked: missing <path>`.
- **Name collisions.** The name could collide with a catalog app George installs by hand.
  - Mitigation: `app.query` first; never act on a non-Custom App, as
    `truenas-update-app.sh:155-156` already refuses.
- **No new privilege surface.** `app.update` can already turn an existing app privileged,
  so `create` adds no new privilege. It adds a new *way to surprise*: a merged file starts
  a container nobody bootstrapped. The opt-in flag makes that a deliberate line in review.

### D8. Safety: honest comparison with today

A bad compose on `master` now deploys with no human click. **It does today too.** Today's
push path has no approval gate: a merge to `master` runs `deploy-hestia.yml` and applies
every app (`:37-43`, `:147-178`). The "click" is the PR merge in both designs. The real
differences:

| | Today (push) | Pull deployer |
|---|---|---|
| Trigger | push event; lost if the runner is offline (2026-08-18: queued forever) | level-triggered: converges whenever it runs, catches up after any outage |
| Drift by hand-edits in SCALE UI | never corrected (only on next push touching hestia) | reverted within one tick, so a UI hotfix gets undone (needs `paused`, see below) |
| Blast radius per commit | all apps in parallel (`fail-fast: false`) | ≤3 per tick, serial, verified |
| Quirk | not handled | stop/start every apply |
| Secrets | would blank `""` values | blocked unless allowlisted |
| Credentials | GitHub PAT (`ACCESS_TOKEN`) + `TRUENAS_API_KEY` on hestia | none |
| Third-party code as host root | `myoung34/github-runner` | none (alpine + this repo's script) |
| Visibility | GitHub Actions UI per run | metrics, alerts, logs (D9); no GitHub UI |
| Latency | seconds | ≤5 min |

The real new risk is **auto-revert of hand edits**. Break-glass is two switches:

- **Global pause file.** `touch /mnt/main/apps/hestia-deployer/PAUSE` on hestia. It needs
  no PR and works when GitHub or the repo is the problem. The deployer still reads and
  diffs, so the metrics show what it *would* do, but it applies nothing.
- **Per-app `x-deploy.paused: true`.** Goes through a PR, for planned holds.

Both export `homelabscope_deploy_paused`, and a warning fires after 24 h paused, so a
forgotten pause is not silent.

Rate limiting:

- the per-tick cap and per-(app, sha) backoff in D5
- anonymous git traffic of one `ls-remote` per 5 min, plus one shallow fetch per new commit

### D9. Observability: replacing the Actions UI

New textfile `homelabscope-deploy.prom`. HELP strings are fixed once and tested, per
`watchdog.py:97-100`.

```text
homelabscope_job_*{job="hestia-deployer"}                      tick heartbeat (reuses HomelabscopeJobStale / MetricAbsent)
homelabscope_deploy_head_info{commit="<sha7>"} 1               master SHA the last tick evaluated
homelabscope_deploy_head_commit_seconds                        commit time of that SHA
homelabscope_deploy_app_state{app,state} 1                     state ∈ synced|pending|failed|blocked|paused|archived|missing|invalid
homelabscope_deploy_app_applied_info{app,commit="<sha7>"} 1    last SHA successfully applied to the app
homelabscope_deploy_app_last_apply_seconds{app}                time of last successful apply
homelabscope_deploy_paused{scope="global"|app}                 1 when paused
homelabscope_deploy_unmanaged_apps                             count of SCALE apps with no Git compose
```

Alerts go in `infra/configs/homelabscope/prometheus-rule.yaml`, using George's model:
critical pages, warning goes to the inbox.

| Alert | Expression (sketch) | For | Sev |
|---|---|---|---|
| `HestiaDeployAppFailed` | `homelabscope_deploy_app_state{state="failed"} == 1` | 15m | critical |
| `HestiaDeployAppBlocked` | `...{state=~"blocked\|invalid"} == 1` | 15m | critical |
| `HestiaDeployLagging` | an app `pending` with head commit older than 1h | 0 | warning |
| `HestiaDeployAppMissing` | `...{state="missing"} == 1` | 1h | warning |
| `HestiaDeployPaused` | `homelabscope_deploy_paused == 1` | 24h | warning |
| stale / absent | `HomelabscopeJobStale` + `HomelabscopeJobMetricAbsent` arm for `job="hestia-deployer"` (`max_age` 1800s) | existing | critical |

**Off-cluster signal.** `runner-canary.yml` exists because in-cluster alerts go mute with
Prometheus (`:18-26`). The deployer replaces it by pinging healthchecks.io at the end of
every tick that completed evaluation. Paused or blocked still counts as a ping, because
the deployer is alive; a crashed or wedged one sends none.

- The ping URL lives in a host file, `/mnt/main/family/admin/keys/hestia-deployer.hc-url`
  (mode 0600), mounted read-only. It is never put in Git.
- Re-use the existing runner-canary check with Period 5m and Grace 15m. That is a
  healthchecks.io setting George changes.

**"Did my merge deploy?"**

- **Grafana.** Add a "hestia deploys" row to the existing homelabscope dashboard
  (`infra/configs/dashboards/homelabscope-cm.yaml`): a table of app × state × applied SHA
  × last apply time, and a stat for "head SHA vs. apps on it".
- **Logs.** `docker logs hestia-deployer` has one line per tick with SHA and decision, and
  one block per apply with the changed paths and verify result.
- **GitHub (optional, open question 2).** Post a commit status `hestia-deployer` on the
  SHA, green when every app is synced. It needs a fine-grained token with
  `statuses:write`, which re-introduces a credential on hestia. Recommendation: no token
  in v1.

## Migration (no gap in deploys)

Each phase is its own PR. Nothing is retired until the step before it has run clean.

**Phase 0: plan.** This doc.

**Phase 1: shadow (dry-run).**

- Build `images/hestia-deployer/` (fetch, discovery, diff, secret merge, metrics) with
  apply disabled (`DRY_RUN=1`), as a **second** app beside the watchdog. The watchdog keeps
  the runner alive and `deploy-hestia.yml` keeps deploying.
- Operator step: `app.create hestia-deployer`, the same as the watchdog's first install.
- Exit criteria:
  - For 7 days, every tick on an unchanged SHA reports every app `synced`, with zero
    normalisation false-positives. Comparator fixes go in with tests.
  - For each real hestia merge in that window, the deployer's predicted changed-app set
    matches the apps `deploy-hestia.yml` actually changed. Compare the run log to the
    deployer log.
  - The text-compare question (Facts) is answered.

**Phase 2: cutover.**

- Flip the deployer to apply (`DRY_RUN` unset).
- Same PR: change `deploy-hestia.yml`'s `on:` to `workflow_dispatch` only. Keep the file,
  as manual break-glass, until Phase 3.
- Add the D9 alerts and the dashboard row.
- From here the deployer owns every app except `actions-runner/` and itself. Self-apply
  (D5) is tested with a no-op image bump of the deployer.

**Phase 3: retire the runner** (after 14 days of clean Phase 2).

Repo PR:

- Delete `hosts/hestia/actions-runner/`, `.github/workflows/deploy-hestia.yml`,
  `.github/workflows/runner-canary.yml`, `scripts/truenas-update-app.sh` (only the runner
  and the workflow call it), and `images/gha-runner-watchdog/` with
  `hosts/hestia/gha-runner-watchdog/` and `build-gha-runner-watchdog.yml`.
- In `.github/workflows/lint.yaml:60-80`, point the test job at `images/hestia-deployer/`.
- In `.gitignore:17-19`, keep the line while `hosts/alcatraz/actions-runner/` exists.
- Remove from `infra/configs/homelabscope/prometheus-rule.yaml`:
  - `HomelabscopeGhaRunnerApplyFailed`, `...TokenExpiring`, `...TokenInvalid`
  - the ephemeral `HomelabscopeContainerDown` / `RestartLoop` arms
  - `gha-runner` from `HomelabscopeContainerMetricAbsent`
  - the `gha-runner-image-apply` absent arm
  - `gha-runner-watchdog` from the long-lived allowlists, replaced by `hestia-deployer`
- Docs:
  - `docs/operations/apps/gha-runner.md` becomes a retired stub pointing to a new
    `docs/operations/apps/hestia-deployer.md` runbook.
  - Update `hosts/hestia/README.md` (deploy model, app table, adding-an-app flow) and
    `docs/reference/monitoring.md` (runner canary section).
  - Set `docs/plans/2026-05-02-hestia-gha-runner.md` to `superseded`, and add a note to
    `docs/plans/2026-08-18-hestia-deploy-monitoring-gap.md`.
  - Update `docs/STATUS.md:29` (hestia role).
  - Update `docs/operations/2026-05-14-truenas-app-update-quirk.md` (the deployer now
    always stop/starts).
  - Fix the passing references in the per-app READMEs listed by
    `grep -rl "deploy-hestia\|gha-runner" hosts/hestia`.

Operator steps (George, in this order):

1. Stop and delete the `gha-runner` and `gha-runner-watchdog` Custom Apps in SCALE.
2. Remove the `hestia` runner registration in repo Settings → Actions → Runners.
3. **Revoke the runner's `ACCESS_TOKEN` PAT** on GitHub.
4. Delete the SCALE API key behind `TRUENAS_API_KEY`. After step 1 nothing uses it: the
   deployer talks to the middleware over its local socket. Confirm in SCALE that no other
   key consumer exists first.
5. Delete the repo secret `HEALTHCHECKS_RUNNER_CANARY_URL`, once the deployer is pinging
   the check.
6. Optionally archive `/mnt/main/apps/actions-runner`.

**Phase 4: settle.** Two weeks of observing. Then `docs/STATUS.md` records the plan as
`complete`.

**Phase 5: `app.create` (optional, D7).** Only if George answers open question 1 with
yes.

## Risks

- **Comparator false positives** make the deployer bounce apps every tick. Mitigated by
  Phase 1's exit criterion and by the per-(app, sha) backoff, which is a backstop only.
- **Auto-revert of SCALE UI hotfixes.** A behaviour change from today. The runbook must
  say: pause first, then edit.
- **The deployer deploys itself.** A bad deployer image stops all hestia deploys.
  `HomelabscopeContainerDown`, the stale heartbeat and the healthchecks.io ping all catch
  it. Recovery is manual (SCALE UI edit or `midclt`), the same as the runner today.
- **No GitHub-side record of deploys** unless open question 2 is a yes. "Did it deploy"
  moves from the Actions tab to Grafana and logs.
- **TrueNAS middleware API drift.** `app.config` / `app.update` shapes changed across
  releases before: `truenas-update-app.sh:158-166` tries several shapes. A SCALE upgrade can
  break the deployer. The deployer must turn unknown shapes into `invalid`, never apply on
  a guess.
- **The node-exporter / heartbeat apps are deployed by the thing they observe.** The
  per-tick cap keeps them apart, and the off-cluster ping does not depend on them.
- **Latency.** Up to about 5 minutes from merge to start of apply, against seconds today.
- **Anonymous GitHub access** can be throttled during GitHub incidents. The deployer then
  holds the last SHA and keeps doing local drift checks. Staleness alerts only if it
  cannot evaluate at all.
- **Same third-party runner image on alcatraz** (`hosts/alcatraz/actions-runner/docker-compose.yml:35`).
  The single-maintainer concern remains there until open question 6 is decided.

## Decisions on the open questions (George, 2026-11-09)

Answered one at a time; each answer is the option George picked.

| # | Question | George's answer |
|---|---|---|
| 1 | Should the deployer create new apps? | "Opt-in, later": only `x-deploy.create: true`, behind the bind-mount preflight, after two stable weeks (Phase 5) |
| 2 | "Deployed" commit status in GitHub? | "No, Grafana only": homelabscope row + alerts for v1 |
| 3 | Require GitHub-verified head commits? | "No": branch protection + TLS, as Flux |
| 4 | Is ≤5 min merge-to-deploy acceptable? | "5 min is fine": 300 s ticks |
| 5 | Keep the 3-apps-per-tick cap? | "Keep 3 per tick" |
| 6 | Same treatment for alcatraz? | "Yes, follow-up plan", once hestia has run on the deployer for a month |

The original questions, with their recommendations, are kept below for the record.

## Open questions for George (answered above)

1. **Should the deployer create new apps?** Recommendation: yes, but only for compose
   files with `x-deploy.create: true`, behind a bind-mount preflight, and only after
   cutover has been stable for two weeks (Phase 5). Otherwise a new app stays a one-time
   operator `app.create`, shown as a `missing` warning.
2. **Do you want "deployed" visible in GitHub (a commit status on the merge SHA)?**
   Recommendation: no for v1. It needs a write-scoped token on hestia, which this change
   is removing. Use the Grafana row and alerts, and revisit if you miss the Actions tab
   after a month.
3. **Require the head commit to be GitHub-verified (signed) before applying?**
   Recommendation: no. Branch protection plus TLS is what Flux trusts for the cluster,
   and squash merges are web-flow signed anyway. Add it later as a one-line check if you
   want defence against a direct push with a stolen token.
4. **Is ≤5 minutes from merge to deploy acceptable?** Recommendation: yes, keep 300 s
   ticks. If not, the cheapest speed-up is a 60 s `ls-remote` poll with apply still at
   most 3 apps per tick. That is still anonymous, about 60 requests/h of git traffic, and
   needs no webhook or inbound port.
5. **Keep the 3-apps-per-tick cap?** Recommendation: yes. A commit touching many apps
   rolls out over about 20 minutes instead of all at once, and the deployer never
   restarts its own monitoring pair in one tick.
6. **alcatraz runs the same `myoung34/github-runner` image. Same treatment there?**
   Recommendation: yes, as a separate follow-up plan once hestia has been on the deployer
   for a month. alcatraz deploys with plain `docker compose`
   (`scripts/alcatraz-deploy-compose.sh`), not TrueNAS, so it needs its own small puller.
   Out of scope here.
