# Documentation

Index of docs in this repo, organized by category.

## Start here

- [Repository README](../README.md) — repo structure and quick start
- [Apps overview](../apps/README.md) — auto-generated list of deployed apps
- [Infra overview](../infra/README.md) — cluster-level controllers and configs

## Architecture

Design decisions and system context.

- [Overlays and structure](architecture/overlays-and-structure.md) — base vs staging vs production
- [DNS strategy](architecture/dns-strategy.md) — split-horizon DNS with wildcard records

## Guides

Operational how-to guides for day-to-day work.

- [Making changes](guides/making-changes.md) — workflow, secrets, rollback
- [Flux and deployments](guides/flux-and-deployments.md) — how Flux applies changes, reconcile and debug commands
- [Staging workflow](guides/staging-workflow.md) — how PRs auto-deploy to staging via CI and Flux
- [Synology iSCSI operations](guides/synology-iscsi-operations.md) — common storage scenarios and runbook
- [Synology iSCSI cleanup](guides/synology-iscsi-cleanup.md) — orphan LUN/target concepts and cleanup process

## Apps

Per-app usage, configuration, and operation docs.

- [Authelia (SSO / OIDC)](apps/authelia.md)
- [Snapcast (multi-room audio)](apps/snapcast.md)

## Incidents

Postmortems for past outages.

- [2026-02-15: iSCSI targets disabled](incidents/2026-02-15-iscsi-targets-disabled.md)
- [2026-02-12: iSCSI zombie targets](incidents/2026-02-12-iscsi-zombie-targets.md)
- [2026-02-08: PV recovery](incidents/2026-02-08-pv-recovery.md)

## Plans

Active plans and TODOs.

- [Authelia SSO rollout](plans/authelia-sso-rollout.md)
- [Authelia SMTP notifier](plans/authelia-smtp-notifier.md)
- [AdGuard HA](plans/adguard-ha.md)
- [Synology iSCSI monitor setup](plans/setup-synology-iscsi-monitor.md)
