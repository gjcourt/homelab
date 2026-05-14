---
status: Stable
last_modified: 2026-05-14
---

# TrueNAS SCALE `app.update` does not recreate a crashlooping container

TrueNAS SCALE's `app.update` API (used by the SCALE UI "Edit" button and by
[`scripts/truenas-update-app.sh`](../../scripts/truenas-update-app.sh)) stores
a new compose definition for a Custom App and returns `SUCCESS` without
actually recreating the underlying container when that container is stuck in
a restart loop. The old (broken) container keeps restarting against the old
config; the new compose is persisted in app config but never applied.

Discovered during the Phase 10 ipmi-exporter rollout (PRs #669, #670, #671):
after fixing a broken bind-mount in #670, `app.update` returned `SUCCESS` in
`core.get_jobs` but the crashlooping container kept its old config and old
error. Same pattern repeated for #671.

## Symptom

- `core.get_jobs [["id","=",<job_id>]]` shows the `app.update` job as
  `SUCCESS`.
- `docker ps` shows the container still on the old image / old mounts, and
  (if previously crashlooping) still emitting the *old* error.
- The GHA `Deploy hestia Custom Apps` workflow reports success, but the app
  on the box is unchanged.

## Diagnostic

`app.config` reflects the **new** compose stored in the app database, while
`docker inspect` shows the **old** runtime. The mismatch is the giveaway.

```bash
# What TrueNAS thinks the app should look like:
ssh truenas_admin@10.42.2.10 'sudo midclt call app.config ipmi-exporter' | jq .

# What's actually running:
ssh truenas_admin@10.42.2.10 'docker inspect ix-ipmi-exporter-ipmi-exporter-1 \
  --format "{{.Config.Image}}{{range .Config.Env}}\n{{.}}{{end}}\n{{range .HostConfig.Binds}}\n{{.}}{{end}}"'
```

If the two disagree, `app.update` silently no-op'd the container recreate.

## Workaround

Stop and start the app explicitly. Both calls run jobs you must wait on:

```bash
# Via the WebSocket API from a shell on the operator's mac (preferred — same
# auth path the GHA workflow uses):
python3 /tmp/redeploy_ipmi.py                    # one-shot, see below

# Or via midclt on the box:
ssh truenas_admin@10.42.2.10 'sudo midclt call app.stop ipmi-exporter'
ssh truenas_admin@10.42.2.10 'sudo midclt call app.start ipmi-exporter'
```

Or, from the UI: Apps → `<name>` → Stop, wait, → Start.

The stop/start cycle forces TrueNAS to tear down the container and bring it
up against the *current* `app.config` — which now matches the compose that
`app.update` had stored but not applied.

## Sample helper

A one-shot Python helper lives at `/tmp/redeploy_ipmi.py` on the operator's
mac. It authenticates to `wss://10.42.2.10/api/current` with
`TRUENAS_API_KEY`, calls `app.stop`, waits on the returned job id via
`core.get_jobs`, then `app.start`, and waits again. Swap the hard-coded app
name (`ipmi-exporter`) for the affected app before re-running. The
wait-for-job loop mirrors
[`scripts/truenas-update-app.sh`](../../scripts/truenas-update-app.sh).

## Root cause (hypothesis)

Unverified — most likely the TrueNAS app reconciler treats a crashlooping
container as "not yet healthy / not yet running" and skips the container
recreate step that normally follows a compose write, on the assumption the
next restart will pick up the new config (which it won't, because Docker
restarts use the existing container's stored config, not the app's stored
compose). Worth filing upstream at
<https://ixsystems.atlassian.net/> if it reproduces on a clean app.

## When this matters

- **First-deploy paths** where the initial compose has a typo / bad mount —
  the container crashloops, the fix-up PR's `app.update` no-ops, and the
  operator has to stop/start by hand.
- **Any subsequent compose change that lands while the container is
  already crashlooping** for an unrelated reason.

The standard `deploy-hestia.yml` GHA workflow calls
[`scripts/truenas-update-app.sh`](../../scripts/truenas-update-app.sh), which
calls `app.update` — so this affects automated deploys too, not just manual
UI edits. A future hardening would be to have the script detect the
"`app.update` SUCCESS but container still crashlooping after N seconds" case
and follow up with an explicit `app.stop` / `app.start` (or use
`app.redeploy` if a future TrueNAS release exposes it cleanly).
