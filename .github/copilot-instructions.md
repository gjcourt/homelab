---
# GitHub Copilot instructions (homelab)
---

These are repo-wide rules for Copilot contributions in this GitOps/Kustomize homelab.

## Global rules (always)

- **CRITICAL: NEVER COMMIT DIRECTLY TO `MAIN` or `MASTER`.**
    - All changes, no matter how small, **MUST** be performed on a feature branch.
    - All changes **MUST** be submitted via a Pull Request.
    - **Do not** offer to commit to `main` or `master`. If the user asks, firmly refuse and create a branch instead.
    - **Pre-Commit Check**: Before running `git commit`, ALWAYS run `git branch --show-current` to ensure you are not on `main` or `master`.
- GitOps-first: change files in git, then commit/push; avoid imperative `kubectl apply` except for temporary debugging.
- Use flux reconciliation to validate changes in-cluster.
- Keep changes minimal and scoped to the user request; do not do drive-by refactors.
- Prefer clear, boring solutions over clever ones; optimize for operability.
- **Documentation**: Add meaningful docstrings and comments to code/manifests. Explain the "why", not just the "what".
- Never include secrets in plaintext. Use the repo’s existing secret/SOPS workflow.
- Preserve existing naming conventions, labels, and folder structure (`apps/base`, `apps/staging`, `apps/production`).
- The repo operates a single physical cluster (`melodic-muse`) which manages Staging, Production, and Infra layers simultaneously via `clusters/melodic-muse`.
- Do not create separate cluster directories for environments; everything lives in `clusters/melodic-muse`.
- New apps must follow the 3-layer structure: base + staging + production.
- New apps must follow existing patterns unless there’s a strong reason to deviate.
- New apps must include documentation in `docs/` covering usage, configuration, and operation.
- New apps must be added to the auto-generated apps list in `apps/README.md` and added to the homepage app list in the appropriate layer if applicable.
- New apps must always be added to the homepage `services.yaml` in both staging and production configmaps. Ask the user which section the app belongs to (e.g., Applications, Infrastructure, Monitoring).
- Infrastructure apps generally do not follow the staging/production split; they are singleton instances shared across environments and use production URLs.
- Infrastructure configuration should be defined in `infra/` or `apps/production/`, avoiding duplication in `apps/staging/`.
- Ensure certificates for infrastructure domains are placed in the `apps/production` overlay.
- Prefer deleting unused resources/config rather than commenting them out. Double-check with the user before removing anything significant.
- Harden apps by default: use readiness/liveness probes, resource limits, and restricted Pod Security settings unless there’s a specific reason not to.
- When adding ingress/route resources, ensure they conform to the cluster’s conventions (e.g., Gateway API + namespace label requirements).
- Ensure yaml lists are sorted consistently using alpha-numeric sorting (e.g., container ports, volume mounts).

## Git workflow (branches & PRs)

- **MANDATORY: Always start by creating a branch.**
    - Command: `git checkout -b <branch-name>`
    - Never start editing files while on `main`.
- **Branch naming**: Use a descriptive slug (kebab-case).
    - Good: `add-golinks`, `fix-memos-signup`, `update-cilium`.
    - Acceptable prefixes: `staging/` or `production/` (e.g. `staging/update-image`).
- **Staging branch**: The `staging` branch is **automatically rebuilt** by CI (`.github/workflows/staging-deploy.yaml`). It starts from `master` and merges every open PR whose CI checks pass (no failures, no pending). Rebuilds are event-driven (`check_suite`, `pull_request` events) with no cron; a diff guard skips the push when nothing changed. The Flux `apps-staging` Kustomization tracks the `flux-system-staging` GitRepository which points at the `staging` branch. This means **every CI-passing PR is automatically deployed to the staging environment** without manual intervention. Do not manually push to the `staging` branch; CI force-pushes it on every rebuild.
- **Production branch**: The `master` branch is the production branch. Merging a PR to `master` deploys to production via the `flux-system` GitRepository.
- **Commits**:
    - Commit in logical, incremental chunks.
    - Use imperative mood: "Add golinks base manifests" (not "Added" or "Adding").
    - Avoid large monolithic commits if possible.
- **Pull Requests**:
    - **Automate Git Operations**: Do not ask the user to commit or create PRs manually. YOU are responsible for `git add`, `git commit`, `git push`, and `gh pr create`.
    - **Automate PR Creation**: Once the feature branch is pushed, ALWAYS attempt to create the PR immediately using `gh pr create`.
    - **Quality Check**: Run `make lint` locally and ensure it passes before creating a PR. If linting fails, run `make format` to fix common issues automatically.
    - **Reconciliation Check**: Before submitting a PR, ALWAYS verify that the manifests can be built and applied. Run `kubectl kustomize apps/production > /tmp/prod.yaml && kubectl apply -f /tmp/prod.yaml --dry-run=server` (or similar for staging/infra) to catch validation errors early.
    - **PR Descriptions**: All PRs MUST have a description that follows the structure of `.github/pull_request_template.md` (What changed, Why, Notes). Do not simply use `--fill`. You must explicitly generate the body content to answer these questions.
    - **Secrets**: If encryption keys are unavailable, commit placeholder secret files (e.g., `value: "PLACEHOLDER"`) and instruct the user in the PR description to encrypt them before merging.
- **Cleanup**: After merge, locally delete the feature branch.
- **Multi-repo**: If a task spans multiple repos, create a branch in each and cross-reference them.

## Workflow conventions

- When changing app behavior, update `apps/base/...` and then overlay-specific patches as needed.
- When debugging, capture evidence (logs/status) but land only the necessary fixes.
- If a change must be validated, prefer: `kubectl kustomize <path>` locally, then reconcile via Flux.
- Don't assume cluster access in automation; instructions should work from the repo.

## Kubernetes / YAML (general)

- Use `.yaml` (not `.yml`) unless the repo already uses `.yml` for a specific file.
- Enforce 2-space indentation for all YAML in this repo (exactly 2 spaces per level). Never use tabs and never use 4-space indentation.
- Keep manifests readable: stable key ordering, consistent quoting, avoid line-wrapping that hurts diffs.
- Prefer explicit port names, resource requests/limits, and readiness/liveness probes where appropriate.
- Security: follow the repo’s Pod Security stance (restricted-style) unless explicitly asked to relax it.

## Kustomize (base + overlays)

- Base should be reusable; overlays should be small patches.
- Use patches to rename namespaces and set env-specific labels/annotations.
- Avoid duplicating full resources in overlays unless necessary.
- When adding ingress/routes, ensure they match the cluster’s conventions (e.g., Gateway API + namespace label requirements).

## Docker / images

- Prefer multi-stage builds when it reduces runtime size or removes build tooling.
- Prefer pinned versions (base image tags, package versions, downloaded artifacts).
- Avoid runtime GitHub downloads; download during image build instead.
- Prefer Alpine when feasible, but choose Debian/Ubuntu when it avoids compatibility pain (e.g., glibc-dependent binaries).
- Build multi-arch images when required by the cluster (amd64/arm64).

## Scripts (shell/python)

- Prefer small, composable scripts.
- **Documentation Mandatory**: specific usage instructions, environment variables, and purpose MUST be documented at the top of every script file (Shell or Python).
- Be safe by default: `set -euo pipefail` in bash-compatible scripts.
- Avoid writing secrets to stdout; avoid leaking secret file contents.
- Place scripts in an appropriate folder (e.g., `scripts/`); avoid scattering them.

## Docs (Markdown)

- Be concise and practical; assume the reader is operating a homelab.
- Use consistent headings, bullet lists, and fenced code blocks with language tags.
- Link to other repo docs when relevant (especially under `docs/`).

## Secrets / SOPS

- Never create plaintext secrets.
- Follow existing patterns for encrypted secret files and related documentation.
- When adding new required secret values, document how to generate/rotate them.
- Validate SOPS-encrypted files with `sops filestatus <file>` before committing.
- For placeholder values in docs, use clearly fake values (e.g., `your-secret-value-here`).
- Include TODO notes in docs for any manual secret setup steps needed.
## Naming Conventions
- **Kubernetes Resources**:
    - **Storage**: Always name PersistentVolumeClaim and StorageClass files as `storage.yaml`. Do not use `pvc.yaml`, `pv.yaml`, or similar.
    - **Config**: Use `configmap.yaml` and `secret.yaml`.
    - **Workloads**: Use `deployment.yaml`, `statefulset.yaml`, `daemonset.yaml`.
    - **Network**: Use `service.yaml`, `ingress.yaml`, `httproute.yaml`.
    - **RBAC**: Use `rbac.yaml` or `serviceaccount.yaml`.

## Synology iSCSI / Volume Debugging

This cluster uses a Synology NAS (192.168.5.8) as the iSCSI storage backend via the `synology-csi` driver. Volume issues are among the most common failure modes. Use this section to diagnose and fix them efficiently.

### Architecture
- **LUN**: Block device on the NAS at `/volume1/@iSCSI/LUN/BLUN/<uuid>/`. Named `k8s-csi-pvc-<UUID>` by the CSI driver.
- **Target**: iSCSI endpoint identified by IQN `iqn.2000-01.com.synology:alcatraz.pvc-<UUID>`. Each LUN maps to exactly one target.
- **PV/PVC**: Kubernetes abstractions referencing the target IQN. CSI driver translates PVC requests into LUN+Target creation on the NAS.
- **Max targets**: 128 (hard Synology limit). Monitor proactively.

### Common failure modes and diagnosis

1. **Pods stuck in `ContainerCreating` / `FailedMount`**:
    - Check `kubectl describe pod <pod>` for mount errors.
    - Check CSI plugin logs: `kubectl logs -n synology-csi -l app=synology-csi-node -c csi-plugin --tail=50`.
    - Common causes: disabled targets, unmapped LUNs, stale iSCSI sessions.

2. **Read-only filesystem (btrfs remounted ro)**:
    - Symptom: apps crash with write errors, `dmesg` shows `BTRFS warning: btrfs_check_rw_degradable`.
    - Root cause: iSCSI I/O errors cause btrfs to remount read-only.
    - **Fix**: Scale deployment to 0 (forces unmount), wait 30s, scale back to 1 (forces fresh mount). If the underlying iSCSI path is broken, fix that first.
    - Check iSCSI sessions from the CSI node plugin: `kubectl exec -n synology-csi <csi-node-pod> -c csi-plugin -- iscsiadm -m session -P3 | grep -A5 "Target:"`.

3. **LUN mismatch (wrong lun-N)**:
    - Symptom: mount fails or mounts wrong device.
    - The CSI driver expects `lun-1`. After NAS operations, LUNs may be remapped to `lun-2` or higher.
    - **Fix**: Rescan iSCSI session to restore correct lun mapping: `iscsiadm -m session -r <session-id> --rescan` (run from within the CSI node plugin container).

4. **Released PVs (CNPG clusters)**:
    - Symptom: CNPG pods Pending, PVCs Pending, old PVs in `Released` state.
    - **Fix**: Remove stale UID from PV claimRef: `kubectl patch pv <pv-name> --type=json -p '[{"op":"remove","path":"/spec/claimRef/uid"},{"op":"remove","path":"/spec/claimRef/resourceVersion"}]'`.
    - Prefer the oldest PV (by `.metadata.creationTimestamp`) as it likely has real data.

5. **Disabled targets on NAS**:
    - The Synology `synoiscsiwebapi` CLI silently sets `enabled=no` on targets during LUN mapping operations.
    - Always run `python3 scripts/synology/enable_targets.py` after any NAS LUN operation.
    - Audit: `python3 scripts/synology/audit_luns.py`.

### Synology NAS access
- **DSM API**: `https://192.168.5.8:5001` (HTTPS, self-signed cert — use `-k` with curl).
- **SSH**: Port 22, same credentials.
- **Credentials**: Stored in `client-info-secret` in the `synology-csi` namespace. Retrieve with: `kubectl get secret -n synology-csi client-info-secret -o jsonpath='{.data.client-info\.yaml}' | base64 -d`.
- **Scripts**: All operational scripts are in `scripts/synology/`. See `scripts/synology/README.md`.

### Diagnostic workflow (run in order)
```bash
# 1. Find unhealthy pods
kubectl get pods -A --field-selector 'status.phase!=Running,status.phase!=Succeeded'

# 2. Check for iSCSI-related mount errors
kubectl describe pod <pod> | grep -A5 "Events:"

# 3. Audit NAS LUN/target state
python3 scripts/synology/audit_luns.py

# 4. Check for disabled targets
python3 scripts/synology/enable_targets.py

# 5. Check iSCSI sessions from the node
kubectl exec -n synology-csi <csi-node-pod> -c csi-plugin -- iscsiadm -m session

# 6. Check for Released PVs
kubectl get pv | grep Released
```

### Documentation references
- Operations runbook: `docs/guides/synology-iscsi-operations.md`
- Cleanup concepts: `docs/guides/synology-iscsi-cleanup.md`
- Incident postmortems: `docs/incidents/`
