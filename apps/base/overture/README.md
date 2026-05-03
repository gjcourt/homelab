# overture

Application backend (`overture` + `overture-bridge` sidecar) for
`overture.burntbytes.com`. The base manifests describe the workload, service
account, ServiceMonitor, and ghcr image-pull secret. The production overlay
adds a 3-instance CNPG Postgres cluster, S3-backed Barman backups, and the
HTTPRoute.

## No staging overlay

This app intentionally has no `apps/staging/overture/` overlay.

Reason: overture is a single-instance application whose production overlay
provisions a 3-replica CNPG Postgres cluster, an S3 ObjectStore for WAL
archiving, a scheduled backup, and a separate `tempo-bridge` sidecar with
its own issuer signing key. Standing up a second copy in staging would
duplicate all of that infrastructure (a second Postgres cluster on the same
iSCSI SAN, a second S3 prefix, and a second tempo issuer keypair) for an
app that has no concept of an environment-segmented user base. Production
churn here is low; staging sees no incremental value beyond what
`kustomize build apps/production/overture` already validates at PR time.

To validate changes safely:
- Run `kustomize build apps/production/overture` locally before pushing.
- For schema changes, exercise the migration against a throwaway
  CNPG cluster spun up via `kubectl apply` in a scratch namespace, then
  delete it.
- For tempo-bridge changes, build and run the image locally with
  `docker run` and exercise the `/health` endpoint before bumping the
  pinned digest in `deployment.yaml`.

Phase 4 / PR 4.2 of the critique remediation plan
(`docs/plans/2026-05-02-critique-remediation.md`) explicitly contemplates
this exception.
