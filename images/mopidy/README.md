# mopidy

Custom multi-arch [Mopidy](https://mopidy.com) image for the Snapcast pod's MPD
sidecar. Bridges Navidrome's Subsonic API into Snapcast so MPD clients
(Symfonium, MALP, ncmpcpp) can browse the Navidrome library and stream PCM
into the `navidrome` Snapcast source.

See `docs/plans/2026-03-14-navidrome-snapcast-mopidy.md` for the full design.

## Architecture

```
MPD client ──TCP:6600──► mopidy sidecar ──Subsonic──► Navidrome
                              │
                              ▼  GStreamer pipeline
                              audioresample ! audioconvert
                              ! audio/x-raw,rate=44100,channels=2,format=S16LE
                              ! filesink location=/audio/navidrome.fifo
                              │
                              ▼
                      snapserver (snapcast pod) ──► HifiBerry clients
```

## What's installed

| Package          | Version  | Purpose                                          |
|------------------|----------|--------------------------------------------------|
| `mopidy`         | `3.4.2`  | Music server core                                |
| `mopidy-mpd`     | `3.3.0`  | MPD protocol frontend (port 6600)                |
| `mopidy-subidy`  | `1.1.0`  | Subsonic backend — talks to Navidrome            |
| `mopidy-local`   | `3.2.1`  | Local-library backend (disabled in snapcast use) |

System packages (Debian bookworm):
- `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`, `gstreamer1.0-tools`
  — audio pipeline elements + `gst-inspect-1.0` for debugging.
- `libcairo2`, `libgstreamer1.0-0`, `libgstreamer-plugins-base1.0-0` — runtime
  libs.
- `gettext-base` — provides `envsubst`, used by the snapcast pod's mopidy
  sidecar entrypoint to expand `${NAVIDROME_*}` env vars from the
  `navidrome-credentials` Secret into the Mopidy config at startup. Not
  installed in upstream Mopidy images; required for the sidecar's entrypoint.

## Runtime contract

| Convention | Value | Notes |
|---|---|---|
| User | `1000:1000` | Matches snapcast pod's `runAsUser: 1000` / `fsGroup: 1000` |
| MPD port | `6600/tcp` | Exposed for clients via the snapcast Service |
| Config path | `/etc/mopidy/mopidy.conf` (mounted from `mopidy-config` ConfigMap) | The pod's entrypoint runs `envsubst` and writes the resolved file to `/tmp/mopidy.conf` before invoking `mopidy --config /tmp/mopidy.conf`. |
| State dir | `/mopidy-state` (PVC `snapcast-mopidy-state`, 1Gi) | Subsonic library cache + Mopidy bookkeeping. |
| Cache dir | `/tmp/mopidy-cache` (emptyDir / tmpfs via container `/tmp`) | |
| Audio sink | `/audio/navidrome.fifo` (shared `audio-pipes` emptyDir) | Created by the `init-navidrome-fifo` initContainer; snapserver reads as a `pipe://` source. |
| Subsonic creds | `NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASSWORD` env vars | Sourced from the SOPS-encrypted `navidrome-credentials` Secret. |

## Image build

Images are published to `ghcr.io/gjcourt/mopidy` by the GHA workflow
`.github/workflows/build-mopidy.yml` on every push to `master` that touches
`images/mopidy/**`, plus `workflow_dispatch` for manual runs. Authentication
uses the auto-provisioned `GITHUB_TOKEN` — no operator-set secrets required.

Tag format: `YYYY-MM-DD` (first build of the day) or `YYYY-MM-DD-N` (reruns of
the same workflow). Multi-arch: `linux/amd64` and `linux/arm64`. The `latest`
tag is also moved to the most recent build.

The snapcast pod's deployment manifest (`apps/base/snapcast/deployment.yaml`)
should be retagged to point at the date tag produced by the first CI build of
this image. Subsequent updates should digest-pin per AGENTS.md image
discipline.

## Local build

```bash
docker build -t gjcourt/mopidy:dev images/mopidy
```

For a multi-arch local build (matches CI):

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t gjcourt/mopidy:dev \
  --load \
  images/mopidy
```

## Smoke test

```bash
# Confirm mopidy + plugins all load
docker run --rm --entrypoint mopidy gjcourt/mopidy:dev --version

# Confirm envsubst is present (sidecar entrypoint requirement)
docker run --rm --entrypoint envsubst gjcourt/mopidy:dev --help
```
