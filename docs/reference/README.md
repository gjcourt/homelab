# reference/

Information you look things up in — component reference, network specs, configuration tables.

**Put here:**
- Component-level reference (Cilium config quirks, cert-manager issuer recipes, monitoring alert catalog).
- IP allocation tables, firewall rules, DNS zone snapshots.
- Hardware reference (RPi GPIO pinouts, Synology SAS topology).

**Do not put here:**
- Runbooks — `operations/`.
- Architecture overview — `architecture/`.
- Spike output — `research/`.

**Naming convention:** `<yyyy-mm-dd>-<topic>.md`.

**Allowed `status:` values:** `Stable`, `Superseded`.

Historical note: `docs/infra/` predates this folder and contains component reference for Cilium, cert-manager, Flux, monitoring, storage, etc. Per-doc migration is tracked in `docs/plans/documentation-rewrite-plan.md`.
