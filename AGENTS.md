# AGENTS.md

> GitOps repo for a 6-node Talos Kubernetes cluster (`melodic-muse`) running self-hosted apps via Flux CD. — https://github.com/gjcourt/homelab

## Commands

| Command | Use |
|---------|-----|
| `kustomize build apps/staging/<overlay>` | Validate a staging overlay |
| `kustomize build apps/production/<overlay>` | Validate a production overlay |
| `kustomize build infra/controllers` | Validate infra controllers |
| `flux get kustomizations -A` | Top-level Kustomization health |
| `flux reconcile kustomization apps-production -n flux-system` | Force production reconcile after merge |
| `flux reconcile source git flux-system-staging` | Force staging git source refresh |
| `kubectl describe helmrelease <name> -n <namespace>` | Inspect a HelmRelease failure |
| `gh workflow run staging-deploy.yaml` | Force a staging branch rebuild |
| `sops -e -i <file>` / `sops -d <file>` | Encrypt / inspect secrets |

Pre-PR: `kustomize build` for every affected overlay must pass.

## Architecture

Flux CD (GitOps) cluster — all cluster state is driven from Git; changes take effect when merged to `master` and reconciled by Flux on each Kustomization's `interval` (default 10m).

- `apps/base/<app>/` — base Kustomize resources (env-agnostic).
- `apps/staging/<app>/`, `apps/production/<app>/` — environment overlays.
- `infra/controllers/` — HelmReleases (monitoring, CNI, CSI, etc.).
- `infra/configs/` — cluster configuration controllers depend on (IP pools, cert issuers).
- `clusters/melodic-muse/` — Flux Kustomization entrypoints.

Reconciliation order: `infra-crds` → `infra-controllers` → `infra-configs` → `apps-production` / `apps-staging`.

See `docs/architecture/` for component-level architecture (DNS strategy, gateway auth, overlays-and-structure).

## Conventions

- **Branch + PR for every change** — never commit directly to `master` or `staging`.
- **Image tags are strictly increasing** — never roll back to an earlier tag without explicit intent. In-repo `build-*.yml` workflows tag each image with an immutable date-sha tag `YYYY-MM-DD-<sha7>` (UTC date + 7-char commit SHA, e.g. `2026-07-26-57ffc01`); the build prints the tag, which you then pin in the app's `deployment.yaml` in a follow-up PR (Flux does not auto-track tags). Older pins may still use the legacy `YYYY-MM-DD` / `YYYY-MM-DD-N` form; new builds are date-sha.
- **Namespace convention**: production uses a `-prod` suffix (`golinks-prod`); staging uses a `-stage` suffix (`golinks-stage`). The base manifest declares the plain name (`golinks`) and each overlay patches `metadata.name`. Apps without a staging variant (`mosquitto`, `cloudflare-tunnel`, `truenas-iscsi-monitor`, etc.) use the plain name.
- **Secrets are SOPS-encrypted** before commit (key ref: `.sops.yaml`); never commit plaintext.
- **Adding a new app**: see `docs/operations/2026-05-02-adding-an-app.md`.
- **Conventional Commits** for every commit (`feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`, `ci:`, `deploy:`).
- **Branch names** follow `<type>/<description>`.

## Invariants

- Never commit directly to `master` — Flux deploys from `master`, all changes go through PR.
- Never push to `staging` manually — CI rebuilds it from `master + open PRs` on every trigger; manual pushes are overwritten.
- Never commit plaintext secrets — encrypt with SOPS first.
- Never bypass staging for production changes — open a PR, let CI merge to staging, validate, then merge to `master`.
- Image tags must be strictly greater than the currently deployed tag.

## What NOT to Do

- Do not branch from another feature/fix branch — always branch from `master`.
- Do not skip `kustomize build` validation before pushing.
- Do not roll back an image tag silently — explicit rollback PRs only.
- Do not put plaintext secrets anywhere in the tree, even in dev/test overlays.
- Do not edit the staging branch directly; CI owns it.

## Domain

6-node Talos Kubernetes cluster (3 control-plane + 3 workers, Talos v1.12.4, Kubernetes v1.35.0) running ~14 self-hosted apps (Audiobookshelf, Authelia, Excalidraw, Golinks, Homepage, Immich, Jellyfin, Linkding, Mealie, Memos, Navidrome, Pingo, Snapcast, Vitals + Adguard / Vitals etc.) plus infrastructure (Cilium 1.19 VXLAN, cert-manager, CNPG, monitoring, Authelia SSO). GitOps via Flux CD with an automatic preview environment (`staging` branch) rebuilt by CI from `master + open PRs`.

## Cross-service dependencies

| Service | Purpose |
|---|---|
| Talos Linux | 6-node Kubernetes substrate (3 control-plane, 3 workers) |
| Flux CD | GitOps reconciliation |
| Cilium + Gateway API | CNI + ingress |
| cert-manager | TLS certificate issuance |
| CNPG (Cloudnative-PG) | PostgreSQL operator |
| Authelia | SSO / OAuth2 / OIDC |
| TrueNAS / democratic-csi | Block storage (iSCSI) + photo NFS backing PVCs |
| GitHub Actions | CI for kustomize build + staging branch rebuild |
| ghcr.io | Container image registry (`gjcourt/<app>`) |

## Quality gate before push

1. `kustomize build` passes for every affected overlay
2. `git diff HEAD | grep -i "password\|secret\|key"` returns no plaintext
3. Image tags are strictly increasing (never silently rolled back)
4. New apps wired into the right `apps/{staging,production}/kustomization.yaml`
5. Namespace follows the convention (production unsuffixed, staging `-stage`)
6. New CNPG clusters: iSCSI PVC provisioned and StorageClass correct
7. Docs updated if the change affects a runbook or architecture doc; `docs/STATUS.md` updated if the change flips a plan's status, lands an incident, or changes hardware/topology
8. Every container has a `readinessProbe`; a `livenessProbe` is added only with a signal distinct from readiness (see `docs/operations/2026-05-02-adding-an-app.md#health-probes`)

## Documentation

**Current state, in-flight work, and known issues: `docs/STATUS.md`** — start here for "what's running / what's next".

`docs/` taxonomy: `architecture/` · `design/` · `operations/` · `plans/` · `reference/` · `research/`. Each folder's `README.md` describes scope. Index: `docs/README.md`. The plans index (`docs/plans/README.md`) is generated from frontmatter by `scripts/plans-index` — edit a plan's frontmatter and run `make plans-index`, never hand-edit the index block.

Per-app runbooks live under `docs/operations/apps/<app>.md`. Incident postmortems live under `docs/operations/incidents/<yyyy-mm-dd>-<topic>.md`. Per-doc content rewrites are tracked in `docs/plans/2026-02-21-documentation-rewrite-plan.md`.

## Observability

- `flux get kustomizations -A` — top-level Kustomization health.
- `kubectl describe helmrelease <name> -n <namespace>` — HelmRelease failures.
- `flux reconcile helmrelease <name> -n <namespace> --reset` — force reconcile after a stalled HelmRelease.
- `kubectl -n <ns> get events --sort-by=.lastTimestamp | tail -n 50` — recent events.
- Common Flux failure patterns and recovery: `docs/operations/2026-05-02-flux-debugging.md`.
- Per-app observability dashboards: see each `docs/operations/apps/<app>.md` runbook.

## Recovering read-only iSCSI volumes (recurring)

**This is the most common failure mode in this cluster.** Every StorageClass is
`democratic-csi` over iSCSI to hestia, so any disruption on that path — a switch
port flap, a target hiccup — causes I/O errors, and Linux remounts the affected
filesystems **read-only**. Nothing recovers from that automatically: not the
kernel, not Kubernetes, not CNPG, not Flux. It always needs an operator.

Recurrences: [2026-02-08](docs/operations/incidents/2026-02-08-pv-recovery.md) ·
[2026-02-12](docs/operations/incidents/2026-02-12-iscsi-zombie-targets.md) ·
[2026-02-15](docs/operations/incidents/2026-02-15-iscsi-targets-disabled.md) ·
[2026-02-27](docs/operations/incidents/2026-02-27-homeassistant-staging-iscsi-io-error.md) ·
[2026-02-28](docs/operations/incidents/2026-02-28-iscsi-mass-readonly-cnpg-loki-immich.md) ·
[2026-08-13](docs/operations/incidents/2026-08-13-iscsi-readonly-remount-monitoring-blind.md).
Tracking issue: [#1080](https://github.com/gjcourt/homelab/issues/1080).

### Recognising it

Pods stay `Running` and often `Ready` — **status lies here**. Look for
`read-only file system` in logs, and confirm per-pod rather than trusting status:

```bash
kubectl -n <ns> exec <pod> -c <ctr> -- sh -c 'printf ok > <mount>/.wtest && rm -f <mount>/.wtest'
```

Write bytes, don't just `touch`: creating a zero-byte file can succeed on a
filesystem with no free space, so `touch` alone passes a volume that is full.

### Why the metric will not tell you (measured 2026-08-26)

**`NodeFilesystemReadOnly` cannot detect this failure.** Not "sometimes misses
it" — cannot. On 2026-08-13 both AdGuard `work` volumes went read-only and the
alert stayed silent for 13 days. Both of these were true at the same instant:

```text
kubectl -n adguard-prod exec adguard-0 -c adguard -- \
  sh -c 'printf ok > /opt/adguardhome/work/.p'
  -> sh: can't create ...: Read-only file system          # hard EROFS

node_filesystem_readonly{fstype="ext4"} == 1  ->  0 series  # "everything fine"
```

The mount was **not** unmonitored — `node_filesystem_readonly` scrapes the CSI
globalmounts (69 series). The metric was scraped and it was **wrong**. ext4's
`emergency_ro` error mode fails every write with `EROFS` while leaving the mount
still advertising `rw`; node-exporter reports what the mount claims, so the gauge
stays `0` forever.

**No mount-flag scan can close this gap, because the mount flag is the thing that
is lying.** Only an actual write distinguishes a working volume from a dead one.
That is what `pvc-writeprobe` (`infra/configs/pvc-writeprobe/`) does — it execs a
few bytes into `<mount>/.homelab-writeprobe` in every pod that mounts a PVC
read-write, removes it, and exports `homelab_pvc_writable{namespace,pvc,pod}`.
Alert: `PvcNotWritable`.

Two caveats that are also load-bearing:

- The probe is evaluated **by Prometheus**, so if Prometheus' own PVC is affected
  it goes mute. Silence is not health — `PvcWriteProbeStale` / `PvcWriteProbeAbsent`
  exist to make the silence itself alert.
- Volumes mounted `readOnly: true` (Jellyfin/audiobookshelf media, immich
  homevideo) are deliberately **not** probed — a write there would fail by design.
  To exclude anything else, annotate the pod
  `homelab.burntbytes.com/writeprobe-skip: "all"` or `"<pvc>,<pvc>"`.

### Recovery, by workload type

The goal is always the same: **force an iSCSI detach and reattach.** How much
force depends on the workload.

| Workload | Action | Why |
|---|---|---|
| StatefulSet pod (Prometheus, Loki) | `kubectl delete pod <pod>` | Recreated in place; the delete is enough to detach |
| **CNPG primary** | `kubectl delete pod <primary>` — **never the PVC** | The PVC *is* the data |
| **CNPG replica** | delete the pod; if it still fails, **also delete its PVC** | A replica is reconstructible — CNPG re-clones from the primary. Precedent: 2026-02-28, where a replica PVC came back empty and only `delete pvc` recovered it |
| Deployment on RWO | scale-to-0 cycle (below) | `rollout restart` is **not** sufficient — see below |

For CNPG, **restart the primary first**: replicas stream WAL from it and cannot
sync until it serves. Before deleting a primary, confirm the replicas are
*unready* — otherwise the delete triggers an unplanned switchover. `kubectl cnpg
restart <cluster>` is the supported path when replicas are healthy.

### Deployments on RWO need the full cycle

`rollout restart` creates the replacement pod **before** terminating the old one,
so on a ReadWriteOnce volume the new pod attaches to the same errored device and
comes up read-only again. Two further traps: **Flux reverts a scale-to-0** within
the reconcile interval, and pod termination is *not* proof the device was
released — `volumeattachment` reaching zero is.

```bash
flux suspend kustomization apps-production -n flux-system   # else Flux undoes the scale
kubectl -n <ns> scale deploy/<app> --replicas=0
until [ "$(kubectl -n <ns> get pods -l app=<app> --no-headers 2>/dev/null | wc -l | tr -d ' ')" -eq 0 ]; do sleep 5; done
PV=$(kubectl -n <ns> get pvc <pvc> -o jsonpath='{.spec.volumeName}')
kubectl get volumeattachment -o jsonpath="{range .items[?(@.spec.source.persistentVolumeName=='$PV')]}{.metadata.name}{'\n'}{end}"   # must be empty
kubectl -n <ns> scale deploy/<app> --replicas=1
flux resume kustomization apps-production -n flux-system     # ALWAYS, even if the above failed
```

`tr -d ' '` matters: BSD/macOS `wc -l` emits leading whitespace, so a string
comparison against `"0"` never matches and the loop hangs — with production
reconciliation suspended. Use `-eq`, and if you abort partway, **resume Flux
first**. Verify with `flux get kustomizations -A` showing `SUSPENDED=False`.

### After recovery

Re-check writability per pod (status will look fine either way), confirm CNPG
clusters report full instance counts, and confirm Flux is reconciling on all
Kustomizations.

When you learn a new convention or invariant in this repo, update this file.
