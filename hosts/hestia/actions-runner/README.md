# actions-runner — self-hosted GitHub Actions runner

Self-hosted GHA runner that lives on hestia and applies hestia Custom App compose changes via the TrueNAS WebSocket API. See [`docs/plans/2026-05-02-hestia-gha-runner.md`](../../../docs/plans/2026-05-02-hestia-gha-runner.md) for the full design.

| Attribute | Value |
|-----------|-------|
| Image | `myoung34/github-runner` (digest-pinned) |
| Runner name | `hestia` |
| Labels | `hestia`, `truenas` |
| Workflow target | `gjcourt/homelab` (push to `master`, paths `hosts/hestia/**/docker-compose*.yml`) |
| Persistence | `/mnt/main/apps/actions-runner/work` |

## One-time bootstrap

The runner is the *only* hestia Custom App that gets pasted into SCALE UI by hand. From its first successful registration onward, every other `hosts/hestia/**` change is applied by the workflow it executes.

1. **Create a GitHub PAT** with `repo` scope (classic PAT) or a GitHub App installation token with `actions:write` + `metadata:read` on `gjcourt/homelab`. Long-lived; the runner mints its own short-lived registration token at startup.
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
| `truenas-update-app.sh` (D3) returns 401 | Bad `TRUENAS_API_KEY` | Rotate the key, update env var |

## Graduation path

When ≥2 self-hosted-runner workflows exist or this Custom App becomes a maintenance burden, replace with [`actions-runner-controller`](https://github.com/actions/actions-runner-controller) running in `melodic-muse`. See [`docs/plans/2026-05-02-hestia-gha-runner.md`](../../../docs/plans/2026-05-02-hestia-gha-runner.md#graduation-path--option-2b-actions-runner-controller-in-talos) for the migration steps.
