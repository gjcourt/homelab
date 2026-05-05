---
status: in-progress
last_modified: 2026-05-04
parent_plan: 2026-05-02-critique-remediation.md
---

# Phase 2–5 Completion Plan

Audit run on 2026-05-04 against `master` (post-PR #473). Phases 2.1, 2.3, 2.4
(partial), 3 (partial), and 5 are already complete. This plan covers what
remains and packages it into four independently-parallelisable PRs.

## What's already done

| Item | Status |
|---|---|
| All Deployments have `resources.requests` + `limits` | ✅ done |
| `revisionHistoryLimit: 5` on all Deployments | ✅ done |
| HelmRelease `upgrade.remediation` on all controllers | ✅ done |
| No `:latest` image tags remaining | ✅ done |
| `app.kubernetes.io/*` labels on all kustomizations | ✅ done |
| `storageClassName` set explicitly everywhere | ✅ done |
| gjcourt/* images pinned by date tag or digest | ✅ done |

## What remains — four parallel PRs

### PR A — Health probes  (`fix/health-probes`)

**Closes:** Phase 2 / PR 2.2 (findings #8, #9, #10, #22).

Add `livenessProbe` to the 10 apps that are missing one:
`audiobookshelf`, `excalidraw`, `homepage`, `jellyfin`, `linkding`,
`mealie`, `memos`, `openwebui`, `snapcast`, `synology-iscsi-monitor`.

Add `readinessProbe` to `homeassistant` (the only app missing one entirely).

Rules:
- Use an HTTP GET to an existing health/readiness path where one exists
  (check the app's docs or existing probes in other deploys for the path).
  Fall back to a TCP socket probe if no HTTP endpoint is documented.
- Do NOT add a `livenessProbe` that hits the same path as `readinessProbe`
  with the same timing — use `failureThreshold: 6` / `periodSeconds: 20`
  (tolerant) for liveness vs tighter settings for readiness.
- For slow-start apps (jellyfin, openwebui): add a `startupProbe` with
  `failureThreshold: 30` / `periodSeconds: 10` (5 min budget) and tighten
  the `livenessProbe` to run only after startup completes.
- `synology-iscsi-monitor` is a daemon with no HTTP server — use a
  process/exec probe (`exec: command: [pgrep, -x, <process-name>]`).

**Validation:** `kustomize build` passes; no pod enters `CrashLoopBackOff`
in staging after probe is added.

---

### PR B — PodDisruptionBudgets  (`fix/missing-pdbs`)

**Closes:** Phase 2 / PR 2.4 (finding #13, partial).

Add `pdb.yaml` to the 12 apps that are missing one:
`audiobookshelf`, `authelia`, `excalidraw`, `hermes`, `homeassistant`,
`homepage`, `linkding`, `memos`, `overture`, `signal-cli`, `snapcast`,
`synology-iscsi-monitor`.

Rules:
- Single-replica apps: `maxUnavailable: 0` (drain blocks until pod
  reschedules elsewhere — prevents data-loss windows on rolling drains).
- Multi-replica apps: `minAvailable: 1`.
- `synology-iscsi-monitor` is infrastructure — use `maxUnavailable: 1`
  (it can briefly disappear without user impact).
- Wire each new `pdb.yaml` into the app's `apps/base/<app>/kustomization.yaml`.

**Validation:** `kubectl drain <node> --dry-run` respects PDBs (drain
reports "cannot evict pod as it would violate the pod's disruption budget").

---

### PR C — Image/secret hygiene  (`fix/image-secret-hygiene`)

**Closes:** Phase 3 / PR 3.2 (findings #15, #16, partial).

Two small fixes:

1. **openwebui digest pin** — replace `ghcr.io/open-webui/open-webui:v0.9.2`
   with `ghcr.io/open-webui/open-webui:v0.9.2@sha256:<digest>`.
   Get digest with: `docker buildx imagetools inspect ghcr.io/open-webui/open-webui:v0.9.2`
   or `crane digest ghcr.io/open-webui/open-webui:v0.9.2`.

2. **signal-cli imagePullSecret** — `signal-bridge` pulls from
   `ghcr.io/gjcourt/signal-bridge` (private). Create
   `apps/base/signal-cli/secret-ghcr.yaml` (same pattern as
   `apps/base/golinks/secret-ghcr.yaml`) and add it to the kustomization.

**Validation:** `kustomize build apps/production/signal-cli` includes the
secret; `kustomize build apps/production/openwebui` shows the digest form.

---

### PR D — Architecture cleanup  (`fix/staging-parity`)

**Closes:** Phase 4 / PR 4.1 and 4.2 (findings #18, #19).

Two parts:

1. **vitals staging namespace** — `apps/staging/vitals/` has 8 files with
   hardcoded `namespace: vitals`. Change all to `namespace: vitals-stage`
   to match kustomize convention. Files: `httproute.yaml`,
   `kustomization.yaml`, `secret-aws-creds.yaml`, `database.yaml`,
   `scheduledbackup.yaml`, `objectstore.yaml`, `configmap.yaml`,
   `secret-db-credentials.yaml`.

2. **Staging parity decisions** — 5 production apps lack staging overlays:
   `cloudflare-tunnel`, `external-services`, `openwebui`, `overture`,
   `synology-iscsi-monitor`.
   - `openwebui` and `overture`: add thin staging overlays (namespace patch
     only, copy from any existing staging overlay, add to
     `apps/staging/kustomization.yaml`).
   - `cloudflare-tunnel`, `synology-iscsi-monitor`, `external-services`:
     document in `apps/base/<app>/README.md` why staging is intentionally
     omitted (cloudflare-tunnel needs account credentials; synology-iscsi-monitor
     is hardware-coupled; external-services reverse-proxies LAN appliances
     that don't exist in staging).

**Validation:** `kustomize build apps/staging` succeeds cleanly for all
apps; vitals staging namespace resolves to `vitals-stage`.

---

## Parallelisation notes

All four PRs touch disjoint file sets and can be opened simultaneously:
- PR A: `apps/base/*/deployment.yaml` (probe additions only)
- PR B: `apps/base/*/pdb.yaml` + `apps/base/*/kustomization.yaml`
- PR C: `apps/base/openwebui/deployment.yaml` + `apps/base/signal-cli/*`
- PR D: `apps/staging/vitals/*` + `apps/base/*/README.md` + staging kustomizations

Merge order is flexible — none depends on another landing first.

## Post-merge

Once all four PRs land, update `docs/plans/2026-05-02-critique-remediation.md`
`status:` to `complete` and open the default-deny
`CiliumClusterwideNetworkPolicy` rollout (Phase 1.1 step 4) namespace by
namespace, starting with `excalidraw` (stateless canary).
