# Homelab Documentation

GitOps repo for a single-node Talos Kubernetes cluster (`melodic-muse`). Documentation is organized into a fixed six-folder taxonomy plus historical topic folders that pre-date the canonical layout.

## Canonical taxonomy (six folders)

- [`architecture/`](architecture/README.md) — how the cluster is built today.
- [`design/`](design/README.md) — proposals, RFCs, in-flight or recently shipped designs.
- [`operations/`](operations/README.md) — runbooks, smoke tests, debugging procedures.
- [`plans/`](plans/README.md) — phased migrations, rollout sequencing, frontmatter convention is documented in the folder README.
- [`reference/`](reference/README.md) — component reference, configuration tables.
- [`research/`](research/README.md) — spikes, investigations.

## Historical topic folders (migration in progress)

These predate the canonical taxonomy and remain in place while content is migrated per `docs/plans/2026-02-21-documentation-rewrite-plan.md`:

- [`apps/`](apps/) — per-app runbooks (target: `operations/apps/` or `operations/<app>/`).
- [`guides/`](guides/) — operational how-to guides (target: `operations/`).
- [`incidents/`](incidents/) — incident reports (target: `operations/incidents/`).
- [`infra/`](infra/) — infrastructure component reference (target: `reference/`).

When you write new content, prefer the canonical folder. When you touch existing content materially, move it to the canonical folder in the same PR.

## Quick links

### Architecture

- [Overlays and structure](architecture/overlays-and-structure.md) — base vs staging vs production
- [DNS strategy](architecture/dns-strategy.md) — split-horizon DNS with wildcard records
- [Gateway authentication](architecture/gateway-auth.md) — Global Forward Auth and Envoy filters

### Operations

- [Adding a new app](operations/2026-05-02-adding-an-app.md)
- [Flux debugging — common patterns](operations/2026-05-02-flux-debugging.md)
- [CNPG backup and disaster recovery](operations/2026-05-02-cnpg-backup-recovery.md)
- Existing how-to guides: [Making changes](guides/making-changes.md), [Flux and deployments](guides/flux-and-deployments.md), [Staging workflow](guides/staging-workflow.md), [Synology iSCSI operations](guides/synology-iscsi-operations.md)

### Per-app runbooks

See [`apps/`](apps/) — Adguard, Audiobookshelf, Authelia, Excalidraw, Golinks, Homepage, Immich, Jellyfin, Linkding, Mealie, Memos, Navidrome, Pingo, Snapcast, Vitals.

### Infrastructure component reference

See [`infra/`](infra/) — cert-manager, Cilium, Flux, kernel log shipping, monitoring, Pingo, storage.

### Active plans

See [`plans/README.md`](plans/README.md) for the full plan catalog and the frontmatter convention.

### Past incidents

See [`incidents/`](incidents/) for postmortems.

## Conventions

- Filenames in canonical folders use `<yyyy-mm-dd>-<topic>.md`.
- Frontmatter (`title`, `status`, `created`, `updated`, `updated_by`, `tags`) on new docs in canonical folders. Existing docs in topical folders may be backfilled per the rewrite plan.
- See `AGENTS.md` for the full repo convention.
