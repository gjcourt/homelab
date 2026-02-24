---
# GitHub Copilot instructions (homelab)
---

Core rules for Copilot contributions in this GitOps/Kustomize homelab.
Topic-specific instructions live in `.github/instructions/`.

## Global rules (always)

- **CRITICAL: NEVER COMMIT DIRECTLY TO `MAIN` or `MASTER`.**
    - All changes, no matter how small, **MUST** be performed on a feature branch.
    - All changes **MUST** be submitted via a Pull Request.
    - **Do not** offer to commit to `main` or `master`. If the user asks, firmly refuse and create a branch instead.
    - **Pre-Commit Check**: Before running `git commit`, ALWAYS run `git branch --show-current` to ensure you are not on `main` or `master`.
- **CRITICAL: NEVER MERGE A PULL REQUEST** without the user's **express permission**.
    - Do not merge PRs automatically after creating them.
    - Do not merge PRs as part of a multi-step workflow unless the user explicitly says to merge.
    - Always ask before merging, even for trivial or urgent fixes.
- GitOps-first: change files in git, then commit/push; avoid imperative `kubectl apply` except for temporary debugging.
- Use flux reconciliation to validate changes in-cluster.
- Keep changes minimal and scoped to the user request; do not do drive-by refactors.
- Prefer clear, boring solutions over clever ones; optimize for operability.
- **Documentation**: Add meaningful docstrings and comments to code/manifests. Explain the "why", not just the "what".
- Never include secrets in plaintext. Use the repo's existing secret/SOPS workflow.
- Preserve existing naming conventions, labels, and folder structure (`apps/base`, `apps/staging`, `apps/production`).
- Prefer deleting unused resources/config rather than commenting them out. Double-check with the user before removing anything significant.
- Harden apps by default: use readiness/liveness probes, resource limits, and restricted Pod Security settings unless there's a specific reason not to.
- Ensure yaml lists are sorted consistently using alpha-numeric sorting (e.g., container ports, volume mounts, kustomize resource lists).
- Ensure Kustomize `resources`, `components`, and `transformers` lists are sorted alphabetically.

## Cluster & environment layout

- The repo operates a single physical cluster (`melodic-muse`) which manages Staging, Production, and Infra layers simultaneously via `clusters/melodic-muse`.
- Do not create separate cluster directories for environments; everything lives in `clusters/melodic-muse`.
- New apps must follow the 3-layer structure: base + staging + production.
- New apps must follow existing patterns unless there's a strong reason to deviate.
- New apps must include documentation in `docs/` covering usage, configuration, and operation.
- New apps must be added to the auto-generated apps list in `apps/README.md` and added to the homepage app list in the appropriate layer if applicable.
- New apps must always be added to the homepage `services.yaml` in both staging and production configmaps. Ask the user which section the app belongs to (e.g., Applications, Infrastructure, Monitoring).
- Infrastructure apps generally do not follow the staging/production split; they are singleton instances shared across environments and use production URLs.
- Infrastructure configuration should be defined in `infra/` or `apps/production/`, avoiding duplication in `apps/staging/`.
- Ensure certificates for infrastructure domains are placed in the `apps/production` overlay.
- When adding ingress/route resources, ensure they conform to the cluster's conventions (e.g., Gateway API + namespace label requirements).

## Git workflow (branches & PRs)

- **MANDATORY: Always start by creating a branch.**
    - Command: `git checkout -b <branch-name>`
    - Never start editing files while on `main`.
- **Branch naming**: Use a descriptive slug (kebab-case).
    - Good: `add-golinks`, `fix-memos-signup`, `update-cilium`.
    - Acceptable prefixes: `staging/` or `production/` (e.g. `staging/update-image`).
- **Staging branch**: The `staging` branch is **automatically rebuilt** by CI (`.github/workflows/staging-deploy.yaml`). It starts from `master` and merges every open PR whose CI checks pass (no failures, no pending). The Flux `apps-staging` Kustomization tracks the `flux-system-staging` GitRepository which points at the `staging` branch. This means **every CI-passing PR is automatically deployed to the staging environment** without manual intervention. Do not manually push to the `staging` branch; CI force-pushes it on every rebuild.
- **Production branch**: The `master` branch is the production branch. Merging a PR to `master` deploys to production via the `flux-system` GitRepository.
- **Commits**:
    - Commit in logical, incremental chunks.
    - Use imperative mood: "Add golinks base manifests" (not "Added" or "Adding").
    - Avoid large monolithic commits if possible.
- **Pull Requests**:
    - **Automate Git Operations**: Do not ask the user to commit or create PRs manually. YOU are responsible for `git add`, `git commit`, `git push`, and `gh pr create`.
    - **Automate PR Creation**: Once the feature branch is pushed, ALWAYS attempt to create the PR immediately using `gh pr create`.
    - **NEVER merge PRs** without explicit user approval. Creating a PR is the final automated step.
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
