# Plan: Update Images & Restrict Tunnel Access

## Objective
Update `biometrics` and `golinks` image tags, restrict Cloudflare Tunnel to only expose auth/links, and document Split-Horizon DNS.

## Steps

### 1. Update Application Images
- **File**: `apps/base/biometrics/deployment.yaml`
- **Change**: Set image to `ghcr.io/gjcourt/biometrics:a83212c`
- **File**: `apps/base/golinks/deployment.yaml`
- **Change**: Set image to `ghcr.io/gjcourt/golinks:sha-2b1d036`

### 2. Configure Cloudflare Tunnel
- **File**: `apps/production/cloudflare-tunnel/configmap.yaml`
- **Action**: Remove internal apps (`adguard`, `audiobooks`, `bio`, `excalidraw`, `go`, `home`, `mealie`, `memos`).
- **Action**: Add `authelia` (`auth.burntbytes.com`).
- **Action**: Keep `linkding` (`links.burntbytes.com`).

### 3. Documentation
- **File**: `docs/dns-strategy.md`
- **Action**: Append section "Manual AdGuard Configuration (Split Horizon)" with `kubectl` instructions and DNS rewrite table.
