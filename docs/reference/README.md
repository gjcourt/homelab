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

**Naming convention:** `<yyyy-mm-dd>-<topic>.md` for new content.

Component reference docs migrated from `docs/infra/` on 2026-05-02 keep their original component-name filenames as a grandfathered exception (e.g. `cilium.md`, `cert-manager.md`, `flux.md`, `storage.md`, `monitoring.md`, `kernel-log-shipping.md`, `pingo.md`).

**Allowed `status:` values:** `Stable`, `Superseded`.
