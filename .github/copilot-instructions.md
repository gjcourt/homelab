---
# GitHub Copilot instructions (homelab)
---

These are repo-wide rules for Copilot contributions in this GitOps/Kustomize homelab.

## Global rules (always)

- **CRITICAL: NEVER COMMIT DIRECTLY TO `MAIN`.**
    - All changes, no matter how small, **MUST** be performed on a feature branch.
    - All changes **MUST** be submitted via a Pull Request.
    - **Do not** offer to commit to `main` or `master`. If the user asks, firmly refuse and create a branch instead.
- GitOps-first: change files in git, then commit/push; avoid imperative `kubectl apply` except for temporary debugging.
- Use flux reconciliation to validate changes in-cluster.
- Keep changes minimal and scoped to the user request; do not do drive-by refactors.
- Prefer clear, boring solutions over clever ones; optimize for operability.
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
- **Commits**:
    - Commit in logical, incremental chunks.
    - Use imperative mood: "Add golinks base manifests" (not "Added" or "Adding").
    - Avoid large monolithic commits if possible.
- **Pull Requests**:
    - When changes are ready, you must signal to open a PR against `main`.
    - Provide a short description of what changed and why.
    - **Do not merge your own PRs** unless explicitly instructed to "merge" or "ship it".
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

## Scripts (shell)

- Prefer small, composable scripts; document usage at top of file.
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