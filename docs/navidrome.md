# Navidrome

[Navidrome](https://www.navidrome.org/) is a self-hosted music server and streamer compatible with the Subsonic API.

## URLs

| Environment | URL |
|---|---|
| Staging | `https://navidrome.stage.burntbytes.com` |
| Production | `https://navidrome.burntbytes.com` |

## Configuration

Navidrome runs with minimal configuration via environment variables set in the base deployment:

| Variable | Value | Description |
|---|---|---|
| `ND_DATAFOLDER` | `/data` | Database and cache storage |
| `ND_MUSICFOLDER` | `/music` | Music library root |
| `ND_LOGLEVEL` | `info` | Log verbosity |
| `ND_SCANSCHEDULE` | `1h` | How often to scan for new music |
| `ND_SESSIONTIMEOUT` | `24h` | User session duration |

## Storage

Two PVCs are provisioned per environment:

- **navidrome-data-pvc** (1 Gi) — database, cache, and configuration
- **navidrome-music-pvc** (10 Gi) — music library files

## First-time setup

1. Navigate to the Navidrome URL for your environment.
2. Create an initial admin account on the first-run wizard.
3. Upload music to the `/music` volume (e.g. via `kubectl cp` or by mounting shared storage).

## Subsonic API

Navidrome exposes a Subsonic-compatible API at `/rest/`. Compatible clients include:

- [Symfonium](https://symfonium.app/) (Android)
- [play:Sub](https://apps.apple.com/app/play-sub-subsonic-client/id955329386) (iOS)
- [Sublime Music](https://sublimemusic.app/) (Linux)
- [Sonixd](https://github.com/jeffvli/sonixd) (Desktop)

## Health checks

The deployment includes readiness and liveness probes on `/ping` (port 4533).

## Operation

- Navidrome auto-scans the music folder every hour. To trigger an immediate scan, use the admin UI or the Subsonic API endpoint `/rest/startScan`.
- Logs can be viewed via Grafana/Loki by filtering on `namespace=navidrome-stage` or `namespace=navidrome-prod`.
