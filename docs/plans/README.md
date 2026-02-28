# Plans

This directory contains planning documents for features, migrations, and operational improvements in the homelab.

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

| File | Status | Description |
| :--- | :--- | :--- |
| [adguard-ha.md](adguard-ha.md) | `planned` | AdGuard Home high-availability with config sync |
| [app-health-dashboards-plan.md](app-health-dashboards-plan.md) | `in-progress` | Grafana application health dashboards |
| [authelia-smtp-notifier.md](authelia-smtp-notifier.md) | `planned` | Replace filesystem notifier with real SMTP |
| [authelia-sso-rollout.md](authelia-sso-rollout.md) | `in-progress` | SSO rollout across all homelab apps |
| [cluster-health-dashboards-plan.md](cluster-health-dashboards-plan.md) | `planned` | Grafana cluster health dashboard suite |
| [cnpg-backup-upgrade.md](cnpg-backup-upgrade.md) | `complete` | Migrate CNPG backups to Barman Cloud Plugin |
| [documentation-rewrite-plan.md](documentation-rewrite-plan.md) | `in-progress` | Rewrite all app and infra documentation |
| [george.md](george.md) | `active` | Personal TODO backlog |
| [linkding-db-restore-plan.md](linkding-db-restore-plan.md) | `planned` | Live DR test: destroy and restore Linkding staging DB |
