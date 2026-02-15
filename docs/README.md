# Documentation

Index of documentation in this repo.

## Architecture & Design

- [Overlays and structure](overlays-and-structure.md) — base/staging/production Kustomize strategy
- [DNS strategy](dns-strategy.md) — split-horizon DNS with AdGuard wildcard rewrites
- [Authelia](authelia.md) — SSO/OIDC setup, secret generation, client config
- [Snapcast](snapcast.md) — multi-room audio deployment

## Runbooks

Operational procedures for day-to-day cluster management.

- [Making changes](runbooks/making-changes.md) — workflow, secrets, adding apps, rollback
- [Flux and deployments](runbooks/flux-and-deployments.md) — entry points, reconcile commands, debugging
- [Synology iSCSI operations](runbooks/synology-iscsi-operations.md) — storage troubleshooting, orphan cleanup, target management

## Incidents

Post-mortems from past outages.

- [2026-02-15: Disabled iSCSI targets](incidents/2026-02-15-iscsi-targets-disabled.md)
- [2026-02-12: Zombie iSCSI targets](incidents/2026-02-12-iscsi-zombie-targets.md)
- [2026-02-08: PV recovery](incidents/2026-02-08-pv-recovery.md)

## Plans

- [Authelia SSO rollout](plans/authelia-sso-rollout.md) — OIDC integration for apps
- [AdGuard HA](TODO-adguard-ha.md) — future high-availability AdGuard setup

## Other

- [Synology iSCSI monitor setup](todo/setup-synology-iscsi-monitor.md)
