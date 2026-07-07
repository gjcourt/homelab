# actions-runner — self-hosted GitHub Actions runner (alcatraz)

Self-hosted GHA runner that lives on alcatraz (Synology DSM, `10.42.2.11`) and
applies `hosts/alcatraz/**` compose changes by running `docker compose up -d`
against the bind-mounted Docker socket. It mirrors the hestia runner
(`hosts/hestia/actions-runner/`) but drops the TrueNAS WebSocket API layer —
Synology has no `app.update` equivalent, so the apply mechanism is direct
Docker. Full design: [`docs/plans/2026-06-26-alcatraz-gitops-docker.md`](../../../docs/plans/2026-06-26-alcatraz-gitops-docker.md).

| Attribute | Value |
|-----------|-------|
| Image | `myoung34/github-runner` (digest-pinned) |
| Runner name | `alcatraz` |
| Labels | `alcatraz`, `synology` |
| Selector | `runs-on: [self-hosted, alcatraz]` |
| Workflow | `alcatraz-deploy.yaml` (push to `master`, paths `hosts/alcatraz/**/docker-compose*.yml`) |
| Apply | `scripts/alcatraz-deploy-compose.sh` → `docker compose -p <name> up -d --remove-orphans` |
| Persistence | `/volume1/docker/actions-runner/work` (BTRFS `/volume1`) |
| Secret | `ACCESS_TOKEN` via git-ignored `./.env` (or masked Container Manager env) |

## Architecture caveats (read before bootstrap)

The apply step runs `docker compose` **inside** this runner container, so the
runner image must ship the Docker CLI + Compose v2 plugin. Two alcatraz unknowns
this workstation could not verify (plan prerequisites P2/P3):

- **CPU arch.** `myoung34/github-runner` bundles Docker + Compose on **x86_64**
  but **omits docker-compose on arm64**. Run `uname -m` on alcatraz first:
  - `x86_64` → the digest pinned in `docker-compose.yml` is correct and Compose
    is present. (Most Synology x86 DS-class units.)
  - `aarch64` → **re-pin the arm64 digest** and, because Compose is missing,
    build a thin derived image (`FROM myoung34/github-runner` +
    `apt-get install -y docker-compose-plugin`) or the apply step fails. The
    deploy helper preflights `docker compose version` and fails loudly if it's
    absent, so a wrong arch surfaces as a clear job error, not silence.
- **DSM version.** Compose "Project" support wants Container Manager (DSM 7.2+).
  On older DSM the `docker compose` CLI still works; the runner path is the same.

## One-time bootstrap

The runner is the *only* alcatraz compose pasted/brought up by hand. From its
first successful registration onward, every other `hosts/alcatraz/**` change is
applied by the workflow it executes. (Unlike hestia's TrueNAS `app.update`,
`docker compose up -d` *creates* a brand-new app on first merge, so no manual
first paste is needed for subsequent apps.)

Satisfy plan prerequisites **P1–P7** first (SSH reachable; Container Manager
installed; arch confirmed; a dedicated automation account with Docker-socket
access; `/volume1/docker/actions-runner/work` pre-created; a registration PAT
minted; Docker-socket blast-radius acknowledged). Then:

1. **Mint a GitHub PAT** with runner-registration rights (see `.env.example`
   for the exact scopes). If `docker logs gha-runner` shows
   `curl: (22) ... 403` on first start, the token lacks `Administration`
   (or `repo` for classic) — fix the scopes.
2. **Pre-create persistence** on alcatraz over SSH:
   ```bash
   sudo mkdir -p /volume1/docker/actions-runner/work
   ```
3. **Bring up the runner** — pick ONE:
   - **SSH (recommended):**
     ```bash
     cd /volume1/docker/actions-runner        # or wherever you place the compose
     cp .env.example .env && $EDITOR .env      # paste the PAT; .env is git-ignored
     docker compose -f docker-compose.yml -p gha-runner up -d
     ```
   - **Container Manager UI:** Container → Project → Create → paste
     `docker-compose.yml`; set `ACCESS_TOKEN` as a **masked** environment
     variable (the UI overrides the `${ACCESS_TOKEN:?...}` substitution).
4. **Verify** — within ~30s the runner appears at
   `https://github.com/gjcourt/homelab/settings/actions/runners` with status
   **Idle** and labels `alcatraz`, `synology`.

> A dormant/not-yet-bootstrapped runner shows **Offline** by design — do NOT
> wire a liveness alert on it until this bootstrap is actually done (plan "ops").

## Operations

### Token rotation
Regenerate the PAT in GitHub, update `./.env` (or the masked UI value), then
`docker compose -p gha-runner up -d` (SSH) / Edit → Save (UI). The container
recreates and re-registers automatically.

### Re-register from scratch
Delete `/volume1/docker/actions-runner/work/.runner` on alcatraz and restart the
container; the runner mints a fresh registration token via `ACCESS_TOKEN`.

### Pause
`docker compose -p gha-runner stop` (or Stop in the UI). Workflows queue at
GitHub until restart. A DSM reboot mid-job fails that run; `restart:
unless-stopped` brings the runner back and the merge can be re-run via
`workflow_dispatch`.

### Drift detection
```bash
docker inspect gha-runner \
  --format '{{.Config.Image}}{{println}}{{range .Config.Env}}{{println .}}{{end}}'
```
Diff the image digest + env var **keys** (not values) against `docker-compose.yml`.

## Image upgrades
Bump the digest pin in `docker-compose.yml` and `docker compose -p gha-runner up
-d` by hand (the runner never auto-deploys itself). Find a tag's digest:
```bash
docker manifest inspect myoung34/github-runner:ubuntu-noble \
  | jq -r '.manifests[] | select(.platform.architecture=="amd64" and .platform.os=="linux") | .digest'
```
Use `arm64` in the selector if alcatraz is `aarch64`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Runner never appears | Bad `ACCESS_TOKEN` / missing `Administration` scope | `docker logs gha-runner`; regenerate PAT |
| Runner Offline after reboot | Persistence missing | Confirm `/volume1/docker/actions-runner/work` exists + writable |
| Apply job fails `docker compose: command not found` | arm64 stock image omits Compose | Build derived image with `docker-compose-plugin` (see caveats) |
| Apply job fails to pull an image | Image has no tag for alcatraz's arch | Rebuild/target the right arch; `alcatraz-deploy-compose.sh`'s `pull` step surfaces this early |

## Security

The Docker socket is root-equivalent on alcatraz, which also holds live iSCSI
LUNs (CNPG PVCs) and the photo library. Keep `RUNNER_SCOPE: repo`; only
merged-to-`master` compose runs (branch protection). `hosts/alcatraz/**` compose
must not mount protected host paths, run `privileged`, use host networking, or
add capabilities — `alcatraz-deploy.yaml` has a protected-path guard, and
per-app exceptions are declared explicitly via `x-deploy.allow-host-paths`. Full
blast-radius analysis: the plan's "security / blast-radius" section.
