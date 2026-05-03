# signal-cli

`signal-cli` REST/JSON-RPC bridge that allows the cluster to send and
receive Signal messages on behalf of a registered phone number. Used by
`hermes-bot` and `signal-bridge` to deliver alerts.

## Staging overlay caveat

A thin staging overlay exists at `apps/staging/signal-cli/` and is wired
into `apps/staging/kustomization.yaml`. It deploys the same workload into
the `signal-cli-stage` namespace.

**However**, the staging overlay is structural only — it does not (and
cannot) register a Signal account. A Signal phone number can be linked to
exactly one primary device at a time; binding the production number to a
staging instance would unlink it from production. The staging overlay
therefore deploys an unregistered `signal-cli` daemon that exercises the
manifest and image plumbing but cannot send or receive real messages.

If end-to-end staging validation is ever required, the path forward is
to register a second Signal phone number and provision a separate
`signal-cli-stage` PVC + bridge auth secret bound to it, rather than
trying to share state with production.

To validate workload-level changes (image bump, resource tweak, sidecar
config) without touching production:
- `kustomize build apps/staging/signal-cli` and let CI's staging branch
  rebuild the deployment.
- Confirm the staging Pod reaches `Running` and exposes the bridge port,
  then merge to `master`.

For changes that require an actually-registered Signal client, validate
locally with `signal-cli` on a workstation against the production number
(read-only operations) or against a second test number you control.

Phase 4 / PR 4.2 of the critique remediation plan
(`docs/plans/2026-05-02-critique-remediation.md`) reviewed this app.
