# alcatraz-deployer — Git-pull deployer for alcatraz compose apps

> **Status: A-Phase 1 (shadow), not yet started on alcatraz.** It reads, diffs and
> reports, and applies nothing. Nothing else deploys to alcatraz either: the runner
> was never registered. Plan:
> [`docs/plans/2026-11-09-alcatraz-pull-deployer-plan.md`](../../plans/2026-11-09-alcatraz-pull-deployer-plan.md)
> (HOMELAB-118). This runbook is a stub. The cutover (A-Phase 3) fills in the apply,
> verify, alert and recovery sections.

## 1. Overview

| Attribute | Value |
|---|---|
| Host | alcatraz (`10.42.2.11`), Synology DSM, Container Manager |
| Compose | [`hosts/alcatraz/alcatraz-deployer/docker-compose.yml`](../../../hosts/alcatraz/alcatraz-deployer/docker-compose.yml), project `alcatraz-deployer` |
| Source | [`images/hestia-deployer/`](../../../images/hestia-deployer/) (`compose_backend.py`), `DEPLOYER_BACKEND=compose` |
| Image | `ghcr.io/gjcourt/hestia-deployer` (multi-arch, built by `build-hestia-deployer.yml`) |
| Data dir | `/volume1/docker/alcatraz-deployer/` (same path inside the container and out) |
| Metrics | `/volume1/docker/alcatraz-deployer/metrics/homelabscope-deploy.prom`, relayed by hestia's `homelabscope-heartbeat` |
| Credentials | none: Git is read anonymously. Root-equivalent through the docker socket |
| Never manages | `hosts/alcatraz/actions-runner/` and itself |

Every 5 minutes (`INTERVAL_SECONDS=300`), both halves of a tick run in the container:

1. **fetch:** `git ls-remote` resolves `master`. If the SHA moved, a depth-1 fetch of
   that one commit lands in `cache.git/`. Discovery and the first validation pass read
   every `hosts/alcatraz/**/docker-compose*.yml` at that SHA and write `desired.json`.
2. **reconcile:** it rediscovers the apps from the git cache, not from
   `desired.json`. For each app, `docker compose config` renders the compose in memory.
   The second validation pass runs on that render, and the backend compares
   `config --hash` with each container's `config-hash` label. It writes `state.json`
   (0600) and the textfile.

### Why it cannot apply in A-Phase 1

- `APPLY_ENABLED["compose"] = False` in `deployer.py`, and the compose sets
  `DRY_RUN=1`. Either one alone keeps it read-only.
- Every docker call goes through an allowlist of exact argv shapes: `ps`, `inspect`
  with a fixed `--format`, `version`, `compose version`, and `compose ... config`.
  `compose pull`, `compose up`, `run`, `rm` and `stop` raise before a process starts.

## 2. What may be deployed (the validator)

An app is `invalid`, alone, if any of these fail. The full list is in the plan's
implementation notes.

- **Mounts.** A bind source must be one of:
  - exactly the app's own `/volume1/docker/<name>`;
  - exactly an `x-deploy.allow-host-paths` entry.

  Sub-paths are refused, and so is anything under another app's writable bind. An
  app could swap a path it can write for a symlink between the check and `up`
  (#1894). An allow-list entry may not overlap a denied root: system paths,
  `/volume1/docker`, `/volume1/@*`, `/usr/syno`, `/var/packages`, any volume but
  `/volume1`, or this deployer's data dir.
- **No** `privileged`, `devices`, `cap_add`, `security_opt`, host `pid`/`ipc`/`userns`/`cgroup`,
  host or container networking, `sysctls`, `env_file`, `label_file`, `build`,
  `extends` or `include`.
- **No** `${VAR}` interpolation, and no environment key without a value.
- **Top-level volumes and networks:** local or bridge only, named only
  `<project>_<key>`.
- Credentials go in host key files mounted read-only, as `immich-photos-pull` does.

## 3. States (`homelabscope_deploy_app_state{host="alcatraz",app,state}`)

| State | Meaning in A-Phase 1 |
|---|---|
| `synced` | deployer-owned containers match Git (config hash) and are running, or stopped on purpose |
| `adoptable` | made by hand from the same file: matches Git apart from the ownership label |
| `pending` | `up -d` would recreate something. The log lists the drift and, against the baseline, the changed paths |
| `blocked` | the project exists, is not deployer-owned, and differs from Git |
| `paused` | `x-deploy.paused: true` |
| `archived` | `x-deploy.archived: true` |
| `missing` | no containers. `x-deploy.create: true` is honoured from cutover |
| `invalid` | failed the validator, bad `x-deploy`, unparsable, duplicate name, or `compose config` failed |
| `failed` | not used until cutover |

`homelabscope_deploy_unmanaged_apps{host="alcatraz"}` counts two things: compose
projects with no file in Git, and containers with no compose project. The tick log
names them.

## 4. Reading the shadow

```bash
sudo docker logs --tail 50 alcatraz-deployer
```

```text
compose backend: docker client/engine <client> <engine>, compose <version>
tick DRY-RUN sha=1a2b3c4 {"archived": 1, "missing": 1} pending=[] would_apply=[] deferred=[] global_pause=False unmanaged=[...]
  deploy-canary [missing] (hosts/alcatraz/deploy-canary/docker-compose.yml) reason: no containers for this compose project; x-deploy.create: planned for creation (applied only once the compose backend is armed at cutover)
```

Paths are printed, never values. Image references are the one exception, because
they are public.

## 5. Bootstrap (George, over SSH; the agent never does this)

1. **Arch:** `uname -m`. Record it in the plan (A-Phase 2, step 1). The image is
   multi-arch, so this is informational.
2. **Data directories:**

   ```bash
   sudo install -d -m 0711 /volume1/docker/alcatraz-deployer
   sudo install -d -m 0755 /volume1/docker/alcatraz-deployer/metrics
   ```

   `metrics/` must be readable by `truenas-backup` (the relay's SSH user). DSM
   defaults new directories to 0700, so check with
   `sudo -u truenas-backup cat /volume1/docker/alcatraz-deployer/metrics/homelabscope-deploy.prom`
   after the first tick.
3. **Ping URL (not used yet):** create the healthchecks.io check (Period 5m, Grace 15m)
   and write its URL to `/volume1/docker/alcatraz-deployer/hc-url` (mode 0600). The ping
   code lands in a later PR. Until then the file only waits.
4. **First start:** copy `hosts/alcatraz/alcatraz-deployer/docker-compose.yml` from
   `master` to exactly
   **`/volume1/docker/alcatraz-deployer/bootstrap/docker-compose.yml`**, and run
   compose from there:

   ```bash
   sudo install -d -m 0700 /volume1/docker/alcatraz-deployer/bootstrap
   # root-owned 0700: copy with sudo (here from a file you scp'd to your home dir)
   sudo install -m 0600 ~/docker-compose.yml /volume1/docker/alcatraz-deployer/bootstrap/docker-compose.yml
   # no `cd` (the directory is root-only); compose takes the absolute path
   sudo docker compose -f /volume1/docker/alcatraz-deployer/bootstrap/docker-compose.yml -p alcatraz-deployer up -d
   sudo docker logs -f alcatraz-deployer   # first tick within a minute
   ```

   - **Never `DATA_DIR/compose-project/`.** The deployer requires that directory to
     be empty, because compose reads `.env` from it. A compose file there makes
     every tick fail with "compose project directory is not empty".
   - **No `.env` in `bootstrap/`.** The file needs none.

   The same command applies a later digest bump of this file. Nothing else will,
   until self-apply.

   **Cutover needs a newer image.** The pin here (`2026-11-10-e59ae1c`) predates the
   compose apply path (#1894). It is fine for the shadow, which never applies. The
   cutover PR must pin a digest built from a commit that includes #1894.
5. **Turn the relay on:** a one-line PR sets `ALCATRAZ_DEPLOY_RELAY=1` in
   `hosts/hestia/monitoring/docker-compose-homelabscope-heartbeat.yml`. That also arms
   the gated `HomelabscopeJobMetricAbsent` arm for `job="alcatraz-deployer"`. Then
   check that `homelabscope_deploy_app_state{host="alcatraz"}` and
   `homelabscope_relay_last_status{source="alcatraz-deploy"} == 0` are in Prometheus.

## 6. Soak exit criteria (A-Phase 2: 24 h minimum, evidence-gated)

- No false `pending` on an unchanged SHA. `immich-photos-pull` is `archived`, the
  canary is `missing`, and hand-made projects are `adoptable` or `blocked`, as
  expected.
- At least one real merge is predicted exactly, for example the canary's Renovate
  digest bump.
- The `unmanaged` list matches what Container Manager shows.
- The relayed metrics arrive, and the healthchecks.io check is green once the ping
  code lands.
- No secret appears. On alcatraz, grep `docker logs alcatraz-deployer` and
  `state.json` for every value in `hc-url` and in the key files, and find nothing.

## 7. Break-glass

- **Stop it:** `sudo docker stop alcatraz-deployer`. In A-Phase 1 that only stops the
  reporting.
- **Global pause:** `sudo touch /volume1/docker/alcatraz-deployer/PAUSE`. It still
  reads and diffs, and exports `homelabscope_deploy_paused{host="alcatraz",scope="global"} 1`.
  It means something from cutover.
- **Reset the cache:**
  `sudo rm -rf /volume1/docker/alcatraz-deployer/cache.git /volume1/docker/alcatraz-deployer/desired.json`.
  The next tick re-fetches.
- **A tick fails with "compose project directory is not empty":** something wrote
  into `compose-project/`. Compose reads `.env` from there, so the deployer refuses.
  Look at what it is, then remove it.
