# Plans

This directory contains planning documents for features, migrations, and operational improvements in the homelab.

## File Naming

Plan filenames follow the format:

```
YYYY-MM-DD-<slug>.md
```

The date prefix is the **filing date** — when the plan was first written, not the latest edit. It does not change when the plan is updated; that's what `last_modified` in the front-matter is for. The prefix exists so the filesystem listing and the index below sort chronologically without ambiguity.

The slug is kebab-case and describes the work in 2–6 words.

## Front-Matter Convention

Every plan document **must** include YAML front-matter at the top of the file with the following fields:

```yaml
---
status: <value>
last_modified: YYYY-MM-DD
---
```

### `status` values

| Value | Description |
| :--- | :--- |
| `planned` | Work has not yet started. The plan exists for future reference. |
| `in-progress` | Actively being worked on; some steps may be complete. |
| `complete` | All steps are done and the feature/change is live in production. |
| `superseded` | This plan was replaced or made obsolete by a different approach. |
| `abandoned` | Decided not to pursue; kept for historical reference. |

### `last_modified`

Use `YYYY-MM-DD` format. Update this field whenever the document is meaningfully changed.

## Document Index

Sorted by filing date (newest first).

| File | Status | Description |
| :--- | :--- | :--- |
| [2026-05-02-hestia-gha-runner.md](2026-05-02-hestia-gha-runner.md) | `planned` | Self-hosted GHA runner on hestia for auto-deploy of Custom App compose changes |
| [2026-05-02-signal-cli-hermes-rollout.md](2026-05-02-signal-cli-hermes-rollout.md) | `planned` | Signal-cli + signal-bridge stack to feed the Hermes agent |
| [2026-05-02-critique-remediation.md](2026-05-02-critique-remediation.md) | `planned` | IaC hardening — close the 22 findings from the 2026-05-02 critique |
| [2026-03-14-navidrome-snapcast-mopidy.md](2026-03-14-navidrome-snapcast-mopidy.md) | `planned` | Navidrome → Mopidy → Snapcast → HifiBerry whole-house audio |
| [2026-03-08-drawer-inserts.md](2026-03-08-drawer-inserts.md) | `planned` | Cardboard drawer insert design (75×32×12 cm) |
| [2026-03-08-bgp-rollout.md](2026-03-08-bgp-rollout.md) | `planned` | Move LoadBalancer IP advertisement from L2 to BGP with the UCGF |
| [2026-03-08-adguard-dns-rollout.md](2026-03-08-adguard-dns-rollout.md) | `planned` | Roll AdGuard Home as the homelab DNS resolver |
| [2026-02-28-network-migration-192-to-10-42-2.md](2026-02-28-network-migration-192-to-10-42-2.md) | `complete` | Migrate the LAN from 192.168.5.0/24 to 10.42.2.0/24 |
| [2026-02-21-linkding-db-restore-plan.md](2026-02-21-linkding-db-restore-plan.md) | `planned` | Live DR test: destroy and restore Linkding staging DB |
| [2026-02-21-documentation-rewrite-plan.md](2026-02-21-documentation-rewrite-plan.md) | `in-progress` | Rewrite all app and infra documentation |
| [2026-02-21-cnpg-backup-upgrade.md](2026-02-21-cnpg-backup-upgrade.md) | `complete` | Migrate CNPG backups to Barman Cloud Plugin |
| [2026-02-21-cluster-health-dashboards-plan.md](2026-02-21-cluster-health-dashboards-plan.md) | `in-progress` | Grafana cluster health dashboard suite |
| [2026-02-21-app-health-dashboards-plan.md](2026-02-21-app-health-dashboards-plan.md) | `in-progress` | Grafana application health dashboards |
| [2026-02-17-authelia-smtp-notifier.md](2026-02-17-authelia-smtp-notifier.md) | `planned` | Replace filesystem notifier with real SMTP |
| [2026-02-15-adguard-ha.md](2026-02-15-adguard-ha.md) | `planned` | AdGuard Home high-availability with config sync |
| [2026-02-11-authelia-sso-rollout.md](2026-02-11-authelia-sso-rollout.md) | `in-progress` | SSO rollout across all homelab apps |
