# actions-runner — self-hosted GitHub Actions runner

Self-hosted GHA runner that lives on hestia and applies hestia Custom App compose changes via the TrueNAS WebSocket API. See [`docs/plans/2026-05-02-hestia-gha-runner.md`](../../../docs/plans/2026-05-02-hestia-gha-runner.md) for the full design.

| Attribute | Value |
|-----------|-------|
| Image | `myoung34/github-runner` (digest-pinned) |
| Runner name | `hestia` |
| Labels | `hestia`, `truenas` |
| Workflow target | `gjcourt/homelab` — `deploy-hestia.yml` (push to `master`, paths `hosts/hestia/**/docker-compose*.yml`) and `runner-canary.yml` (hourly) |
| Persistence | `/mnt/main/apps/actions-runner/work` |

## One-time bootstrap

The runner is the *only* hestia Custom App that gets pasted into SCALE UI by hand. From its first successful registration onward, every other `hosts/hestia/**` change is applied by the workflow it executes.

1. **Create a GitHub PAT** with permissions for runner registration. Long-lived; the runner mints its own short-lived registration token at startup. **Self-hosted runner registration requires repo-admin permission** — `Contents: read/write` is *not* enough. Pick one:
   - **Classic PAT** (simplest): `repo` scope (full).
   - **Fine-grained PAT** (preferred): scoped to `gjcourt/homelab` only, with permissions `Administration: Read and write` and `Metadata: Read-only`.
   - **GitHub App installation token**: `Administration: write` and `Metadata: read` on the repo.

   If `docker logs` shows `curl: (22) The requested URL returned error: 403` on first start, the token lacks `Administration` (or `repo` for classic). Fix the scopes and update the env var.
2. **Create a TrueNAS API key** — SCALE UI → Settings → API Keys → Add → name it `gha-runner`, copy the value (shown once).
3. **Pre-create the persistence dataset on hestia**:
   ```bash
   ssh truenas_admin@10.42.2.10
   sudo zfs list main/apps 2>/dev/null || sudo zfs create main/apps
   sudo mkdir -p /mnt/main/apps/actions-runner/work
   ```
4. **Add the Custom App in SCALE UI**:
   - Apps → Discover Apps → Custom App
   - Application Name: `gha-runner`
   - Compose YAML: paste the contents of `docker-compose.yml` from this directory
   - Environment → set as masked values:
     - `ACCESS_TOKEN` = the PAT from step 1
     - `TRUENAS_API_KEY` = the API key from step 2
   - Click **Install**
5. **Verify** — within ~30s the runner should appear at `https://github.com/gjcourt/homelab/settings/actions/runners` with status **Idle** and labels `hestia`, `truenas`.

## Runner canary

`.github/workflows/runner-canary.yml` runs hourly on this runner and `curl`s a
healthchecks.io check. **It is the only thing that notices when this runner
dies.**

### Why it exists

On 2026-08-18 a merge to `master` produced a `deploy-hestia.yml` run that sat
**queued indefinitely**: this container was crash-looping on a deprecated runner
version (`RestartCount = 11894`) and GitHub reported it `status=offline`.
Nothing alerted, for a structural reason — GitHub emails on workflow *failure*,
and a run nobody ever picks up never fails. `status=offline` is visible only in
a Settings page nothing polls. An earlier Renovate deploy run had been queued,
unnoticed, since well before that.

A green canary proves more than "the container is running": GitHub could
dispatch, this runner was online and picked the job up, and it executed a step —
the whole path that was broken. It lives entirely off-cluster (GitHub schedules,
hestia executes, healthchecks.io judges), so unlike every Prometheus-based alert
it survives the cluster's monitoring going mute.

### Operator setup — two manual steps

Both are one-time and neither can be committed to this repo.

1. **Create the healthchecks.io check.** Same account as the Alertmanager
   deadman (`gjcourt@gmail.com`).
   - Add Check → Schedule: **Simple** (not Cron)
   - Name: `hestia GHA runner canary`
   - **Period: 1 hour · Grace: 1 hour** → ≤2h worst-case detection
   - Notifications: `gjcourt+critical@gmail.com` — **not** `+alerts`, which is
     the skip-inbox tier documented as unread. Prefer at least one non-email
     channel too.
   - Copy the ping URL (`https://hc-ping.com/<uuid>`).
2. **Add the repo secret.** GitHub → repo **Settings → Secrets and variables →
   Actions → New repository secret**:
   - Name: `HEALTHCHECKS_RUNNER_CANARY_URL`
   - Value: the ping URL from step 1

   A **repo secret, not SOPS** — the workflow is scheduled by GitHub and cannot
   read a cluster secret. Never paste the URL into a shell, a chat window, or
   anything that keeps a transcript: it is a capability, and anyone holding it
   can keep the check green and mask a dead runner.

**Precondition: do this only while the runner is healthy**, or the check is born
red and you start by debugging the monitoring instead of the thing it monitors.
Confirm **Idle** at
<https://github.com/gjcourt/homelab/settings/actions/runners> first.

Verify with `gh workflow run runner-canary.yml` (the workflow carries a
`workflow_dispatch` trigger for exactly this) and confirm the check flips to
**up**. Do this **after** the workflow is on `master`: GitHub evaluates
`schedule` only on the default branch, and `workflow_dispatch` needs the file
present there too, so the canary cannot be exercised from a PR branch.

### Reading the signals

| Signal | Means | First check |
|---|---|---|
| **Check red on healthchecks.io** | No ping arrived — the runner is offline, crash-looping, or runs are stuck queued. **The canary's whole reason for existing.** | `docker logs gha-runner`, then the runners Settings page |
| **Canary workflow run red** | The runner *ran* but the ping failed — no egress from hestia, healthchecks.io down, or the secret is missing/wrong | The run log; the step names the missing-secret case explicitly |
| **Both** | A failed ping is also an absent ping, so a persistent red run drags the check red behind it | Treat as the run-red case |

Silence from both is the only healthy state. There is no failure mode where both
go quiet.

### Cost: one restart per hour

**This runner is ephemeral**, so every canary run re-registers it and the
container restarts. The `myoung34/github-runner` entrypoint *presence-tests*
`EPHEMERAL`, so the `EPHEMERAL: "false"` in `docker-compose.yml` still enables
ephemeral mode — only the variable being set matters, not its value. Do not read
`false` as "off".

Consequence: expect a **~1/hour restart baseline** on this container, plus one
per deploy. Any hestia restart-rate alert must sit above that; the planned
`increase(homelabscope_container_restarts_total[1h]) > 5` arm does. Sustained
counts far above ~2/hour mean a genuine crash loop, which is exactly what the
2026-08-18 incident looked like at 11894.

Two more things that will bite eventually:

- **GitHub disables `schedule` triggers after 60 days of repository
  inactivity.** Renovate keeps this repo active, but a long quiet stretch stops
  the canary — the check going red is what surfaces it.
- **Two checks now share one healthchecks.io account.** If it lapses, both the
  Alertmanager deadman and this canary die silently; nothing in-cluster
  notices, because a dead check cannot alert on itself.

Design: `docs/plans/2026-08-18-hestia-deploy-monitoring-gap.md` section B (lands
separately). Reference: [`docs/reference/monitoring.md`](../../../docs/reference/monitoring.md) §7.
Incident that motivated it:
[`2026-08-13-iscsi-readonly-remount-monitoring-blind.md`](../../../docs/operations/incidents/2026-08-13-iscsi-readonly-remount-monitoring-blind.md)
is the Prometheus-goes-mute precedent this canary is designed to survive.

## Operations

### Token rotation

- **`ACCESS_TOKEN`** (PAT/App): regenerate in GitHub, then SCALE UI → Apps → `gha-runner` → Edit → update env var → Save. SCALE recreates the container; the runner re-registers automatically.
- **`TRUENAS_API_KEY`**: rotate in SCALE UI → Settings → API Keys, then update the env var the same way.

### Drift detection

```bash
ssh truenas_admin@10.42.2.10 'docker inspect ix-gha-runner-runner-1 \
  --format "{{.Config.Image}}{{println}}{{range .Config.Env}}{{println .}}{{end}}"'
```

Diff against `docker-compose.yml` in this directory. Image digest and env var keys (not values) should match.

### Pause the runner

SCALE UI → Apps → `gha-runner` → Stop. Workflows queue at GitHub until restart.

### Re-register from scratch

Delete `/mnt/main/apps/actions-runner/work/.runner` on hestia; restart the app. The runner will mint a fresh registration token via `ACCESS_TOKEN`.

## Image upgrades

Update the digest pin in `docker-compose.yml`. Once the workflow (D2/D3) is live, the merge to `master` will auto-deploy. Until then, paste the new YAML into SCALE UI by hand.

To find the current digest of a tag:

```bash
docker manifest inspect myoung34/github-runner:ubuntu-noble \
  | jq -r '.manifests[] | select(.platform.architecture=="amd64" and .platform.os=="linux") | .digest'
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Runner never appears in GitHub | Bad `ACCESS_TOKEN` | Check container logs: `docker logs ix-gha-runner-runner-1`; regenerate PAT |
| Runner offline after reboot | Persistence missing | Confirm `/mnt/main/apps/actions-runner/work` exists and is writable |
| Workflow hangs | Runner busy/stuck | SCALE UI → Stop → Start; or check `docker logs` for last action |
| Canary check red, no obvious cause | Runner offline / runs queued | Runners Settings page + `docker logs gha-runner`; see [Runner canary](#runner-canary) |
| Canary run red with `HEALTHCHECKS_RUNNER_CANARY_URL is not set` | Repo secret missing | Re-do operator setup step 2 |
| `truenas-update-app.sh` (D3) returns 401 | Bad `TRUENAS_API_KEY` | Rotate the key, update env var |

## Graduation path

The original trigger — **≥2 self-hosted-runner workflows** — is now met: `deploy-hestia.yml` and `runner-canary.yml` both target this runner. Treat that as a counter, not a mandate; the canary is a few seconds of work an hour and adds no maintenance surface. Migrate when this Custom App genuinely becomes a burden, replacing it with [`actions-runner-controller`](https://github.com/actions/actions-runner-controller) running in `melodic-muse`. See [`docs/plans/2026-05-02-hestia-gha-runner.md`](../../../docs/plans/2026-05-02-hestia-gha-runner.md#graduation-path--option-2b-actions-runner-controller-in-talos) for the migration steps.
