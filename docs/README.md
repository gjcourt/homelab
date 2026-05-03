# Homelab Documentation

GitOps repo for a single-node Talos Kubernetes cluster (`melodic-muse`). Documentation is organized into a fixed six-folder taxonomy.

## Canonical taxonomy

- [`architecture/`](architecture/README.md) — how the cluster is built today.
- [`design/`](design/README.md) — proposals, RFCs, in-flight or recently shipped designs.
- [`operations/`](operations/README.md) — runbooks, smoke tests, debugging procedures. Includes per-app runbooks under [`operations/apps/`](operations/apps/) and incident postmortems under [`operations/incidents/`](operations/incidents/).
- [`plans/`](plans/README.md) — phased migrations, rollout sequencing. Frontmatter and naming conventions are documented in the folder README.
- [`reference/`](reference/README.md) — component reference, configuration tables, infrastructure component documentation (Cilium, cert-manager, Flux, monitoring, storage, etc).
- [`research/`](research/README.md) — spikes, investigations.

The historical `docs/apps/`, `docs/guides/`, `docs/incidents/`, and `docs/infra/` folders were merged into the canonical taxonomy on 2026-05-02. Filenames were preserved as a grandfathered exception; new content under those locations follows each folder's stated naming convention.

## Quick links

### Architecture

- [Overlays and structure](architecture/overlays-and-structure.md) — base vs staging vs production
- [DNS strategy](architecture/dns-strategy.md) — split-horizon DNS with wildcard records
- [Gateway authentication](architecture/gateway-auth.md) — Global Forward Auth and Envoy filters

### Operations

- [Adding a new app](operations/2026-05-02-adding-an-app.md)
- [Flux debugging — common patterns](operations/2026-05-02-flux-debugging.md)
- [CNPG backup and disaster recovery](operations/2026-05-02-cnpg-backup-recovery.md)
- How-to guides (grandfathered names): [Making changes](operations/making-changes.md), [Flux and deployments](operations/flux-and-deployments.md), [Staging workflow](operations/staging-workflow.md), [Synology iSCSI operations](operations/synology-iscsi-operations.md)

### Per-app runbooks

See [`operations/apps/`](operations/apps/) — Adguard, Audiobookshelf, Authelia, Excalidraw, Golinks, Homepage, Immich, Jellyfin, Linkding, Mealie, Memos, Navidrome, Snapcast, Vitals.

### Infrastructure component reference

See [`reference/`](reference/) — cert-manager, Cilium, Flux, kernel log shipping, monitoring, Pingo, storage.

### Active plans

See [`plans/README.md`](plans/README.md) for the full plan catalog and the frontmatter convention.

### Past incidents

See [`operations/incidents/`](operations/incidents/) for postmortems.

## Conventions

- Filenames in canonical folders use `<yyyy-mm-dd>-<topic>.md` for new content. Pre-2026-05-02 content keeps its original name.
- Frontmatter (`status`, `last_modified` minimum; `title`, `created`, `updated`, `updated_by`, `tags` for richer cataloging) on new docs in canonical folders. Per-folder READMEs document allowed `status` values.
- See `AGENTS.md` for the full repo convention.
