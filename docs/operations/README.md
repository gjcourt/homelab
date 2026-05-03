# operations/

Runbooks, smoke tests, and on-call procedures.

**Put here:**
- How to deploy, reconcile, debug, and recover from common failure modes.
- Adding-a-new-app procedure, image-bump procedure, secrets rotation.
- CNPG backup/restore drill instructions.

**Do not put here:**
- Component reference (Cilium config schema, cert-manager issuer recipes) — `reference/`.
- Architecture overview — `architecture/`.

**Subfolders:**
- [`apps/`](apps/) — per-app runbooks, one file per app (e.g. `apps/adguard.md`).
- [`incidents/`](incidents/) — incident postmortems, named `<yyyy-mm-dd>-<topic>.md`.

**Naming convention:** `<yyyy-mm-dd>-<topic>.md` for new top-level operations docs.
Examples: `2026-05-02-flux-debugging.md`, `2026-05-02-adding-an-app.md`, `2026-05-02-cnpg-backup-recovery.md`.

How-to guides migrated from `docs/guides/` on 2026-05-02 keep their original names as a grandfathered exception (e.g. `synology-iscsi-operations.md`, `staging-workflow.md`).

**Allowed `status:` values:** `Stable`, `Superseded`.
