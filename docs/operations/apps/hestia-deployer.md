# hestia-deployer — Git-pull deployer for hestia Custom Apps

> **Status: Phase 1 (shadow).** It reads, diffs and reports. It applies
> nothing. `deploy-hestia.yml` and the `gha-runner` still deploy every hestia
> app. Plan: [`docs/plans/2026-11-09-hestia-pull-deployer-plan.md`](../../plans/2026-11-09-hestia-pull-deployer-plan.md)
> (HOMELAB-111). This runbook is a stub; Phase 2 (cutover) fills in the apply,
> verify, alert and recovery sections.

## 1. Overview

| Attribute | Value |
|---|---|
| Host | hestia (`10.42.2.10`), TrueNAS SCALE Custom App `hestia-deployer` |
| Compose | [`hosts/hestia/hestia-deployer/docker-compose.yml`](../../../hosts/hestia/hestia-deployer/docker-compose.yml) |
| Source | [`images/hestia-deployer/`](../../../images/hestia-deployer/) |
| Image | `ghcr.io/gjcourt/hestia-deployer` (built by `build-hestia-deployer.yml`) |
| Data dir | `/mnt/main/apps/hestia-deployer/` (same path in the container and on the host) |
| Metrics | `/var/lib/node-exporter/textfile/homelabscope-deploy.prom` |
| Credentials | none: Git is read anonymously, TrueNAS over its local socket |
| Never manages | `hosts/hestia/actions-runner/` and itself |

Every 5 minutes (`INTERVAL_SECONDS=300`) one tick runs in two halves:

1. **fetch** (in the container): `git ls-remote` resolves `master`. If the
   SHA moved, a depth-1 fetch of that one commit lands in
   `cache.git/`. Discovery reads every `hosts/hestia/**/docker-compose*.yml` at
   that SHA and writes `desired.json`. A failed fetch keeps the previous
   `desired.json`.
2. **reconcile** (on the host, via `nsenter -t 1` and hestia's python3): for
   each app, `app.query` + `app.config`, the secret merge (plan D4), a
   semantic diff, and a running-image check per service. Writes `state.json`
   (0600) and the textfile.

### Why it cannot apply in Phase 1

- `APPLY_ENABLED = False` in `deployer.py`, and the compose sets `DRY_RUN=1`.
  Either one alone keeps it read-only; Phase 2 flips both in one PR.
- All middleware calls go through `GuardedClient`, which allows only
  `app.query` and `app.config`; any other method raises.
- `apply_app()` raises before making a call; `reconcile()` never calls it.
- Docker is reached only through `docker ps` / `docker inspect`.

## 2. `x-deploy` keys

| Key | Meaning |
|---|---|
| `name` | overrides the derived app name |
| `archived: true` | hands-off: never touched; state `archived` |
| `paused: true` | read and diffed, never applied; state `paused` |
| `preserve-env: [KEY, ...]` | a Git `""` for these keys keeps the stored value |
| `create: true` | read, but **not acted on** until Phase 5; a missing app stays `missing` |

`archived`, `paused` and `create` must be YAML bools. Any other value makes
that one app `invalid`; the others carry on. Two active files claiming one
name are both `invalid`.

**Secrets.** If Git has `KEY: ""` and the stored config has a value, the app
is `blocked` unless `KEY` is in `preserve-env`. The deployer never blanks a
stored value. Values are never logged or written anywhere; only `service.KEY`
names are.

## 3. States (`homelabscope_deploy_app_state{app,state}`)

| State | Meaning in Phase 1 |
|---|---|
| `synced` | stored config equals Git (after normalisation) and every container runs its stored image |
| `pending` | it would apply: the log line lists the changed paths and/or the drift |
| `blocked` | a stored value would be blanked (see secrets) |
| `paused` | `x-deploy.paused: true` |
| `archived` | `x-deploy.archived: true` |
| `missing` | no TrueNAS app of that name (a new app still needs an operator `app.create`) |
| `invalid` | bad `x-deploy`, unparsable compose, duplicate name, not a Custom App, or an unrecognised `app.config` shape |
| `failed` | not used until Phase 2 |

Other series: `homelabscope_deploy_head_info{commit}`,
`homelabscope_deploy_head_commit_seconds`, `homelabscope_deploy_paused{scope}`,
`homelabscope_deploy_unmanaged_apps`, `homelabscope_deploy_dry_run`, and the
heartbeat `homelabscope_job_*{job="hestia-deployer"}` (`max_age` 1800 s; the
existing generic `HomelabscopeJobStale` rule covers it). No new alert rules in
Phase 1; they land with cutover.

## 4. Reading the shadow

```bash
sudo docker logs --tail 50 hestia-deployer
```

One line per tick:

```text
tick DRY-RUN sha=1a2b3c4 {"archived": 5, "pending": 1, "synced": 9} pending=['libation'] would_apply=['libation'] deferred=[] ...
  libation [pending] (hosts/hestia/libation/docker-compose.yml) changes: services.libation.image: <old> -> <new>
```

Changed paths are printed, never values; image references are the exception
(they are public).

**Phase 1 exit criteria** (plan, "Phase 1: shadow"):

- 7 days in which every tick on an unchanged SHA reports every app `synced`.
  A `pending` app on an unchanged SHA is a normalisation false positive: fix
  `normalize_service()` in `deployer.py` with a test.
- For each hestia merge in that window, the tick's `pending` set matches the
  apps `deploy-hestia.yml` actually changed.
- The text-compare question in the plan's "Facts" section is answered.

## 5. Bootstrap (operator, once)

1. Merge the Phase 1 PR. `build-hestia-deployer.yml` publishes
   `ghcr.io/gjcourt/hestia-deployer:<date>-<sha7>`.
2. Follow-up PR: pin that tag's `@sha256` digest in the compose and set
   `archived: false`. (`deploy-hestia.yml` cannot create the app, so this
   alone deploys nothing.)
3. On hestia: `sudo install -d -m 0700 -o root -g root /mnt/main/apps/hestia-deployer`.
4. SCALE UI → Apps → Discover → Custom App → `hestia-deployer`, paste the
   compose from `master` (or `midclt call app.create` with the same). From
   then on `deploy-hestia.yml` rolls its digest bumps like any other app.
5. Check the first ticks in `docker logs hestia-deployer` and the textfile.

## 6. Break-glass

- **Stop it:** stop the `hestia-deployer` app in SCALE. In Phase 1 that only
  stops the reporting.
- **Global pause:** `sudo touch /mnt/main/apps/hestia-deployer/PAUSE`. It still
  reads and diffs and exports `homelabscope_deploy_paused{scope="global"} 1`.
  Meaningful from Phase 2.
- **Reset the cache:** `sudo rm -rf /mnt/main/apps/hestia-deployer/cache.git /mnt/main/apps/hestia-deployer/desired.json`;
  the next tick re-fetches.
