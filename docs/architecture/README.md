# architecture/

How the cluster is built **today** — the present shape of overlays, networking, identity, and storage.

**Put here:**
- System-level architecture overviews (DNS strategy, gateway authentication, base/staging/production overlay structure).
- Cross-cutting infrastructure decisions that aren't tied to a single component.

**Do not put here:**
- Proposals for future architecture — `design/` (or open-issue convention here today: `plans/` for in-flight decision + execution).
- Phased rollouts — `plans/`.
- Component-level reference (Cilium quirks, cert-manager config) — `reference/` (or the historical `infra/` while migration completes).
- Runbooks — `operations/` (or the historical `apps/` and `guides/` while migration completes).

**Naming convention:** `<topic>.md` for the existing inventory (DNS strategy, gateway-auth, overlays-and-structure); new docs added under this folder going forward should use `<yyyy-mm-dd>-<topic>.md`.

**Allowed `status:` values:** `Stable`, `Superseded`.
