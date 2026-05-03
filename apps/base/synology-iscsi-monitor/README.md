# synology-iscsi-monitor

Prometheus exporter (Python + Paramiko) that SSHes into the Synology NAS at
`10.42.2.11` to scrape iSCSI LUN / target / volume metrics, plus a Grafana
dashboard and a PrometheusRule for SAN-side alerts.

## No staging overlay

This app intentionally has no `apps/staging/synology-iscsi-monitor/`
overlay. AGENTS.md notes the same exception for namespace conventions
("Apps without a staging variant ... use the plain name").

Reason: the exporter is a singleton scraper aimed at a single piece of
shared homelab infrastructure (one Synology NAS at one fixed IP, with one
SSH user). Running a second instance in staging would:

- double the SSH/scrape load on the NAS for no additional signal,
- duplicate the firing of every PrometheusRule alert (the alerts describe
  real SAN conditions, not per-environment app behavior), and
- require either reusing the production SSH credential (defeating the
  purpose of an isolated stage) or provisioning a second NAS account just
  to satisfy parity.

To validate changes safely:
- Run `kustomize build apps/production/synology-iscsi-monitor` locally
  before pushing.
- For exporter script changes (`script-cm.yaml`), run the script against
  the NAS from a workstation with the same `melodic-muse-app` SSH user and
  inspect the `/metrics` output before merging.
- For dashboard / PrometheusRule edits, render via
  `kubectl apply --dry-run=server` against the production namespace.

Phase 4 / PR 4.2 of the critique remediation plan
(`docs/plans/2026-05-02-critique-remediation.md`) reviewed this app and
chose Path B (documented exception) over a staging duplicate.
