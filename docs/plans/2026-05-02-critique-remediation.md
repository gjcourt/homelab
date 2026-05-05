---
status: in-progress
last_modified: 2026-05-04
---

# Critique Remediation Plan — IaC hardening for melodic-muse

## Context

A `/critique` pass against the homelab repo on 2026-05-02 (`f71eab6`) produced 22 actionable findings: **4 Major, 16 Moderate, 2 Minor**. The biggest gaps are security baseline (zero NetworkPolicies, sparse Pod security context, no RBAC), reliability (apps running BestEffort QoS, sparse health probes, single PodDisruptionBudget), and image discipline (`:latest` tags, mostly-unpinned third-party images).

This plan sequences the work into **5 phases / 13 PRs**, ordered so that each phase is independently shippable and the highest-impact gaps land first. Every PR is small enough to review in one sitting (≤10 files where practical) and validated in staging before reaching production.

**Rules of engagement:**
- One workstream per PR; do not bundle unrelated fixes.
- Apply each change first to one canary app, watch staging, then fan out.
- For NetworkPolicies and PSA enforcement specifically: never enable cluster-wide before per-app rollout completes.
- Update this plan's `status:` to `in-progress` when phase 1 starts; to `complete` when phase 5 lands.

---

## Findings inventory

| # | Severity | Category | Location | Phase / PR |
|---|---|---|---|---|
| 1 | Major | ops | `apps/base/signal-cli/deployment.yaml:23,57` — `:latest` tags | Phase 3 / PR 3.1 |
| 2 | Major | security | NetworkPolicies absent cluster-wide | Phase 1 / PR 1.1 |
| 3 | Major | ops | authelia/excalidraw/linkding/memos missing `requests` (BestEffort QoS) | Phase 2 / PR 2.1 |
| 4 | Major | security | Default ServiceAccount + `automountServiceAccountToken: true` on homepage | Phase 1 / PR 1.3 |
| 5 | Moderate | security | `runAsNonRoot: true` on only ~12/30 deployments | Phase 1 / PR 1.2 |
| 6 | Moderate | security | `readOnlyRootFilesystem: true` on only ~5/30 | Phase 1 / PR 1.2 |
| 7 | Moderate | ops | ~15 deployments have `requests` but no `limits` | Phase 2 / PR 2.1 |
| 8 | Moderate | ops | Sparse health probes (9 liveness, 13 readiness across apps) | Phase 2 / PR 2.2 |
| 9 | Moderate | ops | golinks probes identical (same path, different timing) | Phase 2 / PR 2.2 |
| 10 | Moderate | ops | adguard missing livenessProbe | Phase 2 / PR 2.2 |
| 11 | Moderate | ops | 18/20 HelmReleases lack `upgrade.remediation` | Phase 2 / PR 2.3 |
| 12 | Moderate | ops | `revisionHistoryLimit` not set explicitly anywhere | Phase 2 / PR 2.4 |
| 13 | Moderate | ops | 1 PodDisruptionBudget (adguard only) | Phase 2 / PR 2.4 |
| 14 | Moderate | ops | `priorityClassName` empty on Cilium / cluster-critical workloads | Phase 2 / PR 2.4 |
| 15 | Moderate | security | `imagePullSecrets` only on 3 apps | Phase 3 / PR 3.2 |
| 16 | Moderate | ops | Only 19/66 third-party images pinned by `@sha256:` | Phase 3 / PR 3.2 |
| 17 | Moderate | storage | NFS PVCs use `storageClassName: ""` | Phase 4 / PR 4.1 |
| 18 | Moderate | architecture | Staging missing 5 production apps | Phase 4 / PR 4.2 |
| 19 | Moderate | architecture | Vitals staging secrets hardcode `namespace: vitals` | Phase 4 / PR 4.1 |
| 20 | Moderate | security | homepage `automountServiceAccountToken: true` (rolled into RBAC PR) | Phase 1 / PR 1.3 |
| 21 | Minor | maintainability | `app.kubernetes.io/*` labels sparse | Phase 5 / PR 5.1 |
| 22 | Minor | ops | No `startupProbe` on slow-start apps (immich, jellyfin) | Phase 2 / PR 2.2 |

**Cross-references** the original critique report content if `--write` was used to save it; otherwise this table is the canonical inventory.

---

## Phase 1 — Security baseline (highest priority)

**Goal:** close the three security gaps that compound: open networking, root-by-default containers, and ambient API privilege.

**Order:** PR 1.1 first (network), then 1.2 (pod security), then 1.3 (RBAC), then 1.4 (PSA enforcement). Each is gated on the previous landing in staging without regression.

### PR 1.1 — Default-deny NetworkPolicies + per-app allow rules

**Closes:** finding #2.

**Approach** — Cilium-native (the cluster runs Cilium, so prefer `CiliumNetworkPolicy` over generic `networking.k8s.io/v1` NetworkPolicy where features differ; both work, but Cilium policies give richer L7 rules if needed later):

1. Create `infra/configs/network-policies/default-deny.yaml` — a single `CiliumClusterwideNetworkPolicy` denying all ingress except `kube-system` and `flux-system`. Apply with a feature gate first (cluster label `network-policies: enabled`) so it can be flipped on per namespace.
2. For each app namespace, add `apps/base/<app>/networkpolicy.yaml` with:
   - Ingress: only from the Gateway namespace, plus same-namespace-pod for sidecars
   - Egress: kube-dns, namespace-internal, and explicit external endpoints (e.g., postgres → CNPG namespace, adguard → upstream resolvers)
3. Roll out namespace-by-namespace: start with `excalidraw` (stateless, easy to validate), watch Cilium drop metrics for 24h, expand.
4. Once every namespace has a NetworkPolicy, flip the cluster-wide default-deny on.

**Files touched** — one `networkpolicy.yaml` per app (~21 files), plus `infra/configs/network-policies/`.

**Validation:**
- Run `cilium policy verdict` per pod after each rollout step
- Watch `hubble observe --verdict DROPPED` on staging for 1 hour after each app
- Functional smoke: every Gateway-fronted route still resolves; SSO still flows through Authelia

**Done when:** every namespace has a NetworkPolicy, the cluster-wide default-deny is on, and `hubble observe --verdict DROPPED` over 24h shows no unintended drops.

### PR 1.2 — Pod security baseline (runAsNonRoot, readOnlyRootFilesystem, capabilities, seccomp)

**Closes:** findings #5, #6.

**Approach** — Kustomize patch overlay applied at `apps/base/<app>/`:

1. Create `apps/base/_common/pod-security-baseline.yaml` — a JSON-patch fragment that applies:
   ```yaml
   securityContext:
     runAsNonRoot: true
     seccompProfile: { type: RuntimeDefault }
   containers[*].securityContext:
     allowPrivilegeEscalation: false
     readOnlyRootFilesystem: true
     capabilities: { drop: [ALL] }
   ```
2. Per app, in its base `kustomization.yaml`, add the patch reference. Override `runAsUser`/`runAsGroup` per-app where the image has a known UID (Jellyfin, Immich, Authelia all document this).
3. Where `readOnlyRootFilesystem: true` breaks an app (e.g., it writes to `/tmp` or a cache path), add an `emptyDir` volume mount for that specific path — never disable the protection.
4. Fan out: start with one of the already-compliant apps to verify the patch fragment doesn't regress, then expand.

**Files touched** — one common patch + one kustomization edit per app (~30 files).

**Validation:**
- Each PR-batch (no more than 5 apps): `kubectl get pods -n <ns> -o jsonpath='{.items[*].spec.securityContext}'` shows the baseline
- Functional smoke per app
- Watch for `CrashLoopBackOff` from `readOnlyRootFilesystem` violations; common culprits: `/tmp` writers, cache dirs

**Done when:** every Deployment in `apps/base/` has the baseline applied; no app is in CrashLoopBackOff in staging or production.

### PR 1.3 — Per-app ServiceAccount + RBAC scoping + token automount hygiene

**Closes:** findings #4, #20.

**Approach:**
1. For each app, generate an explicit `ServiceAccount` named `<app>` in `apps/base/<app>/serviceaccount.yaml` with `automountServiceAccountToken: false`.
2. Reference it from the Deployment via `spec.template.spec.serviceAccountName: <app>`.
3. For the **few** apps that legitimately need API access (homepage's cluster widgets, if used; nothing else identified), create a tightly-scoped `Role` + `RoleBinding` in the app's namespace and set `automountServiceAccountToken: true` only on those Pods.
4. Audit the homepage configmap: if cluster widgets are not actually configured, set `automountServiceAccountToken: false` and ship.

**Files touched** — one `serviceaccount.yaml` per app + Deployment edit (~30 files).

**Validation:**
- `kubectl auth can-i --list --as=system:serviceaccount:<ns>:<app>` shows minimal/no permissions
- For homepage specifically: confirm cluster widgets still render (or confirm they're disabled)

### PR 1.4 — Pod Security Admission enforcement

**Closes:** the implicit gap that PR 1.2 leaves open (nothing prevents future regressions).

**Approach:**
1. Once PRs 1.1–1.3 are in, label every app namespace:
   ```yaml
   metadata:
     labels:
       pod-security.kubernetes.io/enforce: restricted
       pod-security.kubernetes.io/audit: restricted
       pod-security.kubernetes.io/warn: restricted
   ```
2. Roll out as a single PR touching the Namespace manifests. The label is enforcement-only — if any pod violates it, admission is denied at create/update time but already-running pods are unaffected (so no CrashLoopBackOff risk; only the next deploy that violates is blocked).

**Files touched** — Namespace manifests (~20 files).

**Validation:**
- Try to deploy a deliberate violation (a pod with `runAsNonRoot: false`) → admission denied with a clear PSA error
- All existing apps continue to deploy

**Done when:** every app namespace has the `restricted` PSA label and a forced-bad deploy is rejected at admission.

---

## Phase 2 — Reliability + resource hygiene

**Goal:** close the BestEffort-QoS, missing-probes, and rollback-safety gaps.

### PR 2.1 — Resource requests + limits

**Closes:** findings #3, #7.

**Approach:**
1. Add `resources.requests` (CPU, memory) and `resources.limits` (CPU, memory) to every container in `apps/base/*/deployment.yaml`.
2. Authelia first (its eviction breaks SSO); then excalidraw, linkding, memos.
3. Then fill `limits` on the ~15 apps that have `requests` but no `limits`.
4. Sizing baseline (start here; tune from Prometheus over 1 week):
   - Stateless web apps: `requests: { cpu: 50m, memory: 64Mi }`, `limits: { cpu: 500m, memory: 256Mi }`
   - Authelia / SSO: `requests: { cpu: 100m, memory: 128Mi }`, `limits: { cpu: 500m, memory: 512Mi }`
   - Media (Jellyfin, Immich): start with documented chart defaults; will need GPU-aware tuning
5. Use Kustomize `replacements` or per-app patches; do NOT bundle into a global default that hides per-app intent.

**Files touched** — ~19 deployment.yaml files.

**Validation:**
- `kubectl get pod <p> -o jsonpath='{.status.qosClass}'` returns `Burstable` or `Guaranteed` (never `BestEffort`) for every app
- After 1 week, review Prometheus container CPU/memory and tighten limits

**Done when:** no app reports QoS class `BestEffort`.

### PR 2.2 — Health probes (readiness, liveness, startupProbe)

**Closes:** findings #8, #9, #10, #22.

**Approach:**
1. For every Service-fronted app, add a `readinessProbe` hitting an endpoint that reflects "ready to serve" (e.g., `/health/ready`, or a config-driven `/api/v1/check` like signal-bridge).
2. Add a `livenessProbe` only where a distinct "liveness" endpoint exists; otherwise omit (a flapping liveness is worse than no liveness).
3. For golinks specifically: split `/health/live` (process alive) from `/health/ready` (DB reachable). This is an app-side change, then update the manifest.
4. For adguard: add `livenessProbe` with `failureThreshold: 6, periodSeconds: 30` (tolerant — adguard's filtering rules can pause).
5. For immich + jellyfin: replace `livenessProbe.initialDelaySeconds: 300+` with a `startupProbe` that allows up to 10 minutes, then a tight `livenessProbe` after.

**Files touched** — most `apps/base/*/deployment.yaml` files; possibly a small change to the golinks app source if a distinct liveness endpoint isn't yet there.

**Validation:**
- For each app: `kubectl describe pod <p>` shows distinct probe definitions
- Functional smoke: a deliberate DB outage on golinks marks it NotReady but does not restart the pod
- Immich library scan no longer trips liveness during full re-indexing

### PR 2.3 — HelmRelease `upgrade.remediation`

**Closes:** finding #11.

**Approach:**
1. For each `infra/controllers/*/release.yaml`, add:
   ```yaml
   spec:
     upgrade:
       remediation:
         retries: 3
         strategy: rollback
   ```
2. Use `strategy: uninstall` only for charts with stuck-StatefulSet history (current candidates: any chart that creates StatefulSets — postgres operator, monitoring stack). The AGENTS.md table calls this out.
3. Loki already has `strategy: uninstall` (verified) — leave it.

**Files touched** — ~18 `release.yaml` files.

**Validation:**
- Force a known-bad upgrade in staging (e.g., bump a chart to a known-broken version), verify Flux remediates within 3 retries
- `flux get helmrelease -A` shows no `Failed` for >5min after rollback

### PR 2.4 — `revisionHistoryLimit`, PodDisruptionBudgets, priorityClassName

**Closes:** findings #12, #13, #14.

**Approach:**

*revisionHistoryLimit:* set `spec.revisionHistoryLimit: 5` on every Deployment. Single-line edit per file.

*PodDisruptionBudgets:* one `pdb.yaml` per HA-relevant app. Targets:
- `immich`, `jellyfin`, `mealie`, `navidrome`, `vitals`, `golinks` (DB-backed, want zero downtime on drains)
- For single-replica apps: `maxUnavailable: 0` so node drains pause until a fresh replica is up. For multi-replica: `minAvailable: 1`.

*priorityClassName:* set on cluster-critical workloads via HelmRelease `values:` overrides:
- Cilium agent + operator: `system-node-critical` (it's the network — eviction means everything dies)
- CNPG operator + clusters: `system-cluster-critical`
- cert-manager controller: `system-cluster-critical`
- monitoring stack (kube-prometheus-stack, loki): `system-cluster-critical`

**Files touched** — `apps/base/*/deployment.yaml` for revisionHistoryLimit; new `pdb.yaml` per HA app; HelmRelease `values:` blocks for priorityClassName.

**Validation:**
- `kubectl get deploy -A -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.revisionHistoryLimit}{"\n"}{end}'` shows 5 everywhere
- `kubectl drain` of a worker node respects PDBs (drain pauses until reschedule completes)
- `kubectl describe priorityclass system-node-critical` shows Cilium pods consuming it

---

## Phase 3 — Image discipline

### PR 3.1 — Replace `:latest` tags

**Closes:** finding #1.

**Approach:**
1. `apps/base/signal-cli/deployment.yaml:23` — replace `ghcr.io/asamk/signal-cli:latest` with `@sha256:` digest:
   ```bash
   docker pull ghcr.io/asamk/signal-cli:latest
   docker inspect --format '{{index .RepoDigests 0}}' ghcr.io/asamk/signal-cli:latest
   ```
   Use that digest. (As of 2026-05-02 this is `@sha256:23a808b97eaa65e15f09809e5644aedf33e838db833552dfe825ca52dcd0940e`, already pinned in `hosts/hestia/signal/docker-compose.yml`.)
2. `apps/base/signal-cli/deployment.yaml:57` — replace `gjcourt/signal-bridge:latest` with the date tag published by `.github/workflows/build-signal-bridge.yml` (depends on PR #362 having merged and run).
3. **Constraint:** do NOT roll back to an arbitrary date tag — pick the latest published tag at the moment the PR opens.

**Files touched** — 1 file, 2 lines.

**Validation:**
- `grep -rE 'image:.*:latest' apps/ infra/` returns nothing
- The pods run the expected image (`kubectl get pod <p> -o jsonpath='{.spec.containers[*].image}'`)

### PR 3.2 — Pin third-party images by digest + consolidate `imagePullSecrets`

**Closes:** findings #15, #16.

**Approach:**
1. For each third-party image (non-`gjcourt/*`) in `apps/base/*/deployment.yaml`, replace tag-based references with `@sha256:` digests. Tools: `docker buildx imagetools inspect` or `crane digest`.
2. Standardize `imagePullSecrets` for any app pulling `gjcourt/*` private images: create a single `secret-ghcr.yaml` per app following the existing pattern (golinks/overture/vitals), referenced from the Deployment.
3. First-party `gjcourt/*` images keep date-tagged; CI guarantees immutability per `AGENTS.md`.

**Files touched** — ~30 deployment.yaml edits, plus new `secret-ghcr.yaml` files.

**Validation:**
- After this PR, `grep -rE 'image:.*[^:]:[a-z0-9.-]+$' apps/base/` (tag-only references) returns only `gjcourt/*` images
- `kubectl describe pod` shows the resolved digest matches what we pinned

---

## Phase 4 — Architecture cleanup

### PR 4.1 — NFS storageClass + vitals secret namespace cleanup

**Closes:** findings #17, #19.

**Approach:**
1. `apps/staging/immich/nfs-photos.yaml`, `apps/base/jellyfin/media/nfs-media.yaml` — set `storageClassName:` to the explicit class (whatever the actual NFS provisioner is named; check via `kubectl get sc`).
2. `apps/staging/vitals/secret-{db-credentials,aws-creds}.yaml` — change inline `namespace: vitals` to `namespace: vitals-stage` so the source matches the kustomize-resolved value.

**Files touched** — 4 files.

**Validation:**
- `kustomize build apps/staging/vitals` output namespace is unchanged (`vitals-stage`)
- `kustomize build apps/staging/immich` resolves to the explicit storageClass

### PR 4.2 — Staging parity: add overlays for missing apps

**Closes:** finding #18.

**Approach:**
1. For each of the 5 apps missing from staging — `cloudflare-tunnel`, `overture`, `synology-iscsi-monitor`, `signal-cli`, `snapcast`:
   - Decide: is staging genuinely impossible (e.g., signal-cli has linked-device state that can't be cloned)?
   - If yes: document the exception in the app's `apps/base/<app>/README.md` with a one-paragraph "why no staging" note.
   - If no: add `apps/staging/<app>/kustomization.yaml` (thin namespace patch, copy from any existing staging overlay), encrypted secrets for staging, and add to `apps/staging/kustomization.yaml`.
2. Best candidates for staging: `cloudflare-tunnel`, `synology-iscsi-monitor`, `snapcast`. `overture` and `signal-cli` likely warrant the documented exception.

**Files touched** — new staging overlays per included app; per-app README updates for excluded apps.

**Validation:**
- `kustomize build apps/staging` succeeds and produces a namespace per included app (`<app>-stage`)
- Flux reconciles each new staging Kustomization
- Functional smoke per app in staging

---

## Phase 5 — Polish

### PR 5.1 — `app.kubernetes.io/*` labels + `commonLabels` rollout

**Closes:** finding #21.

**Approach:**
1. Add a `labels:` block to each `apps/base/<app>/kustomization.yaml`:
   ```yaml
   labels:
     - includeSelectors: false
       pairs:
         app.kubernetes.io/name: <app>
         app.kubernetes.io/component: <web|worker|database>
         app.kubernetes.io/part-of: homelab
         app.kubernetes.io/managed-by: flux
   ```
2. Drop the legacy `app: <name>` label gradually; keep it for one PR cycle as both, then remove once dashboards confirm they query the new label.
3. `app.kubernetes.io/version` is best set by CI from the image tag — defer or wire into the build workflow.

**Files touched** — ~20 kustomization.yaml files.

**Validation:**
- `kubectl get pods -A -l app.kubernetes.io/managed-by=flux` returns every Flux-managed pod
- Existing dashboards continue to work (legacy label still present)

---

## Sequencing summary

```
Phase 1 (security)           ───►  Phase 2 (reliability)  ─┐
                                                           │
Phase 3 (image discipline)   ───►                          ├──►  Phase 5 (polish)
                                                           │
Phase 4 (architecture)       ───►                          ┘
```

Phase 1 must complete before any Phase 4 staging additions (new apps inherit security baseline by default).
Phase 2 can run in parallel with Phase 3 (independent file sets).
Phase 5 lands last — it's cosmetic and easier to review against a stable cluster.

---

## Verification per phase

| Phase | Smoke | Soak |
|---|---|---|
| 1 | All apps deploy in staging; admission rejects deliberate violations | 1 week of `hubble observe --verdict DROPPED` shows no false positives |
| 2 | No `BestEffort` QoS; `flux get helmrelease -A` clean; PDB blocks node drain | 1 week of Prometheus container metrics for limit-tuning |
| 3 | `grep -rE 'image:.*:latest' apps/ infra/` returns nothing; `docker manifest inspect` succeeds for every pinned digest | 1 reconcile cycle (10m) without `ImagePullBackOff` |
| 4 | `kustomize build apps/staging` resolves cleanly for all included apps | 1 staging-deploy CI cycle without errors |
| 5 | Dashboards still load against legacy and new labels in parallel | N/A — cosmetic |

---

## Out of scope

- **Disaster recovery drill** — the lens flagged it as a gap; deliberate to keep this plan focused on cluster-config hardening. Plan a separate DR drill (restore-from-snapshot exercise) after Phase 1.
- **Backup automation for non-CNPG PVCs** — CNPG already has barman-cloud; other stateful PVCs (Jellyfin metadata, Immich uploads on NFS) need a separate backup plan.
- **Image automation (Flux Image Update Automation)** — keep image bumps manual via PR per current convention. Re-evaluate after Phase 3.
- **Resource limit tuning from production telemetry** — initial values in PR 2.1 are conservative; tune in a follow-up PR after 1 week of Prometheus data.

---

## Tracking

Update this plan's `status:` field as phases land:
- Phase 1 in flight → `in-progress`
- All phases complete → `complete`

Per-PR checklist (paste into each PR description):

```
- [ ] kustomize build passes for all affected overlays
- [ ] No plaintext secrets in diff (`git diff HEAD | grep -iE 'password|secret|token'`)
- [ ] Image tags strictly increasing (or pinned by digest)
- [ ] Tested in staging for at least one Flux reconcile cycle
- [ ] Linked back to this plan's Phase / PR row
```
