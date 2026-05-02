# operations/

Runbooks, smoke tests, and on-call procedures.

**Put here:**
- How to deploy, reconcile, debug, and recover from common failure modes.
- Adding-a-new-app procedure, image-bump procedure, secrets rotation.
- CNPG backup/restore drill instructions.

**Do not put here:**
- Component reference (Cilium config schema, cert-manager issuer recipes) — `reference/`.
- Architecture overview — `architecture/`.
- Per-app feature docs — `apps/` (until that folder migrates here).

**Naming convention:** `<yyyy-mm-dd>-<topic>.md`.
Examples: `2026-05-02-flux-debugging.md`, `2026-05-02-adding-an-app.md`, `2026-05-02-cnpg-backup-recovery.md`.

**Allowed `status:` values:** `Stable`, `Superseded`.

Historical note: `docs/apps/`, `docs/guides/`, and `docs/incidents/` predate this folder; per-doc migration is tracked in `docs/plans/documentation-rewrite-plan.md`. Cross-link rather than duplicate.
