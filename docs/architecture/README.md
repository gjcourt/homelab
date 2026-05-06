# architecture/

How the cluster is built **today** — the present shape of overlays, networking, identity, and storage.

**Put here:**
- System-level architecture overviews (DNS strategy, gateway authentication, base/staging/production overlay structure).
- Cross-cutting infrastructure decisions that aren't tied to a single component.
- Multi-doc topical bundles as subfolders (e.g. `networking/` for the LAN + cluster network architecture).

**Do not put here:**
- Proposals for future architecture — `design/` (or open-issue convention here today: `plans/` for in-flight decision + execution).
- Phased rollouts — `plans/`.
- Component-level reference (Cilium quirks, cert-manager config) — `reference/` (or the historical `infra/` while migration completes).
- Runbooks — `operations/` (or the historical `apps/` and `guides/` while migration completes).

**Index of topical bundles:**

- [`networking/`](networking/README.md) — physical topology, VLANs/IP map, L2+BGP load balancing, traffic flows, glossary.

**Naming convention:** `<topic>.md` for the existing inventory (DNS strategy, gateway-auth, overlays-and-structure); new docs added under this folder going forward should use `<yyyy-mm-dd>-<topic>.md`. Multi-doc bundles use a subfolder named `<topic>/` containing a `README.md` entry point and topic-specific siblings.

**Allowed `status:` values:** `Stable`, `Superseded`.
