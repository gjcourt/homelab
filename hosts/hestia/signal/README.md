# signal — Signal CLI + Bridge on hestia

Runs the Signal messaging stack on TrueNAS hestia as a **TrueNAS Custom App**:

| Service | Image | Role |
|---------|-------|------|
| `signal-cli` | `ghcr.io/asamk/signal-cli` | Signal daemon (JSON-RPC TCP on port 7583) |
| `signal-bridge` | `ghcr.io/gjcourt/signal-bridge` | SSE+RPC bridge for Hermes (HTTP on port 8080) |

**Hermes config**: `SIGNAL_HTTP_URL=http://10.42.2.10:8080`

## Sync rule (canonical source = git)

The YAML in this file is the source of truth. To apply a change:

1. Edit `docker-compose.yml` here and open a PR.
2. After merge, open SCALE UI → **Apps** → `signal` → **Edit**.
3. Paste the updated YAML → **Save**. TrueNAS diff-applies and restarts containers as needed.

Never edit the compose in the SCALE UI without updating git — that creates drift.

## Auth and allowlist

**Bearer token** (`HERMES_AUTH_TOKEN`): set as a masked env var in SCALE UI — do not commit a real value to git. To rotate: **Edit App** → update `HERMES_AUTH_TOKEN` → **Save**.

**Account allowlist** (`HERMES_ALLOWED_ACCOUNTS`): comma-separated E.164 numbers the bridge will poll and accept connections for. This is also the list that drives per-account SSE polling — every number listed gets polled on every tick. Bridge-side enforcement is sufficient for personal use; Hermes has its own `SIGNAL_ALLOW_ALL_USERS` env but you don't need to set it — the bridge gates access before Hermes sees anything.

## Drift check

```bash
ssh truenas_admin@10.42.2.10 \
  'docker inspect ix-signal-signal-bridge-1 \
    --format "{{.Config.Image}}{{range .Config.Env}}\n{{.}}{{end}}"'
```

Compare the `HERMES_ALLOWED_ACCOUNTS` and image tag against `docker-compose.yml` in git.

## Adding a second Signal account (multi-user)

1. Link the device on the running daemon:
   ```bash
   ssh truenas_admin@10.42.2.10
   docker exec ix-signal-signal-cli-1 \
     signal-cli --config /var/lib/signal-cli link --name "Hestia Bridge"
   ```
   This prints a `tsdevice://` URI. Open Signal on the phone → Settings → Linked Devices → scan the QR code.

2. Confirm the account is registered:
   ```bash
   docker exec ix-signal-signal-cli-1 \
     ls /var/lib/signal-cli/data/
   ```
   The phone number (`+1xxx`) should appear.

3. Update `HERMES_ALLOWED_ACCOUNTS` in SCALE UI → **Edit App** → add the number comma-separated → **Save**.

No code changes required — the bridge polls all accounts listed in that env var.

## Migration playbook (initial cutover from signal-cli-rest-api)

### Pre-conditions

- `hosts/hestia/signal/docker-compose.yml` is in git with correct image tags.
- GHA workflow has published `ghcr.io/gjcourt/signal-bridge:<tag>`.
- `ghcr.io/gjcourt/signal-bridge` package visibility is set to **Public** in GitHub → Packages settings (so hestia can pull without `docker login`).
- Bearer token is ready to set in SCALE UI.

### Steps

**1. Snapshot**
```bash
ssh truenas_admin@10.42.2.10
sudo zfs list | grep ix-apps
sudo zfs snapshot tank/.ix-apps@pre-signal-migration-$(date +%Y%m%d)
```

**2. Create operator-owned dataset**
```bash
sudo zfs create tank/apps/signal 2>/dev/null || true
sudo mkdir -p /mnt/tank/apps/signal/data
```

**3. Copy identity data** (safe — old App still running)
```bash
sudo rsync -aHAX \
  /mnt/.ix-apps/app_mounts/signal-cli-rest-api/config/ \
  /mnt/tank/apps/signal/data/
sudo chown -R 568:568 /mnt/tank/apps/signal/data
```

**4. Capture baseline**
```bash
docker exec ix-signal-cli-rest-api-signal-cli-rest-api-1 \
  signal-cli -a +16179397251 listIdentities 2>&1 | wc -l
```
Record the count.

**5. Stop old App**

SCALE UI → **Apps** → `signal-cli-rest-api` → **Stop**.

Confirm: `docker ps --filter name=signal-cli-rest-api` returns nothing.

**6. Create new Custom App**

SCALE UI → **Apps** → **Discover Apps** → **Custom App**:
- **Name**: `signal`
- **Compose YAML**: paste `hosts/hestia/signal/docker-compose.yml` with `<tag>` replaced by the published tag
- **Environment** → add `HERMES_AUTH_TOKEN` as a masked value
- **Install** → wait for status **Running**

**7. Verify identity intact**
```bash
docker exec ix-signal-signal-cli-1 \
  signal-cli -a +16179397251 listIdentities 2>&1 | wc -l
```
Must match step 4.

**8. Verify Hermes endpoints**
```bash
curl -fsS http://10.42.2.10:8080/api/v1/check
curl -fsS -N "http://10.42.2.10:8080/api/v1/events?account=+16179397251" \
  -H "Authorization: Bearer $TOKEN"
```
First returns `{"status":"ok"}`. Second opens SSE stream (Ctrl-C after heartbeat).

**9. Functional smoke**

Send a Signal message TO `+16179397251` from a phone. The SSE stream should emit the message envelope. Reply via:
```bash
curl -fsS -X POST http://10.42.2.10:8080/api/v1/rpc \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"send","params":{"account":"+16179397251","recipient":["+1<test>"],"message":"smoke test"},"id":1}'
```

**10. Decommission**

Once step 9 passes, delete immediately — don't defer:

```bash
# Optional: also delete the copied data from the old App's mount (we have it at /mnt/tank/apps/signal/data)
# sudo rm -rf /mnt/.ix-apps/app_mounts/signal-cli-rest-api/
```

SCALE UI → `signal-cli-rest-api` → **Delete**.

The ZFS snapshot from step 1 is your safety net if anything resurfaces; delete it after 2 weeks once you're confident.

### Rollback (before step 10)

SCALE UI → `signal` → **Stop** → `signal-cli-rest-api` → **Start**.
The rsync in step 3 was a copy; the old data at `/mnt/.ix-apps/app_mounts/signal-cli-rest-api/config/` is untouched.

## Upgrading signal-cli

1. Check https://github.com/AsamK/signal-cli/releases for the latest image digest:
   ```bash
   docker pull ghcr.io/asamk/signal-cli:latest
   docker inspect --format '{{index .RepoDigests 0}}' ghcr.io/asamk/signal-cli:latest
   ```
2. Update the `image:` line in `docker-compose.yml` with the new digest, open a PR.
3. After merge, paste updated YAML into SCALE UI.

## Upgrading signal-bridge

The GHA workflow publishes a new tag on every push to master that touches `images/signal-bridge/`. To upgrade:

1. Note the new tag from the GHA run summary (format: `YYYY-MM-DD`).
2. Update the `signal-bridge` `image:` line in `docker-compose.yml`, open a PR.
3. After merge, paste updated YAML into SCALE UI.
