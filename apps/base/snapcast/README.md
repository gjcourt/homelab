# snapcast

Snapcast multi-room audio server (`snapserver` + `snapweb`) with a
`go-librespot` sidecar feeding the FIFO for Spotify Connect. Streams audio
to Snapcast clients on the LAN.

## Staging overlay

A staging overlay exists at `apps/staging/snapcast/` and is wired into
`apps/staging/kustomization.yaml`. It deploys the full stack into the
`snapcast-stage` namespace, with a dedicated HTTPRoute and its own PVC for
go-librespot Spotify state. The overlay is exercised by the staging branch
on every reconcile.

To validate changes:
- `kustomize build apps/staging/snapcast` and `kustomize build
  apps/production/snapcast` before pushing.
- After merge, the staging branch rebuild lands the change in the
  `snapcast-stage` namespace; verify the snapserver Pod reaches `Running`
  and the HTTPRoute admits before merging to `master` for production.

Phase 4 / PR 4.2 of the critique remediation plan
(`docs/plans/2026-05-02-critique-remediation.md`) confirmed this app's
staging overlay is sufficient and no further work was required.
