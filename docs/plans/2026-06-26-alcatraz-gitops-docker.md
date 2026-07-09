---
status: in-progress
last_modified: 2026-07-06
summary: "GitOps push-deploy Docker workflow on alcatraz (Synology) mirroring the hestia GHA-runner model; D1–D3 + first workload (immich-photos-pull) implemented, D4 bootstrap operator-gated"
blocked_on: "operator bootstrap (P1–P7): confirm arch/DSM, create automation account, bring up the runner compose once over SSH, then flip immich-photos-pull archived→false + pin the image digest"
---

# GitOps Docker workflow on alcatraz (Synology Container Manager)

## TL;DR — feasibility verdict

**Feasible, with caveats.** Alcatraz can host a self-hosted GitHub Actions
runner in a Container Manager container and apply `hosts/alcatraz/**` compose
changes on merge to `master`, mirroring the hestia model — *but* the apply
mechanism cannot reuse hestia's TrueNAS WebSocket API path. Synology has no
equivalent declarative "Custom App" API, so the runner drives the Docker Engine
directly (`docker compose up -d` against bind-mounted `/var/run/docker.sock`).
This is a thinner, more conventional path than hestia's, and it works only if a
handful of operator prerequisites are met first — chiefly an SSH/automation
account and confirmation that Container Manager is still installed.

The bigger question is **whether it is worth doing at all.** Alcatraz's role is
actively *narrowing* (see the [alcatraz → hestia migration](2026-05-20-alcatraz-to-hestia-migration.md)
and [photos-SOT](2026-06-01-hestia-photos-sot.md) plans): block storage and
media are moving to hestia, and the long-term direction is alcatraz as a
single-purpose phone-photo upload target + passive secondary copy. Standing up a
new deploy substrate on a host we are trying to *simplify* is in tension with
that direction. The recommendation (see [Recommendation](#recommendation)) is to
**build the capability but keep it dormant / minimal** unless and until a
concrete alcatraz-resident container workload appears that cannot live on hestia
or in the cluster.

## Implementation status (2026-07-06)

The mechanism is **built** (moving this plan from `planned` → `in-progress`).
A concrete alcatraz-resident workload appeared — the nightly
[immich-photos-pull](2026-07-04-alcatraz-photos-pull.md) backup leg, previously a
manual DSM Task Scheduler job — so D1–D3 were implemented *and* wired to a real
first workload (D5) rather than left dormant. What ships:

| Deliverable | Status | Artifact |
|---|---|---|
| D1 — runner compose + README | **done** | `hosts/alcatraz/actions-runner/{docker-compose.yml,README.md,.env.example}` |
| D2 — apply wrapper | **done** | `scripts/alcatraz-deploy-compose.sh` |
| D3 — deploy workflow + protected-path guard | **done** | `.github/workflows/alcatraz-deploy.yaml` |
| First workload image (crond container) | **done** | `images/immich-photos-pull/` + `.github/workflows/build-immich-photos-pull.yml` |
| First workload compose | **done (archived until image built)** | `hosts/alcatraz/immich-photos-pull/docker-compose.yml` |
| D4 — bootstrap the runner | **operator-gated** | P1–P7 + bring up the runner compose once over SSH |

Two things stay operator-gated and are the reason `status` is `in-progress`, not
`complete`: (1) the **runner label/arch/DSM** unknowns (P2/P3) — the runner
compose pins the **amd64** `myoung34/github-runner` digest and the build
workflow targets `linux/amd64`; if `uname -m` reports `aarch64` both must be
re-pinned/re-targeted and a Compose-plugin-bearing derived runner image built;
and (2) the **first-deploy sequencing** — the `immich-photos-pull` compose ships
`x-deploy.archived: true` (same pattern as `homelabscope-heartbeat`) so it does
not try to deploy before `build-immich-photos-pull.yml` has published the image;
the operator flips it to `false` and pins the `@sha256` digest after the first
CI publish.

### Protected-path guard

D3's `discover` job enforces the plan's "no protected-path mounts" rule in CI:
it parses each non-archived compose's bind mounts and **fails the job** if any
host path is hard-denied (`/`, `/etc`, `/dev`, `/var/run` incl. the Docker
socket, bare `/volume1`, `/volume1/@iSCSI`, …) or is a protected prefix
(`/volume1/homes`, `/volume1/family`, media shares) **not** explicitly opted
into via a per-compose `x-deploy.allow-host-paths` list. The app's own
`/volume1/docker/<name>/` subtree is always allowed. This is defence-in-depth,
not a sandbox (the socket still permits `docker run -v /:/host`), but it catches
the realistic copy-paste accident at PR time. `immich-photos-pull` legitimately
writes the per-user Photos libraries, so it declares them in
`allow-host-paths` — an explicit, reviewed exception visible in the diff.

> **Divergence from the round-2 critique.** The critique framed the guard as an
> unconditional denylist of `/volume1/homes`. But the *first* workload's entire
> purpose is to write `/volume1/homes/<user>/Photos`, so an unconditional deny
> would block it. The shipped guard therefore hard-denies only system/iSCSI/root
> paths and gates the photo/media prefixes behind an explicit per-app
> `allow-host-paths` opt-in — same protection against the accident, without
> blocking the deliberate, reviewed case.

### First managed workload: immich-photos-pull

The nightly hestia→alcatraz additive photo pull is the first app the runner
manages, replacing the manual DSM-Task-Scheduler copy of `pull-from-hestia.sh`:

- **Image** (`images/immich-photos-pull/`) — `alpine:3.23` + `rsync`,
  `openssh-client`, `bash`, `tzdata`, `coreutils`; bakes in
  `pull-from-hestia.sh` and fires it nightly at 05:00 local via busybox `crond`.
  Identical containerisation pattern to the hestia-side
  `images/immich-photos-backup/`. Published as
  `ghcr.io/gjcourt/immich-photos-pull:YYYY-MM-DD` by
  `build-immich-photos-pull.yml`.
- **Compose** (`hosts/alcatraz/immich-photos-pull/docker-compose.yml`) — runs
  the image as root (needed to chown received files to the DSM accounts),
  bind-mounts the hestia SSH key (ro) + `known_hosts`, each
  `/volume1/homes/<user>/Photos` destination, and the pull log at the host's
  `/var/log/immich-photos-pull.log` so the existing hestia-side
  `homelabscope-heartbeat` collector's SSH grep for the `END (success, Ns)`
  trailer keeps working unchanged. `mem_limit`/`cpus` per the security rules.
- **Script fix folded in.** Containerising surfaced a latent bug in the
  DSM-task version: the per-user `chown -R` walked the whole ~289k-file library
  every run (O(library size)), which could exceed the runtime cap and get the
  process killed *after* the rsync but *before* the `=== END ===` trailer
  logged — silently staling the heartbeat metric despite real work done. The
  script now chowns **only the newly-transferred files** (captured via rsync
  `--out-format='%n'`, numeric `chown` since the DSM uids aren't in the
  container's `/etc/passwd`) and an **EXIT trap** guarantees the trailer always
  logs. Verified in a container: `--out-format='%n'` lists exactly the new files
  and numeric `chown 1028:100` sets the right owner without a passwd entry.

## Background — the hestia model being mirrored

hestia's push-deploy workflow (plan:
[2026-05-02-hestia-gha-runner.md](2026-05-02-hestia-gha-runner.md), now
`complete`) works like this:

1. `hosts/hestia/<app>/docker-compose*.yml` files are the source of truth.
2. `.github/workflows/deploy-hestia.yml` fires on push to `master` with a path
   filter on `hosts/hestia/**/docker-compose*.yml`. A `discover` job enumerates
   non-archived compose files (honouring an `x-deploy.archived` extension key)
   into a matrix.
3. An `apply` job runs `runs-on: [self-hosted, hestia]` — the self-hosted runner
   that lives on hestia as a TrueNAS Custom App
   (`hosts/hestia/actions-runner/docker-compose.yml`, image
   `myoung34/github-runner`, digest-pinned).
4. The runner executes `scripts/truenas-update-app.sh`, which opens a WebSocket
   to `wss://host.docker.internal/api/current`, authenticates with a TrueNAS API
   key, and calls `app.update` with the parsed compose dict. TrueNAS's Apps
   subsystem reconciles the container.

The load-bearing TrueNAS-specific piece is step 4: hestia delegates the actual
container lifecycle to TrueNAS's declarative Apps API. **Synology has no
equivalent**, which is the crux of the architectural difference below.

## Feasibility investigation — evidence

| Question | Finding | Evidence |
|---|---|---|
| Does alcatraz run Docker / Container Manager? | **Yes — confirmed it has run Docker.** qBittorrent ran on alcatraz under Container Manager at `/volume1/docker/qbittorrent/` before its migration to hestia. Container Manager (DSM's Docker package, with compose "Project" support on DSM 7.2+) is therefore installed/installable on this exact unit. | [migration plan, qBit section](2026-05-20-alcatraz-to-hestia-migration.md): "Stop the Container Manager / Docker container … `rm -rf /volume1/docker/qbittorrent/`". |
| Is SSH reachable? | **Port 22 open and the SSH daemon answers** (offers `publickey,password`). A `truenas-backup@10.42.2.11` key-based account already exists and is used by hestia's photo-backup rsync. | TCP probe from this Mac: `port 22 … succeeded`. Backup uses `ssh -i ~/.ssh/id_ed25519_alcatraz truenas-backup@10.42.2.11` ([photos-SOT plan](2026-06-01-hestia-photos-sot.md)). |
| Can this Mac log in to drive setup? | **No — not non-interactively.** `admin`/`george`/`gjcourt` all returned `Permission denied (publickey,password)`; this workstation holds no alcatraz key. Setup is operator-driven via the DSM UI + an operator SSH session. | Direct `ssh -o BatchMode=yes` attempts, 2026-06-26. |
| CPU architecture? | **Unconfirmed by probe** (no login). Synology DS-class; Container Manager runs on x86_64 and on *some* ARM64 models. The fact that the digest-pinned `myoung34/github-runner` and `linuxserver/*` images ran/run here is the practical test — **the runner image must have a matching arch tag/digest for alcatraz's CPU.** Operator must confirm `uname -m` before pinning a digest. | Synology Container Manager package notes (x86_64 + select ARM64). Operator prerequisite P3 below. |
| DSM version? | **Unconfirmed.** Compose "Project" support (declarative `docker compose`) requires Container Manager (DSM 7.2+). On older DSM/Docker, the runner can still call the `docker compose` CLI directly. | Operator prerequisite P3. |
| Persistent storage for runner state across DSM updates? | **Available** via a bind mount under `/volume1/...` (BTRFS volume), the same volume class that held `/volume1/docker/qbittorrent/config`. DSM package *updates* preserve `/volume1`; DSM *major upgrades* can stop/disable packages and occasionally reset package state — see [Synology gotchas](#synology-specific-gotchas). | `/volume1/docker/...` paths in the migration plan. |
| Filesystem? | BTRFS on `/volume1` (not ZFS). No `app.update`-style API; no ZFS snapshots for rollback (BTRFS snapshots exist but are DSM-managed, not git-driven). | [migration plan](2026-05-20-alcatraz-to-hestia-migration.md): "BTRFS on /volume1". |

**Net:** every hard blocker is either already satisfied (Docker present, SSH up,
persistent storage available) or is a one-time operator confirmation
(arch/DSM/account). There is no fundamental technical "no." The architecture
simply has to drop the TrueNAS API layer and talk to Docker directly.

## Architecture

```
PR merged on master
        │
        ▼
GHA workflow (deploy-alcatraz.yml)
        │   path filter: hosts/alcatraz/**/docker-compose*.yml
        ▼
discover job (ubuntu-latest) → matrix of non-archived compose files
        │
        ▼
apply job  runs-on: [self-hosted, alcatraz]
        │
        ▼
runner container on alcatraz (Container Manager)
        │   bind mount: /var/run/docker.sock
        ▼
docker compose -f <file> -p <name> up -d --remove-orphans
        │
        ▼
target container(s) reconciled by the local Docker Engine
```

### Key design decisions

- **Runner placement.** A Container Manager container on alcatraz itself,
  identical pattern to hestia's runner (`myoung34/github-runner`, digest-pinned,
  `restart: unless-stopped`, healthcheck on `Runner.Listener`). Labels
  `alcatraz,synology` so `runs-on: [self-hosted, alcatraz]` selects it
  unambiguously and never collides with the `hestia` runner.
- **Apply mechanism = direct Docker, not an API.** The runner bind-mounts
  `/var/run/docker.sock` and runs `docker compose up -d` per discovered file.
  This is the standard self-hosted-runner-deploys-compose pattern. No TrueNAS
  API, no `truenas-update-app.sh` analogue beyond a thin wrapper script
  (`scripts/alcatraz-deploy-compose.sh`).
- **Source of truth.** New tree `hosts/alcatraz/<app>/docker-compose*.yml`,
  mirroring `hosts/hestia/`. Reuse the same `x-deploy.archived` /
  `x-deploy.name` extension-key convention and the same filename→app-name
  derivation, so the discover logic is copy-paste-parameterised from
  `deploy-hestia.yml` (host dir + runner label are the only diffs).
- **Workflow scope isolation.** `deploy-alcatraz.yml` path-filters on
  `hosts/alcatraz/**` only; `deploy-hestia.yml` stays on `hosts/hestia/**`.
  The two never overlap. A shared `discover` could be factored later
  (reusable workflow) but is out of scope for v1 — duplication is cheaper than
  a premature abstraction here.
- **Maintainability trade-off of building-but-deferring.** D1–D3 carry
  near-zero *runtime* risk while dormant (no runner = no apply), but the
  duplicated workflow + script will not be *exercised* until first real use, so
  they can bit-rot relative to `deploy-hestia.yml`. Mitigation: when D4/D5 are
  finally executed, treat the first deploy as a deliberate end-to-end test
  (use `workflow_dispatch` with `dry_run: true` first), and re-diff
  `deploy-alcatraz.yml` against the then-current `deploy-hestia.yml`.
- **Bootstrap exception.** The runner container itself is the *one* compose that
  the operator brings up by hand once (via Container Manager UI or `docker
  compose up -d` over SSH). From then on it deploys every other
  `hosts/alcatraz/**` change. The runner's own compose is excluded from the
  discover matrix (`hosts/alcatraz/actions-runner/`), exactly as hestia excludes
  its runner — a runner cannot reliably recreate its own container mid-job.
- **First-deploy of a brand-new app.** Unlike hestia (where TrueNAS
  `app.update` only *updates* an existing app, forcing a manual first paste),
  `docker compose up -d` *creates* the project if absent. So on alcatraz the
  runner can stand up a brand-new app on first merge with no manual paste —
  a small ergonomic win over the hestia model.
- **The runner image must ship the Compose plugin (arch caveat).** The apply
  step runs `docker compose` *inside* the runner container, so the runner image
  needs the Docker CLI + Compose v2 plugin. `myoung34/github-runner` bundles
  Docker + Compose on its **x86_64** builds but **omits docker-compose on its
  ARM builds**. If P3 reports `aarch64`, do **not** rely on the stock image's
  Compose — either build a thin derived image that installs the
  `docker-compose-plugin` for arm64, or have the wrapper script install/verify
  it at job start. This is the single biggest arch-dependent correctness risk;
  see [gotchas](#synology-specific-gotchas).
- **Project names must be Compose-safe.** `docker compose -p <name>` requires
  lowercase alphanumerics, `-`/`_`, starting with a letter or number. The
  hestia filename→name derivation already yields kebab-case names, but the
  wrapper should lowercase/sanitise defensively and fail on an invalid name
  rather than silently picking a different project.

### Why not the alternatives

| Alternative | Verdict |
|---|---|
| Reuse hestia's TrueNAS WS API path | **Impossible** — Synology has no `app.update` equivalent. |
| Pull-based deploy via DSM Task Scheduler (cron `git pull` + `docker compose up`) | Viable and lower-infra (no runner, no PAT), but **diverges from the hestia model** the task asks to mirror, has no per-deploy CI surface (logs/status live only on the NAS), and DSM Task Scheduler entries are not in git (config drift). Keep as the documented **fallback** if the runner proves a maintenance burden. |
| Watchtower-style auto-image-pull | Rejected — it watches image tags, not git; no compose-as-source-of-truth, no review gate, silent restarts. Antithetical to GitOps. |
| actions-runner-controller in the Talos cluster targeting alcatraz | Over-engineered for one host; the runner would still need `docker.sock` access on alcatraz across the network (insecure) or a remote-Docker context. The hestia plan itself defers ARC to a "graduation path." |

## Operator prerequisites (blocking — must precede any execution)

These are operator-only and cannot be done from this workstation (no alcatraz
login). Listed in dependency order.

- **P1 — Enable + harden SSH (if not already operator-reachable).** DSM →
  Control Panel → Terminal & SNMP → Enable SSH service. Confirm an operator can
  `ssh <admin>@10.42.2.11`. *(SSH **is** running — port 22 answers — but this
  workstation has no key; the operator must confirm they can log in.)*
- **P2 — Confirm Container Manager is installed and on a compose-capable
  version.** DSM → Package Center → Container Manager (install if absent).
  Note the DSM version; Project/compose support wants DSM 7.2+. If older, the
  runner falls back to the `docker compose` CLI directly (still fine).
- **P3 — Confirm CPU architecture.** `ssh <admin>@10.42.2.11 'uname -m'`.
  Record x86_64 vs aarch64 — it determines the `myoung34/github-runner` digest
  to pin and whether each target app's image has a matching arch.
- **P4 — Create a dedicated, least-privilege automation account** for the runner
  rather than reusing `truenas-backup` (whose key is rsync-scoped) or a DSM
  admin. The runner needs Docker socket access, which on Synology effectively
  means membership in the `docker` group / administrators — see
  [security](#security--blast-radius). Document which account and why.
- **P5 — Pre-create the persistence directory** on `/volume1`, e.g.
  `/volume1/docker/actions-runner/work`, owned by the automation account.
- **P6 — Mint a GitHub PAT / App token** with self-hosted-runner registration
  rights on `gjcourt/homelab` (`Administration: Read and write` +
  `Metadata: Read-only` for a fine-grained PAT, or classic `repo`). Same
  requirement and rationale as the hestia runner.
- **P7 — Decide the deploy account's Docker access model** and whether
  `/var/run/docker.sock` is exposed to the runner (it must be, for this design)
  — acknowledge the blast-radius (root-equivalent on alcatraz) explicitly.

## Step-by-step implementation

Each step is a separate PR after this plan merges, mirroring the hestia
deliverable sequencing.

### D1 — `hosts/alcatraz/actions-runner/` (runner compose + README)

- `hosts/alcatraz/actions-runner/docker-compose.yml` modelled on hestia's:
  - `image: myoung34/github-runner@sha256:<digest for alcatraz's arch>`
    (pin per P3).
  - `environment`: `REPO_URL`, `RUNNER_NAME: alcatraz`, `RUNNER_SCOPE: repo`,
    `LABELS: alcatraz,synology`, `RUNNER_WORKDIR: /tmp/runner-work`,
    `EPHEMERAL: "false"`, `DISABLE_AUTO_UPDATE: "true"`, empty `ACCESS_TOKEN: ""`
    (set in Container Manager UI as a masked value — **never commit**).
  - `volumes`: `/volume1/docker/actions-runner/work:/tmp/runner-work` and
    `/var/run/docker.sock:/var/run/docker.sock`.
  - `restart: unless-stopped`; healthcheck on `pgrep -f Runner.Listener`.
  - **No `TRUENAS_API_KEY`** (not applicable).
- `hosts/alcatraz/actions-runner/README.md`: one-time bootstrap (paste compose
  into Container Manager → Project, or `docker compose up -d` over SSH; set
  `ACCESS_TOKEN` masked), token rotation, re-register, image-upgrade, and
  troubleshooting — adapted from hestia's runner README.

### D2 — `scripts/alcatraz-deploy-compose.sh` (apply wrapper)

A small, defensive wrapper the workflow calls per matrix entry:

```bash
# alcatraz-deploy-compose.sh <app-name> <compose-file> [--dry-run]
#   command -v "docker compose"   # preflight: fail clearly if the Compose
#                                  # plugin is missing (ARM-image gotcha)
#   NAME=sanitise(<app-name>)      # lowercase; must match ^[a-z0-9][a-z0-9_-]*$
#   docker compose -f <file> -p $NAME config   # validate / render
#   [--dry-run] -> stop here, print the rendered config
#   docker compose -f <file> -p $NAME pull     # surface arch-mismatch early
#   docker compose -f <file> -p $NAME up -d --remove-orphans
#   docker compose -f <file> -p $NAME ps       # report
```

- `set -euo pipefail`; fail loudly on a missing file / bad compose / missing
  Compose plugin / invalid project name.
- The `pull` step exists to surface an arch-incompatible image as a clear
  failure *before* `up -d` (an x86-only image on an ARM unit fails to pull).
- `--dry-run` runs `config` only (parity with hestia's dry-run).
- Idempotent: `up -d` is a no-op when nothing changed.

### D3 — `.github/workflows/deploy-alcatraz.yml`

- Copy `deploy-hestia.yml`; change:
  - `paths:` → `hosts/alcatraz/**/docker-compose*.yml`,
    `.github/workflows/deploy-alcatraz.yml`,
    `scripts/alcatraz-deploy-compose.sh`.
  - `concurrency.group: deploy-alcatraz`.
  - discover glob → `hosts/alcatraz/**`; runner self-exclude →
    `hosts/alcatraz/actions-runner/`.
  - `apply.runs-on: [self-hosted, alcatraz]`.
  - apply step → `./scripts/alcatraz-deploy-compose.sh "${{ matrix.name }}"
    "${{ matrix.file }}" $DRY_RUN_FLAG`.
- Keep the `x-deploy` archived/name handling verbatim.
- **Add a guard step to the `discover` job** (runs on GitHub-hosted, before any
  apply) that rejects a compose under `hosts/alcatraz/**` if it bind-mounts a
  protected host path. Concretely, fail the job if any `volumes:` entry resolves
  to a prefix in a denylist — `/volume1/family`, `/volume1/homes`,
  `/volume1/@iSCSI`, the iSCSI LUN/target paths, `/` , `/etc`, `/volume1/docker`
  *except* the app's own `/volume1/docker/<app>/...` subtree. This is a
  defence-in-depth check, not a complete sandbox (the socket still permits
  `docker run -v /:/host`), but it catches the realistic accident — a copy-paste
  mount of the photo tree — at PR-review time.

### D4 — Bootstrap the runner (operator, one-time)

1. Satisfy P1–P7.
2. Bring up the runner compose by hand (Container Manager UI Project import, or
   `docker compose -f docker-compose.yml -p gha-runner up -d` over SSH), with
   `ACCESS_TOKEN` set as a masked/exported value.
3. Verify the runner shows **Idle** with labels `alcatraz,synology` at
   `github.com/gjcourt/homelab/settings/actions/runners`.

### D5 — First real app + docs

- Only when a concrete alcatraz-resident workload exists (see
  [Recommendation](#recommendation)). Add
  `hosts/alcatraz/<app>/docker-compose-<app>.yml`; merge; confirm the runner
  creates it.
- Add `hosts/alcatraz/README.md` (host table like
  [`hosts/hestia/README.md`](../../hosts/hestia/README.md)) and a STATUS.md line.

## Security / blast-radius

- **Docker socket = root on alcatraz.** Bind-mounting `/var/run/docker.sock`
  into the runner gives anything that can schedule a job on that runner full
  root-equivalent control of the NAS host (mount `/`, run privileged
  containers). This is the *same* trust model hestia's runner already accepts,
  but alcatraz currently holds **iSCSI LUNs, NFS exports, and the phone-photo
  upload target / secondary photo copy** — a compromise here is materially worse
  than on hestia. Mitigations: keep `RUNNER_SCOPE: repo` (no org-wide exposure);
  the repo is private and PR-gated by branch protection, so only merged-to-master
  compose runs; consider `EPHEMERAL` later; never expose the socket over TCP.
- **Do NOT jeopardise alcatraz's existing roles.** alcatraz holds **live iSCSI
  LUNs backing CNPG PVCs** and the **photo upload target / secondary copy**.
  Three concrete rules, enforced by the D3 guard step (CI) plus review:
  1. **No protected-path mounts.** Deploy compose files mount only their own
     `/volume1/docker/<app>/...` subtree; never the `/volume1/family/**`,
     `/volume1/homes/**`, or iSCSI paths. The CI denylist (D3) blocks the
     obvious accident; the socket means it is not a hard sandbox, so review
     still matters.
  2. **No `privileged: true`, no host networking, no extra capabilities** in
     `hosts/alcatraz/**` compose unless explicitly justified in the PR — these
     widen the path to the storage roles.
  3. **Resource limits.** Any alcatraz app sets `mem_limit` / cpus and writes
     its workspace under `/volume1/docker/<app>/` — not into the photo volume's
     free space. A runaway container must not be able to fill the volume that
     holds the photo library or starve iSCSI/NFS I/O. The runner's own
     workspace (`/volume1/docker/actions-runner/work`) should be size-watched.
- **Secrets.** `ACCESS_TOKEN` (and any app secrets) are set as masked values in
  Container Manager / the runner env — never committed. This repo's SOPS flow is
  for *cluster* secrets; the runner token follows the hestia precedent (masked
  UI value, not SOPS). No plaintext secrets in `hosts/alcatraz/**`.
- **Least privilege + treat the PAT as a root credential.** Dedicated
  automation account (P4), not a human admin and not the rsync `truenas-backup`
  key (separation of duties: a photo-backup key compromise should not also grant
  deploy/root). Because the runner has the Docker socket, the GitHub
  `ACCESS_TOKEN` is effectively a root-on-alcatraz credential — store it masked,
  rotate it on the same cadence as other privileged secrets, and prefer a
  fine-grained PAT scoped to `gjcourt/homelab` only.
- **Network.** The runner makes only outbound HTTPS to GitHub + local Docker
  socket calls. No inbound ports. No cross-host trust (it does not talk to
  hestia or the cluster).

## Rollback

- **Per-app.** Revert the compose change PR on `master`; the next deploy runs
  `up -d` with the prior file. For an emergency, an operator runs
  `docker compose -p <app> down` / `up -d` over SSH, or stops the project in the
  Container Manager UI.
- **Whole capability.** Stop/delete the runner project in Container Manager; the
  workflow then simply queues with no runner (no effect on alcatraz's storage
  roles). Delete `deploy-alcatraz.yml` to remove the trigger entirely.
- **No data-loss path.** Because no deploy is permitted to mount the photo/iSCSI
  volumes (see security), a bad deploy can at worst break a *self-contained*
  alcatraz app, never the storage roles. There is no BTRFS-snapshot-based
  git-driven rollback (unlike hestia's ZFS), so rollback is "revert + redeploy,"
  which is sufficient given the no-shared-state rule.

## Synology-specific gotchas

- **DSM major upgrades can disable packages.** A DSM major-version upgrade may
  stop Container Manager and occasionally reset package state. After any DSM
  upgrade, the operator must confirm Container Manager is running and the runner
  came back (it will, given `restart: unless-stopped` + persisted
  `/volume1/docker/actions-runner/work/.runner`, but verify).
- **No declarative app API.** Unlike TrueNAS, there is no supported "set the
  whole compose and reconcile" call; `docker compose up -d` is the contract.
  Drift between the Container Manager UI's view and the git compose is possible
  if someone edits in the UI — treat git as canonical and avoid UI edits.
- **BTRFS, not ZFS.** No `zfs snapshot` pre-change safety net; rely on
  revert-and-redeploy. DSM's own BTRFS snapshots are not git-driven.
- **`docker.sock` path / group.** Confirm the socket path and the automation
  account's group membership on this DSM build; Synology occasionally relocates
  package data across DSM versions.
- **Auto-update of packages.** If DSM auto-updates Container Manager, the Docker
  Engine version can change under the runner. Pin nothing you can't, and keep
  `DISABLE_AUTO_UPDATE: "true"` on the runner image itself.
- **Image arch.** Every target app image (and the runner image) must have a tag
  for alcatraz's arch (P3). An x86-only image fails to pull on an ARM unit — the
  wrapper's explicit `pull` step turns that into a clear job failure.
- **`myoung34/github-runner` omits Compose on ARM.** If alcatraz is `aarch64`,
  the stock runner image has the Docker CLI but **not** the Compose plugin, so
  the apply step would fail. Mitigation: a small derived image
  (`FROM myoung34/github-runner` + install `docker-compose-plugin`) or a
  job-start install/verify in the wrapper. On x86_64 the stock image is fine.

## Ops / observability

- **Deploy visibility:** GitHub Actions run history per merge (same as hestia) —
  job summary lists discovered apps + per-app result. This is the primary
  advantage over the Task-Scheduler fallback.
- **Runner liveness:** the runner's container healthcheck (`pgrep -f
  Runner.Listener`) + the GitHub runners page (Idle/Offline). Optionally add the
  runner to the same drift-detection pattern hestia documents
  (`docker inspect` image+env keys vs the committed compose).
- **No Prometheus wiring in v1.** If alcatraz gains real workloads, add a
  cAdvisor/node-exporter compose under `hosts/alcatraz/` and scrape it the way
  hestia's thermalscope/ipmi-exporter are scraped — but that is future work,
  gated on there being something to observe.
- **A dormant runner shows Offline by design.** Under the build-but-defer
  recommendation, the runner is registered-then-stopped (or never bootstrapped),
  so the GitHub runners page shows `alcatraz` as Offline. Do **not** wire an
  alert on alcatraz-runner liveness until D4 is actually executed, or it will
  page on an intended state.
- **Filename foot-gun (inherited from hestia).** The discover glob is
  `docker-compose*.yml`. A file named `.yaml` is **silently skipped** — same
  trap as hestia. Document the `.yml` convention in `hosts/alcatraz/README.md`.
- **In-flight job vs DSM reboot.** A DSM upgrade/reboot mid-job fails that run;
  `restart: unless-stopped` brings the runner back and the merge can be
  re-run via `workflow_dispatch`. Not a data risk, just a re-run.

## Recommendation

Build D1–D3 (the compose + script + workflow are cheap, reviewable, and carry no
runtime cost while dormant) and **document** the bootstrap, but **defer D4/D5
(actually running the runner) until a concrete alcatraz-resident workload
exists** that genuinely cannot run on hestia or in the cluster. Rationale:

- Alcatraz is being *simplified*, not expanded (migration + photos-SOT plans).
  Adding a root-capable deploy agent to the host that holds the photo library
  and iSCSI LUNs increases blast-radius against the explicit "don't jeopardise
  those roles" constraint, for no current payload.
- If a workload appears that *must* be alcatraz-local (e.g. something that needs
  direct BTRFS/`/volume1` locality or a Synology-only integration), the runner
  bootstraps in minutes from the already-merged D1–D3 artifacts.
- If we instead want the absolute-minimum-infra option, the **DSM Task Scheduler
  pull-based fallback** (cron `git pull` + `docker compose up -d`) avoids the
  PAT and the runner container entirely, at the cost of GitOps fidelity and the
  CI deploy surface. Documented above as the fallback, not the recommendation.

## Open questions

1. **Is there actually a workload destined for alcatraz?** If no, this is
   capability-for-its-own-sake on a host we're winding down. (Drives the
   build-but-defer recommendation.)
2. **Arch + DSM version** (P3/P2) — unconfirmed without an operator login; sets
   the runner digest and whether compose Projects are available.
3. **Account model** (P4) — dedicated automation account vs an existing admin;
   exact `docker` group mechanics on this DSM build.
4. **Long-term alcatraz fate.** If the migration plans conclude with alcatraz
   fully decommissioned, this workflow should be `superseded`/`abandoned` rather
   than maintained. Revisit at the next migration milestone.
5. **Shared discover logic.** Worth factoring `deploy-hestia` + `deploy-alcatraz`
   into one reusable workflow once a second host exists? (Defer; YAGNI for v1.)

## Critique log

**Round 1 — feasibility & correctness.** Caught that the apply step runs
`docker compose` *inside* the runner container, and `myoung34/github-runner`
**omits the Compose plugin on its ARM builds** — a hard apply-time failure if
alcatraz is `aarch64`. Tied this to P3 and added a derived-image/install
mitigation. Also added: Compose project-name sanitisation (`-p` requires
lowercase DNS-safe names), and clarified the wrapper's `pull` step exists to
surface arch-mismatched images as a clear failure before `up -d`. Confirmed the
`discover` job's GitHub-hosted runner makes enumeration arch-independent.

**Round 2 — security & blast-radius on alcatraz's storage roles.** The first
draft *stated* "don't mount the photo/iSCSI volumes" but didn't enforce it.
Added a CI guard step in `discover` (denylist of protected host paths) as
defence-in-depth, three hard rules (no protected-path mounts; no
privileged/host-net/extra-caps; resource limits + workspace off the photo
volume), and the explicit fact that the GitHub PAT is effectively a
root-on-alcatraz credential given the Docker socket — so it must be guarded and
rotated like one, and must not reuse the rsync `truenas-backup` key.

**Round 3 — maintainability, ops/observability, Synology durability.** Flagged
that building-but-deferring leaves `deploy-alcatraz.yml` unexercised and prone to
drift vs `deploy-hestia.yml` (mitigation: deliberate dry-run-first test at first
real use + re-diff). Added ops notes: a dormant runner shows Offline *by design*
(don't alert on it pre-D4); the `docker-compose*.yml` glob silently skips
`.yaml` files (inherited hestia foot-gun); and a DSM reboot mid-job is a re-run,
not a data risk. No change to the core verdict across any round — the
build-but-defer recommendation held up and was reinforced by the round-2
blast-radius analysis.
