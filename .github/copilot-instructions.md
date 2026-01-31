---
# GitHub Copilot instructions (homelab)
---

These are repo-wide rules for Copilot contributions in this GitOps/Kustomize homelab.

## Global rules (always)

- GitOps-first: change files in git, then commit/push; avoid imperative `kubectl apply` except for temporary debugging.
- Use flux reconciliation to validate changes in-cluster.
- Keep changes minimal and scoped to the user request; do not do drive-by refactors.
- Prefer clear, boring solutions over clever ones; optimize for operability.
- Never include secrets in plaintext. Use the repo’s existing secret/SOPS workflow.
- Preserve existing naming conventions, labels, and folder structure (`apps/base`, `apps/staging`, `apps/production`).
- Prefer deterministic changes: pin versions/tags, avoid “latest”, avoid runtime downloads.
- New apps must follow the 3-layer structure: base + staging + production.
- New apps must follow existing patterns unless there’s a strong reason to deviate.
- New apps must include documentation in `docs/` covering usage, configuration, and operation.
- New apps must be added to the auto-generated apps list in `apps/README.md` and added to the homepage app list in the appropriate layer if applicable.

## Workflow conventions

- When changing app behavior, update `apps/base/...` and then overlay-specific patches as needed.
- When debugging, capture evidence (logs/status) but land only the necessary fixes.
- If a change must be validated, prefer: `kubectl kustomize <path>` locally, then reconcile via Flux.
- Don't assume cluster access in automation; instructions should work from the repo.

## Kubernetes / YAML (general)

- Use `.yaml` (not `.yml`) unless the repo already uses `.yml` for a specific file.
- Use 2-space indentation; avoid tabs.
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
- Validate SOPS-encrypted files with `sops --verify` before committing.