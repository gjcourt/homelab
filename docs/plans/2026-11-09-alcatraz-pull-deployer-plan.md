---
status: in-progress
last_modified: 2026-11-09
summary: "Give alcatraz the hestia pull model: the same hestia-deployer image with a docker-compose backend (one commit per tick, config-hash diff, compose up -d per project), dry-run beside hestia's, a 24h evidence-gated soak, cutover about a week after hestia's (HOMELAB-112), then delete the never-registered alcatraz runner path (HOMELAB-118)"
---

# alcatraz pull deployer: same image, compose backend (HOMELAB-118)

## Decision

The hestia plan's open question 6 asked whether alcatraz, which carries the same
`myoung34/github-runner` image, should get the same treatment. George answered
(2026-11-09):

> "Yes, follow-up plan"

On the timeline he picked "Faster, evidence-gated", then:

> "since we're polling instead of pushing - a 24 hour soak should be enough"

For alcatraz this means three things. Write this plan now. Run alcatraz's deployer in
dry-run while hestia's runs. Cut alcatraz over about one week after hestia's cutover
(HOMELAB-112), after a 24 h dry-run soak that is gated on evidence, not on the clock.
This replaces the "once hestia has run on the deployer for a month" wording in the hestia
plan's answers table (`docs/plans/2026-11-09-hestia-pull-deployer-plan.md:506`).

## What exists today (verified 2026-11-09, `origin/master` at `a91becd2`)

### The headline: the alcatraz runner was never bootstrapped, and has deployed nothing

- **GitHub lists one self-hosted runner, `hestia`.** On 2026-11-09,
  `gh api repos/gjcourt/homelab/actions/runners` returned `total_count: 1`, a runner
  named `hestia`. No `alcatraz` runner is registered.
- **`alcatraz-deploy.yaml` has run 122 times since 2026-07-07, and every `apply` job was
  `skipped`.** All 122 runs concluded `success` because `discover` emitted an empty
  matrix (`.github/workflows/alcatraz-deploy.yaml:215`,
  `if: needs.discover.outputs.count != '0'`).
- **Nearly every recent run was a Renovate digest bump of the runner's own compose.** The
  last 20 commits under `hosts/alcatraz/` are daily
  `chore(deps): update myoung34/github-runner` bumps. They match the workflow's path
  filter (`:32-35`), and the runner directory is then excluded from the matrix (`:143`).
  `renovate.json:52` records that these bumps, together with `renovate/renovate`, used to
  consume Renovate's whole hourly PR budget.
- **The only managed compose is archived.**
  `hosts/alcatraz/immich-photos-pull/docker-compose.yml:28-32` has
  `archived: true` ("image not yet built"), with an un-digested tag (`:47`). The photo pull
  that actually runs is the DSM Task Scheduler job: `docs/STATUS.md:39`, and the
  heartbeat reads its log at `images/homelabscope-heartbeat/heartbeat.sh:7-9`.
  `music-pull` is also a DSM Task Scheduler script with no compose
  (`hosts/alcatraz/music-pull/README.md:44-54`).
- **The docs agree.** The alcatraz GitOps plan is still `blocked_on` the operator
  bootstrap (`docs/plans/2026-06-26-alcatraz-gitops-docker.md:2-5`), and
  `docs/STATUS.md:30` and `:38` say "operator bootstrap pending".
- **No repo secret is alcatraz's.** `gh secret list` shows only
  `HEALTHCHECKS_RUNNER_CANARY_URL`, which belongs to hestia's runner canary. The runner
  reads its PAT from a git-ignored `.env` on alcatraz
  (`hosts/alcatraz/actions-runner/docker-compose.yml:94`, `.env.example:15`). Whether
  George ever minted that PAT, or created the `.env`, cannot be seen from the repo.

So "retire the alcatraz runner" retires repo files, a workflow that does nothing, and
possibly an unused PAT. It does **not** retire a running deploy path. On alcatraz the
deployer is not a replacement. It is the first thing that will ever deploy there.
Open question 1 checks that George still wants it, given this.

### Push path as designed (never exercised)

- **Discovery** (`alcatraz-deploy.yaml:139-187`). It globs
  `hosts/alcatraz/**/docker-compose*.yml` (`:141`) and skips `actions-runner/`
  unconditionally (`:143`). `x-deploy.archived` must be a YAML bool, and any other value
  fails the whole run (`:155-169`). The name is `docker-compose-<name>.yml` → `<name>`,
  else the parent directory, with `x-deploy.name` overriding both (`:171-176`). This is the
  same rule as hestia's.
- **Protected-path guard** (`:84-137`, enforced at `:178-196`):
  - Bare roots are hard-denied: `/`, `/volume1`, `/volume2`, `/volumeUSB1`.
  - Some prefixes are hard-denied outright: `/etc`, `/proc`, `/sys`, `/boot`, `/dev`,
    `/var/run`, `/run`, `/volume1/@iSCSI`.
  - Protected prefixes such as `/volume1/homes` and `/volume1/family` are allowed only
    through `x-deploy.allow-host-paths`.
  - `/volume1/docker/<name>` is always allowed.
  - Any violation fails the whole run.
- **Apply** runs on `[self-hosted, alcatraz]` (`:216`) with `fail-fast: false` (`:218`). It
  calls `scripts/alcatraz-deploy-compose.sh <name> <file> [--dry-run]` (`:234-241`).
- **`scripts/alcatraz-deploy-compose.sh`:**
  - preflights `docker compose version` (`:40-46`)
  - lowercases the name and fails if it is not a valid project name (`:52-57`)
  - **prints `docker compose config` to the job log** (`:63-64`)
  - stops there on `--dry-run` (`:66-69`)
  - otherwise runs `pull` (`:74`), then `up -d --remove-orphans` (`:80`), then `ps` (`:83`)

  It does nothing else: no build, no migrations, no hooks.
- **The workflow does nothing beyond that.** There is no image build, no migration, no
  notification and no status. `workflow_dispatch` adds an `app` filter and the
  `dry_run` flag (`:36-47`, `:224-241`).

**Latent secret leak in the push path.** `config` interpolates `${VAR}` from the
environment and from `.env`. The repo is public (the deployers read it anonymously), so a
compose that interpolated a secret would print it to a public Actions log at `:64`. This
has never happened, because nothing has ever applied. The deployer drops that step (D4).

### Platform facts

- **OS.** Synology DSM with Container Manager. Docker is confirmed to have run there, but
  the DSM version is unconfirmed (`docs/plans/2026-06-26-alcatraz-gitops-docker.md:141`,
  `:145`).
- **CPU arch.** Unconfirmed: nobody has run `uname -m` (`:144`, prerequisite P3 at
  `:255-257`). Both the runner pin and `build-immich-photos-pull.yml:42-49` assume amd64.
  `build-hestia-deployer.yml:46` builds `linux/amd64` only.
- **Secrets and env reach containers through bind-mounted key files, not env vars.**
  `immich-photos-pull` mounts its SSH key read-only
  (`hosts/alcatraz/immich-photos-pull/docker-compose.yml:64`). `music-pull` uses a key
  file under `/volume1/homes/truenas-backup/.ssh/`
  (`hosts/alcatraz/music-pull/README.md:34`). The one `${VAR}` in `hosts/alcatraz/` is the
  runner's `ACCESS_TOKEN` (`actions-runner/docker-compose.yml:94`), and that file goes
  away.
- **Project names.** The deploy script uses `-p <derived name>` (`:80`). The runner itself
  was meant to be brought up as `-p gha-runner` (`actions-runner/README.md:65`). A project
  made in the Container Manager UI takes its UI name.
- **There is no metrics path on alcatraz.** Nothing in `infra/` scrapes alcatraz
  (`docs/plans/2026-08-09-local-deadman-mesh.md:189-190`). The GitOps plan deferred
  Prometheus wiring (`2026-06-26-alcatraz-gitops-docker.md:465-468`).
- **The only alcatraz signal is relayed by hestia.** `homelabscope-heartbeat` SSHes to
  `truenas-backup@10.42.2.11` with a key mounted from hestia
  (`hosts/hestia/monitoring/docker-compose-homelabscope-heartbeat.yml:48-51`). It greps the
  pull log and writes a `homelabscope_job_*` series into hestia's textfile directory
  (`images/homelabscope-heartbeat/heartbeat.sh:49-52`, `:104-130`), every 600 s (compose
  `:40`). The cluster scrapes that directory at `10.42.2.10:9100`
  (`infra/configs/homelabscope/scrapeconfig.yaml:31-34`).
- **No alert references the alcatraz runner.** Every `gha-runner` rule in
  `infra/configs/homelabscope/prometheus-rule.yaml` (`:249`, `:272-305`, `:442`,
  `:557-562`, `:605`) is about hestia's runner, as seen through hestia's docker socket.
  This was by design: "do not wire an alert on alcatraz-runner liveness until D4 is
  actually executed" (`2026-06-26-alcatraz-gitops-docker.md:469-474`). The only alcatraz
  alert is `absent(...{job="alcatraz-photos-pull"})` (`:242`). It is about the DSM job and
  is unaffected.

### What hestia's deployer gives us to reuse

`images/hestia-deployer/` (HOMELAB-111, shadow, live on hestia since #1880):

- **Fetch half** (`deployer.py:352-387`). It runs `git ls-remote`, then a depth-1 fetch of
  exactly one commit into a bare cache (`:317-340`), then reads every compose file under
  `HOSTS_PREFIX` **at that SHA** (`:343-345`) and writes `desired.json`. It uses no
  credential and no REST API. Only the `HOSTS_PREFIX = "hosts/hestia/"` constant (`:75`) is
  host-specific.
- **Discovery** (`:188-302`). This is the same name, archived and strict-bool rule as
  alcatraz's workflow, plus several additions:
  - per-app `invalid` (instead of failing the whole run)
  - duplicate-name detection
  - `paused`, `preserve-env` and `create` flags
  - nested `x-*` rejection
- **Pure compare helpers**: `normalize` (`:416-440`), `diff_paths` / `describe_paths`
  (`:476-510`, which log paths only, plus public images), `plan_applies` (`:591-605`: cap 3,
  eyes apart, self last).
- **Metrics** (`:620-677`). Families `homelabscope_job_*{job=...}` and
  `homelabscope_deploy_*`. Their HELP strings are pinned (`:113-129`), because
  node-exporter drops a family whose HELP differs between files (`:109-112`, PR #1864).
- **Dry-run guards** (`:7-19`):
  - `APPLY_ENABLED = False` (`:71`)
  - `DRY_RUN` defaults on (`:136-139`)
  - `GuardedClient` default-denies anything outside `READ_METHODS` (`:104-105`, `:687-699`)
  - `apply_app` refuses before any call (`:702-709`)
  - docker is limited to `ps` / `inspect` (`:107`, `:755-758`)
  - tests assert all of this (`tests/test_deployer.py:43-107`)
- **Secret handling**:
  - Exception text is logged by type only (`:722-725`, `:864-868`).
  - The would-be payload is never persisted (`:900`).
  - `state.json` is 0600 (`:901`).
- **The TrueNAS-only part** is the reconcile half: `app.query` / `app.config` and the
  `ix-<name>` project label (`:772`). It runs on the **host** through
  `nsenter -t 1 ... /usr/bin/python3 - reconcile < deployer.py` (`deployer.sh:29`). That
  is why `deployer.py` must stay one stdlib-only file that can be piped on stdin. Its
  compose is privileged, with `pid: host` (`hosts/hestia/hestia-deployer/docker-compose.yml:38-40`).

## Design

### A1. Same image, a second backend; not a fork

**Decision: one source tree and one image (`images/hestia-deployer/`), with a new
`compose` backend.** alcatraz runs it with `DEPLOYER_BACKEND=compose DEPLOYER_HOST=alcatraz`. hestia
keeps the `truenas` default.

Why the same image:

- **The fetch half is host-neutral.** About 60% of `deployer.py` (fetch, discovery,
  normalise, diff, plan, metrics) is shared verbatim.
- **Safety stays shared.** The dry-run guards, the HELP-string discipline and the
  secret-logging rules are the safety story. A fork would let the two copies drift.
- **Metric families must match byte for byte (A6).** One renderer guarantees it.

What changes in the code:

- **The compose backend lives in a new `compose_backend.py`**, which imports
  `deployer` for the shared pure functions. `deployer.py` stays a single stdlib-only file,
  so hestia's `nsenter ... python3 - reconcile < deployer.py` path (`deployer.sh:29`) is
  untouched. The compose reconcile half runs **in the container**: no nsenter, no host
  python.
- **`deployer.sh` branches on `DEPLOYER_BACKEND`.** With `compose`, it runs
  `python3 /app/compose_backend.py reconcile` in the container.
- **Host parameters come from the environment**, with today's values as defaults:
  `DEPLOYER_HOSTS_PREFIX`, `DEPLOYER_SELF_NAME`, `DEPLOYER_JOB`, `DATA_DIR`,
  `TEXTFILE_DIR`, `DEPLOYER_EXCLUDED_PREFIXES`. The new names carry a
  `DEPLOYER_` prefix because bare `HOST` / `JOB` are set by shells and some
  images (#1889 review).
- **The Dockerfile adds `docker-cli` and `docker-cli-compose`**, both from alpine
  (`Dockerfile:16`). The image pins the compose version, so the version that predicts a
  change is the version that applies it.
- **`build-hestia-deployer.yml:46` becomes `linux/amd64,linux/arm64`.** alcatraz's arch is
  unconfirmed. Building both arches removes the dependency, and every package is in alpine
  for both.
- **`APPLY_ENABLED` becomes per-backend:** `{"truenas": ..., "compose": False}`. hestia's
  cutover (HOMELAB-112) flips `truenas` only. A test asserts `compose` is `False` until
  alcatraz's cutover PR. One global flag would arm alcatraz the day hestia cuts over.
- **The image keeps the name `ghcr.io/gjcourt/hestia-deployer`.** A rename (for example to
  `host-deployer`) is a cosmetic follow-up after hestia's Phase 3. Doing it during
  hestia's cutover window would be churn. On alcatraz, the app, container and project are
  all named `alcatraz-deployer`.

**Cost, stated honestly.** Every alcatraz-motivated change rebuilds the image hestia
runs. Renovate automerges `ghcr.io/gjcourt/*` pin bumps (`renovate.json:59`), so hestia's
deployer self-applies on alcatraz work. This is mitigated three ways:

- hestia's test suite must pass unchanged in every PR that touches shared code
- the alcatraz code lands as additions (a new file, an env-defaulted parameter), not as
  edits to hestia paths
- no shared-code PR merges inside hestia's own cutover soak

Each such bump is also one more real exercise of hestia's self-apply path.

### A2. Fetch: one commit per tick

This is the hestia fetch half unchanged, with `DEPLOYER_HOSTS_PREFIX=hosts/alcatraz/`:

- `ls-remote` every tick
- a depth-1 fetch only when the SHA moves
- every app evaluated against that one SHA
- 300 s ticks (hestia answer 4: "5 min is fine")
- no credential and no integrity check beyond TLS and branch protection (hestia
  answer 3: "No")

`desired.json` carries each app's compose **text** as well as the parsed dict. The
compose backend feeds that text to `docker compose -f -` with a fixed, empty
`--project-directory`, so no working tree is checked out.

### A3. Discovery and validation, per app

The shared `discover()` applies, plus a `compose` validator. Every failure makes that
one app `invalid`, never the whole tick:

- **Protected-path guard.** A port of `alcatraz-deploy.yaml:84-137`: the same three lists,
  the same `/volume1/docker/<name>` own subtree, and the same `x-deploy.allow-host-paths`
  escape. It moves from CI to the deployer, so it guards what is actually applied, not
  only what CI saw.
- **Relative or `~` bind sources are invalid.** The compose CLI runs inside the deployer
  container, so it would resolve them against the container's filesystem. The runner had
  the same latent bug.
- **No interpolation, no `env_file`, no `secrets: file:` / `configs: file:` in v1.**
  `docker compose config --no-interpolate` is compared with the interpolated form; any
  difference makes the app invalid.
  - The reason is that an unset `${VAR}` becomes `""` silently. That is the compose form of
    hestia's "never blank a stored secret" (hestia plan D4), and it fails closed.
  - `env_file` and `file:` are refused because the CLI would read those files inside the
    container, and their values would land in `docker inspect`.
  - No current app needs any of these. Credentials go in host key files mounted read-only,
    as `immich-photos-pull` already does. Hestia plan D4 recommends the same convention.
- **No `privileged`, `network_mode: host`, `pid: host` or `cap_add`.**
  `hosts/alcatraz/actions-runner/README.md:124-126` states this rule, but nothing enforces
  it today; the deployer enforces it.
- **Self-exemption, exactly one, in code.** `hosts/alcatraz/alcatraz-deployer/` may mount
  `/var/run/docker.sock`, and nothing else on the deny lists. The exemption is tested.
- **Excluded:** `hosts/alcatraz/actions-runner/` (until A-Phase 4 deletes it). The
  deployer's own file is handled as self (A5).

### A4. Diff: compose's own hash against running containers

There is no stored config to compare against, as there is on TrueNAS. The desired
state is `docker compose config` and the actual state is the containers. Per app, the
backend computes the following:

1. **Desired hashes.** `docker compose -f - -p <name> config --hash '*'` gives one
   `service hash` line per service. This is the same hash `up` compares with each
   container's `com.docker.compose.config-hash` label to decide whether to recreate it,
   so "pending" means exactly "`up -d` would recreate". If the image is pinned by digest,
   an image bump changes the hash.
2. **Actual.** `docker ps -a --filter label=com.docker.compose.project=<name>` plus
   `docker inspect`. The deployer reads only labels, `Config.Image`, `State` and
   `RestartCount`, and never `Config.Env`, which holds values.
3. **Drift is any of the following:**
   - a service with no container
   - a hash mismatch
   - a container whose service is no longer in the compose (an orphan, which
     `--remove-orphans` removes, as `alcatraz-deploy-compose.sh:80` does today)
   - a container not running or `Restarting`, for an app that is not stopped (A5)
4. **Log-safe "what changed".** A hash says *that* something changed, not *what*. The
   backend therefore keeps a **fingerprint tree** per app in `state.json`: the normalised
   `config --format json`, with every leaf replaced by `sha256(value)[:12]` and images
   kept in clear, since they are public. `diff_paths` against the last applied tree then
   names paths, for example `services.x.environment.FOO` or
   `services.x.image: a -> b`, without ever holding a value on disk. This is the compose
   equivalent of hestia's paths-only rule (hestia plan D3).
5. **Ownership.** The backend adds the label
   `homelab.burntbytes.com/deployed-by=alcatraz-deployer` to every service through a
   generated override (`-f - -f <override>`). It is part of the hash on both the predict
   side and the apply side.
   - A project whose containers lack the label is `blocked` with the reason
     "project exists and is not deployer-owned". The deployer will not `up
     --remove-orphans` over a project someone made by hand in Container Manager.
   - To adopt such a project, add `x-deploy.adopt: true` for one deploy.
6. **Unmanaged** = compose projects (from `docker compose ls -a`) and unlabelled
   containers with no Git file. They are counted in `homelabscope_deploy_unmanaged_apps`
   and listed in the log, and **never touched**.

Everything `config` prints is held in memory only. It is never logged, never written,
and never shown in a diff. The equivalent of `alcatraz-deploy-compose.sh:63-64` is
dropped.

### A5. Apply

The flow mirrors hestia plan D5:

- **Order and cap.** One app at a time, in name order, at most `MAX_APPLIES_PER_TICK=3`
  (hestia answer 5: "Keep 3 per tick"), with self last. The "eyes" rule has no members on
  alcatraz: its monitoring runs on hestia.
- **Per app**, in this order:
  1. a bind-source preflight (below)
  2. `docker compose -p <name> pull`. A wrong-arch or missing image fails here, before
     anything is stopped, as today (`:71-74`).
  3. `up -d --remove-orphans --wait --wait-timeout 300`
  4. a 60 s restart-count settle
- **Failure.** The app becomes `failed` with a per-(app, sha) backoff of 1 h. A new SHA
  retries at once. There is no automatic rollback: rollback means reverting the PR, as on
  hestia.
- **Creating a project needs `x-deploy.create: true` (hestia answer 1: "Opt-in, later").**
  A missing app without the flag is `missing`, with a warning.
  - On alcatraz every first deploy is a create, so open question 2 asks whether the flag
    should be honoured from cutover rather than from a later phase.
  - The preflight checks every bind source with a throwaway
    `docker run --rm --mount type=bind,source=<path>,target=/p,readonly <deployer image> true`.
    `--mount` errors on a missing source rather than creating it, which `-v` would
    silently do as root.
  - A missing source makes the app `blocked: missing <path>`.
- **Stopped on purpose.** If every container of a project exists and none is running or
  restarting, the project is `stopped` and the deployer never starts it. This is parity
  with hestia (`deployer.py:520-522`, `:712-720`).
  - If a stopped project is also pending, it is applied with `up --no-start`: the update
    lands and nothing starts.
  - To hold an app stopped across deploys, stop it by hand or use `x-deploy.paused`.
- **Self-update.** `alcatraz-deployer` cannot `up -d` its own project from inside its
  own container, because compose stops the container that is running the CLI.
  - The backend therefore launches a **detached helper container** from the new image:
    `docker run -d --rm --name alcatraz-deployer-self-apply -v /var/run/docker.sock:...`.
    It runs the same pull / up / settle on the `alcatraz-deployer` project, outside the
    old container, and survives its stop.
  - The next tick, running in the new container, verifies the result.
  - This is the compose analogue of hestia's `systemd-run` (hestia plan D5).
  - A deployer that cannot start is caught by its stale heartbeat and by the
    healthchecks.io ping (A6). The recovery is George running `docker compose up -d` over
    SSH, which is the same bootstrap command as the first install.
- **Break-glass**, with the same two switches as hestia plan D8:
  - **Global:** `touch /volume1/docker/alcatraz-deployer/PAUSE` on alcatraz. The
    deployer still reads and diffs, but applies nothing.
  - **Per app:** `x-deploy.paused: true`, through a PR.

  Both export `homelabscope_deploy_paused`.

### A6. Observability

**The same metric families, with a `host` label.** Every `homelabscope_deploy_*` sample
gets `host="hestia"` or `host="alcatraz"`. The heartbeat is
`homelabscope_job_*{job="alcatraz-deployer"}`, with `max_age` 1800 s.

- **Prerequisite PR on hestia's side.** That PR has three parts:
  - it adds `host` to every `homelabscope_deploy_*` sample
  - it makes the `DEPLOY_HELP` strings host-neutral (`deployer.py:120-129`; for example
    "master commit the last deployer tick evaluated.")
  - it updates the pinned-HELP test

  It is needed because both hosts' files end up in **one** node-exporter (below). That
  node-exporter needs identical HELP text and identical label names per family. Existing
  queries without a `host` matcher keep matching.
- **Getting alcatraz's file to Prometheus: relay it through hestia's heartbeat.**
  Recommended; open question 3.
  1. alcatraz's deployer writes `homelabscope-deploy.prom` (0644) into
     `/volume1/docker/alcatraz-deployer/metrics/`. The directory needs `o+rX`, because DSM
     defaults to 0700.
  2. A new `collect_alcatraz_deploy` in `heartbeat.sh` reads it with the **existing**
     `truenas-backup` SSH credential (`heartbeat.sh:49-51`), on the existing 600 s slow
     cadence (`:362-366`).
  3. It **validates** every line, then writes the result atomically as
     `homelabscope-deploy-alcatraz.prom` in hestia's textfile directory. Only these lines
     pass:
     - `# HELP` / `# TYPE` lines
     - `homelabscope_deploy_*{host="alcatraz",...}`
     - `homelabscope_job_*{job="alcatraz-deployer"}`

     A rejected line drops the whole file and logs the line number. alcatraz must not be
     able to write hestia-labelled or arbitrary series into hestia's exporter.
  4. On SSH failure it leaves the last file in place. `last_success_seconds` then ages,
     and `HomelabscopeJobStale` fires, rather than series going absent.

  This needs no new listener on alcatraz, no DSM firewall change, no new credential and no
  new ScrapeConfig. Worst-case staleness is about 600 s (relay) plus 300 s (tick).
- **Alerts.** These are the hestia D9 set (hestia plan `:364-371`), written **once**,
  host-generic: `HomelabDeployAppFailed`, `...Blocked`, `...Lagging`, `...Missing`,
  `...Paused`, with `{{ $labels.host }}` in the summary.
  - If HOMELAB-112 has already added them as `HestiaDeploy*`, alcatraz's cutover PR
    renames them and adds `host` to their grouping, rather than copying them as
    `AlcatrazDeploy*`.
  - Add a `job="alcatraz-deployer"` arm to `HomelabscopeJobMetricAbsent`
    (`prometheus-rule.yaml:242-249`).
  - Add `HomelabDeployRelayRejected`, fed by a heartbeat counter, as a warning.
  - Severities follow George's model: critical pages, warning goes to the inbox.
- **Off-cluster ping.** alcatraz is the one independent box
  (`2026-08-09-local-deadman-mesh.md:62`), but the relay depends on hestia and on the
  cluster. The deployer therefore pings a **new** healthchecks.io check at the end of every
  evaluated tick, the way hestia's does (hestia plan `:373-381`), with Period 5m and Grace
  15m.
  - The URL is in `/volume1/docker/alcatraz-deployer/hc-url` (0600), mounted read-only and
    never in Git.
  - George creates the check.
- **Dashboard.** hestia's planned "deploys" row in `homelabscope-cm.yaml` gains a `host`
  column, not a second row.
- **Logs.** `docker logs alcatraz-deployer` shows one line per tick, with the SHA, the
  per-state counts, `would_apply`, `deferred` and `unmanaged`, plus one block per app with
  its changed paths. This is the same format as `deployer.py:875-891`.

### A7. Dry-run guards on the compose backend

These mirror `deployer.py:7-19`, and each one is tested:

- `APPLY_ENABLED["compose"] = False` in code, and `DRY_RUN` defaults on, so only
  `DRY_RUN=0` turns it off. Either one alone is enough to apply nothing.
- **`docker_read` is extended to an allowlist of exact argv prefixes:**
  - `ps`, `inspect`, `image inspect`, `version`
  - `compose ls`
  - `compose ... config` with `--hash`, `--format json` or `--no-interpolate`

  Everything else raises `DryRunViolation`, including:
  - `compose pull`, `compose up`
  - `run` (the preflight is a mutation: it creates a container)
  - `rm`, `stop`
- **`apply_compose()` raises before its first subprocess call** when mutations are off.
  `reconcile` does not call it in dry-run.
- **The guarded runner never passes `env=` with secrets, and never logs argv beyond the
  subcommand and the project name.** `subprocess` errors are logged by exception type
  only, as on hestia.
- **A test feeds a compose that has an obvious sentinel value in `environment:`**, runs a
  full dry-run tick against a fake docker, and asserts that the sentinel appears nowhere
  in stdout, `state.json`, `desired.json` or the `.prom` file.
  - It may appear in `desired.json` only because Git content is public, and this is
    asserted explicitly. Values that come **from the host** must never appear anywhere.

### A8. Every step of today's push path has a home

| Today | Where it goes |
|---|---|
| `on: push` path filter (`alcatraz-deploy.yaml:29-35`) | Replaced by `ls-remote` polling (A2). |
| `workflow_dispatch` `app` / `dry_run` inputs (`:36-47`) | `dry_run` → `DRY_RUN` / PAUSE. A one-app redeploy → George runs `docker compose -p <name> up -d` over SSH. Dropped as a GitHub button: no runner exists to press it. |
| `concurrency: deploy-alcatraz` (`:49-51`) | One process, serial ticks. |
| `permissions: contents: read` (`:56-57`) | Moot: no GitHub token at all. |
| Discovery (`:139-187`) | Shared `discover()` (A3). |
| Protected-path guard (`:84-137`, `:178-196`) | Ported into the validator (A3), per app. **Also kept in CI** as a lint step over `hosts/alcatraz/**`, so review still sees a red check. |
| Summary steps (`:203-211`, `:243-251`) | Logs and metrics (A6). |
| Script preflight `docker compose version` (`:40-46`) | The image ships compose. The deployer logs `docker version` (client and engine) at startup. |
| Name sanitise (`:52-57`) | `APP_NAME_RE` (`deployer.py:101`), which is stricter and lowercase-only. |
| `config` render, printed (`:63-64`) | Rendered in memory, **never printed** (A4). |
| `pull` before `up` (`:74`) | Kept (A5). |
| `up -d --remove-orphans` (`:80`) | Kept, plus `--wait` and the ownership label (A4, A5). |
| `ps` (`:83`) | The verify step (A5). |

### A9. Deployer's own compose (`hosts/alcatraz/alcatraz-deployer/docker-compose.yml`)

- **Image and identity.** `ghcr.io/gjcourt/hestia-deployer:<date-sha>@sha256:...`,
  `container_name: alcatraz-deployer`, `restart: unless-stopped`, and `user: "0:0"`.
  root is needed for the socket.
- **Not privileged, no `pid: host`.** This is less privilege than hestia's deployer, which
  needs both for nsenter. It is still root-equivalent through the socket, which is the
  same exposure the runner design accepted (`actions-runner/README.md:119-127`). The
  difference is that it runs this repo's code, with no GitHub credential.
- **Environment.**
  - `DEPLOYER_BACKEND=compose`, `DEPLOYER_HOST=alcatraz`,
    `DEPLOYER_HOSTS_PREFIX=hosts/alcatraz/`
  - `DEPLOYER_SELF_NAME=alcatraz-deployer`, `DEPLOYER_JOB=alcatraz-deployer`
  - `DATA_DIR=/volume1/docker/alcatraz-deployer`
  - `TEXTFILE_DIR=/volume1/docker/alcatraz-deployer/metrics`
  - `DRY_RUN=1`, `INTERVAL_SECONDS=300`
  - `TZ=America/New_York`, matching `immich-photos-pull` (`:56`)
- **Volumes.**
  - `/var/run/docker.sock`
  - `/volume1/docker/alcatraz-deployer:/volume1/docker/alcatraz-deployer`
  - the `hc-url` file, read-only
- **`mem_limit: 256m`.** The GitOps plan calls for resource ceilings on alcatraz
  (`immich-photos-pull/docker-compose.yml:57-60`).
- **Workflow and CI changes in the same PR as this file.**
  - `alcatraz-deploy.yaml` gets an unconditional skip for `hosts/alcatraz/alcatraz-deployer/`,
    next to `:143`. Without it, its guard hard-denies `/var/run` (`:95-96`) and **fails
    `discover` on every push** to `hosts/alcatraz/**`.
  - The CI lint guard gets the same one exemption as A3.

### Implementation notes (A-Phase 1 PR 2, `compose_backend.py`)

Where the code differs from the design above, and why:

- **No compose text in `desired.json` (A2).** The backend feeds the parsed dict as
  JSON (JSON is YAML) to `docker compose -f -`, so `desired.json` keeps hestia's
  format and `DESIRED_VERSION`. The project directory is a fixed, empty
  `DATA_DIR/compose-project/` (a non-empty one fails the tick: compose reads `.env`
  from it).
- **Ownership label injected in memory, not by an override file (A4.5).** Same
  effect: the label is part of what is hashed and what would be applied.
- **The interpolation check reads the uninterpolated render (A3).** Comparing
  `config --no-interpolate` with the interpolated form does not work: compose 5.6.1
  also normalises the interpolated form (defaults filled in, keys reordered), so
  they differ for every app. The rule is instead "no string compose read may still
  hold an unescaped `$`", on top of the same static check in Git. `$$` escapes are
  allowed. The fingerprint tree is taken from the interpolated, normalised form:
  what `up` would apply.
- **`docker inspect --format`** asks for labels, image, state and restart count only,
  so `Config.Env` never leaves the engine. The docker CLI gets a minimal
  environment (`PATH`, `HOME`, `DOCKER_*`, `TZ`).
- **Unmanaged** comes from one `docker ps -a --format` (project label or none), not
  `compose ls`, which is therefore not on the allowlist.
- **Also refused in v1:** `build`, `extends`, `include` and `secrets`/`configs` with an
  `environment` source, for the same reason as `env_file`.
- **Baseline for "what changed".** In shadow nothing is applied, so the fingerprint
  baseline is the last tree seen `synced`. At cutover it also becomes the tree last
  applied.
- **Apply (A5) is not in this PR.** `apply_compose()` refuses while mutations are off
  and otherwise raises `NotImplementedError`. The pull / up / settle path, the
  `--mount` preflight and the self-apply helper land before alcatraz's cutover.
- **Measured end to end** against a real compose 5.6.1 CLI and engine (podman,
  locally): a project brought up with the owner-labelled compose reads `synced`
  (the `config --hash` hash equals the container's `config-hash` label), and an
  image bump reads `pending` with `services.s.image: <old> -> <new>`.

## Migration (faster, evidence-gated)

Each phase is its own PR, and nothing is retired until the step before it has run clean.

**A-Phase 0: plan.** This doc.

**A-Phase 1: build (code, dry-run only).** This runs in parallel with hestia's
HOMELAB-112 work, and the PRs land in this order:

1. **Shared-code prerequisites**, with no behaviour change on hestia:
   - host parameters from the environment
   - per-backend `APPLY_ENABLED`
   - `host` label and neutral HELP strings (A6)
   - the multi-arch build

   hestia's tests must pass unchanged, apart from the HELP-text and `host`-label
   assertions. The PR does not merge inside hestia's 24 h cutover soak.
2. **`compose_backend.py`**, its tests (A7) and the `docker-cli-compose` packages.
3. **The relay**: `collect_alcatraz_deploy` in `heartbeat.sh`, its validation tests, and
   the `JobMetricAbsent` arm.
4. **`hosts/alcatraz/alcatraz-deployer/docker-compose.yml`** with `DRY_RUN=1`, plus the
   `alcatraz-deploy.yaml` exclusion (A9). A runbook stub goes in
   `docs/operations/apps/alcatraz-deployer.md`.
5. **Optional: the canary app** (open question 4), `hosts/alcatraz/deploy-canary/`.
   It is a `busybox` `sleep infinity` with `mem_limit: 16m`, no mounts and
   `x-deploy.create: true`.

**A-Phase 2: bootstrap and dry-run soak (24 h minimum, evidence-gated).**

George's steps on alcatraz (over SSH; the agent does not SSH):

1. **Arch check.** Run `uname -m` and record it here. The multi-arch build makes this
   informational, but the runner pin and `build-immich-photos-pull.yml` depend on it.
2. **Data directories:**
   `sudo install -d -m 0711 /volume1/docker/alcatraz-deployer && sudo install -d -m 0755 /volume1/docker/alcatraz-deployer/metrics`.
3. **Ping URL.** Create the healthchecks.io check and write its URL to
   `/volume1/docker/alcatraz-deployer/hc-url` (0600).
4. **First start.** Copy the compose from `master` and run
   `sudo docker compose -f docker-compose.yml -p alcatraz-deployer up -d`. This is the one
   hand-run compose, the same role the runner had.

The soak exits only when **all** of the following are true. The 24 h is a minimum, not
a deadline:

- **No false pendings.** Every tick on an unchanged SHA reports every app at its expected
  state: `archived` for `immich-photos-pull`, `missing` for the canary before its first
  apply, `synced` for the rest. There are zero false `pending` results, and comparator
  fixes land with tests.
- **A real merge predicted exactly.** At least one real alcatraz merge is predicted
  exactly: the `would_apply` set and the changed paths in the log match the diff of the
  PR.
  - Today's real alcatraz merges are Renovate bumps to the excluded runner directory, so
    they test only that an unrelated commit yields no pending.
  - The positive case needs a deliberate change. The canary's Renovate digest bump is
    the cheapest one, and it repeats on its own after that. Otherwise, a no-op comment
    plus an image bump to `immich-photos-pull` while it is still archived must yield
    "archived", not "pending".
- **Unmanaged count checked.** The `unmanaged` list matches what George sees in
  Container Manager. That count is the first time anyone has inventoried alcatraz's
  containers from the repo side.
- **Metrics arrive.** `homelabscope_deploy_app_state{host="alcatraz"}` is in Prometheus
  through the relay, and the healthchecks.io check is green.
- **No secret appears.** A grep of `docker logs alcatraz-deployer` and of
  `state.json` for every value in the `hc-url` file and the key files finds nothing.
  George runs it on alcatraz.

**A-Phase 3: cutover (about 1 week after hestia's HOMELAB-112 cutover).**

Gate: hestia's deployer has applied for about 7 days with no deployer-caused incident,
and alcatraz's soak exit criteria are met. The cutover is one PR:

- **Arm the backend.** Set `APPLY_ENABLED["compose"] = True`, and remove `DRY_RUN=1` from
  the alcatraz compose.
- **Turn off the old workflow.** Delete `alcatraz-deploy.yaml`'s `on: push` block, leaving
  `workflow_dispatch` only. On alcatraz there is no runner to fall back to (open
  question 5).
- **Alerts and dashboard.** Generalise or add the A6 alerts and the dashboard `host`
  column.
- **Create apps.** If open question 2 is "yes", the canary's `create: true` is honoured,
  and its first apply is the first-ever GitOps deploy on alcatraz.
- **Self-apply test.** Test A5's self-apply with a no-op image bump of the deployer.

**A-Phase 4: retire (after 7 clean days of A-Phase 3).**

Repo PR:

- **Delete the push-path files:** `hosts/alcatraz/actions-runner/`,
  `.github/workflows/alcatraz-deploy.yaml` and `scripts/alcatraz-deploy-compose.sh` (only
  the workflow calls it).
- **Keep the guard.** The CI lint guard (A8) stays, as a standalone `lint.yaml` step.
- **Close out the docs:**
  - Set `docs/plans/2026-06-26-alcatraz-gitops-docker.md` to `superseded`, with
    `superseded_by` this plan.
  - Update `docs/STATUS.md:30` and `:38`.
  - Update the `hosts/alcatraz/*/README.md` deploy references.
  - Update the "compose path" mentions in `docs/plans/2026-08-09-local-deadman-mesh.md:51`
    and `:101`.
- **Leave the `.gitignore` line.** `hosts/*/actions-runner/.env` (`.gitignore:17-19`) goes
  only when hestia's runner directory is also gone (hestia Phase 3).
- **Renovate goes quiet.** It stops bumping `myoung34/github-runner` for alcatraz. After
  hestia's Phase 3 too, the image leaves the estate.

George's operator steps. The repo shows no registration and no secret, so these are
mostly *confirm nothing exists*:

1. **Runner registration.** Settings → Actions → Runners has no `alcatraz` runner (none on
   2026-11-09). Delete it if one has appeared.
2. **PAT.** If a PAT was ever minted for the alcatraz runner (`.env.example:4-10`),
   **revoke it** on GitHub.
3. **Leftovers on alcatraz.** Delete `hosts/alcatraz/actions-runner/.env` if it exists
   there, and any `gha-runner` container or Container Manager project. Remove
   `/volume1/docker/actions-runner/` if it was ever created.
4. **Repo secrets.** None to remove: `gh secret list` shows nothing alcatraz-specific.

## Risks

- **First real deploys on alcatraz.** A bug in the deployer is a first-time bug on a box
  that holds the photo library. The mitigations:
  - the guard runs in the deployer, not only in CI
  - creation is opt-in per file
  - `--mount` preflight
  - the cap of 3 per tick
  - the ownership label, so it never `--remove-orphans` a hand-made project
- **Config-hash portability.** The hash algorithm is compose-version-specific. The
  deployer predicts and applies with its own pinned compose, so the hashes are
  self-consistent. A project last created by a *different* compose, such as DSM's or an
  operator's, may show one false `pending` the first time. That is caught by the ownership
  rule (it is `blocked`, not applied) and measured in the soak.
- **Old DSM Docker Engine against a new CLI.** The CLI negotiates the API version down,
  but a compose feature, such as `--wait`, can need a newer engine. The soak logs
  `docker version`. If the engine is too old, the fallback is `up -d` with a polling
  verify.
- **Shared image churn on hestia** (A1). Every alcatraz change rebuilds the image hestia
  self-applies. It is mitigated by additive code, unchanged hestia tests and no shared
  merges in hestia's soak.
- **The relay couples alcatraz visibility to hestia and the cluster.** The healthchecks.io
  ping is the independent path. If hestia is down, alcatraz deploys go on but nobody sees
  them until hestia returns.
- **The relay widens what hestia's node-exporter accepts.** It accepts only after
  validation, and fails closed by dropping the whole file.
- **Hand edits get reverted.** A `docker compose` or Container Manager change to a managed
  project is undone within one tick. That is the intended behaviour, and the runbook
  says: PAUSE first.
- **Arch.** If alcatraz is aarch64, every managed image needs an arm64 build, the
  `immich-photos-pull` build included. A wrong-arch image fails at `pull`, before
  anything stops.
- **DSM upgrades** can stop Container Manager
  (`2026-06-26-alcatraz-gitops-docker.md:426-429`). The deployer stops with it, and is
  caught by the stale heartbeat and the ping.
- **Root-equivalent socket.** This is unchanged from the runner design and is now owned by
  repo code. Anyone who can merge to `master` can already deploy to alcatraz by design,
  exactly as with Flux and hestia.

## Decisions on the open questions (George, 2026-11-09)

Answered one at a time; each answer is the option George picked.

| # | Question | George's answer |
|---|---|---|
| 1 | Still build the deployer for alcatraz? | "Yes, build it" |
| 2 | Honour `x-deploy.create: true` from cutover? | "From switch-over": opt-in per file, mount preflight, ownership label |
| 3 | Metrics path | "Relay via hestia": heartbeat fetch over the existing SSH key, plus a healthchecks.io ping |
| 4 | Add the `deploy-canary` app? | "Yes, add it" |
| 5 | When to delete the dead runner files | "7 days after": separate deletion PR after 7 clean days |

The original questions, with their recommendations, are kept below for the record.

## Open questions (answered above) for George

1. **The alcatraz runner was never registered and has deployed nothing (122 runs, every
   `apply` skipped). Still build the deployer?**
   - Recommendation: yes. It is the cheapest way for alcatraz to get GitOps at all, and
     most of it is shared code.
   - It unblocks moving the DSM Task Scheduler jobs (`immich-photos-pull`, `music-pull`)
     and the deadman-mesh alcatraz leg into Git.
   - The alternative is deleting the dormant runner path now and keeping DSM Task
     Scheduler as the deploy model. That is less work, but alcatraz then stays outside
     the repo.
2. **Honour `x-deploy.create: true` on alcatraz from cutover, rather than after two stable
   weeks as on hestia?**
   - Recommendation: yes, from cutover, opt-in per file, with the `--mount` preflight and
     the ownership label.
   - On alcatraz every first deploy is a create, and `up -d` creating a project is the
     model the runner design already chose (`actions-runner/README.md:41-45`). Your hestia
     answer ("Opt-in, later") was about TrueNAS `app.create`, which has no equivalent of
     these guards.
3. **Get alcatraz's metrics to Prometheus by relaying them through hestia's heartbeat, or
   by a node-exporter on alcatraz?**
   - Recommendation: the relay. It uses the existing SSH credential and needs no new
     listener on alcatraz, no DSM firewall rule and no new ScrapeConfig, and the
     healthchecks.io ping covers hestia being down.
   - A node-exporter on alcatraz (`10.42.2.11:9100`, a new ScrapeConfig) would also give
     the deadman mesh a direct alcatraz target. It could follow later, deployed by this
     deployer.
4. **Add a tiny `deploy-canary` app on alcatraz?** It would be busybox, 16 MB, with no
   mounts.
   - Recommendation: yes. Without it, the soak has no real alcatraz merge to predict, and
     after cutover nothing exercises the apply path between rare real changes.
   - Its own Renovate bumps make it a weekly end-to-end test.
5. **Retire the alcatraz runner files at cutover rather than 7 days after?**
   - Recommendation: keep your 7-day rule for the deletion PR. Moving `on: push` to
     `workflow_dispatch` at cutover (A-Phase 3) already stops the daily no-op runs.
   - There is no runner, so nothing could fall back to it anyway. If you would rather not
     carry dead files for a week, the deletion can ride in the cutover PR.
