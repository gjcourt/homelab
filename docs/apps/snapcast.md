# Snapcast (Snapserver)

This repo deploys **snapserver** (from the Snapcast project) into Kubernetes, so you can run multi-room audio with Snapcast clients (Raspberry Pis, PCs, etc.) on your network.

## What gets deployed

- Base app: [apps/base/snapcast/](../apps/base/snapcast/)
- Overlays:
  - Staging: [apps/staging/snapcast/](../apps/staging/snapcast/)
  - Production: [apps/production/snapcast/](../apps/production/snapcast/)

## Ports

The `snapcast` Service is a `LoadBalancer` (Cilium LB IPAM) exposing:

- `1704/TCP`: Snapcast audio stream
- `1705/TCP`: Snapcast control / RPC
- `1780/TCP`: Snapweb (web UI)

## Web UI

- Staging: `https://snapcast.stage.burntbytes.com`
- Production: `https://snapcast.burntbytes.com`

## Connecting clients

On each room/device, run `snapclient` pointing to the snapserver LoadBalancer IP (or DNS you map to it):

- `snapclient -h <snapserver-lb-ip>`

Tip: the Android app Snapdroid and Home Assistant’s Snapcast integration can control grouping/volume.

## Feeding audio into the server

The default config uses a named pipe inside the pod:

- FIFO path: `/tmp/snapfifo`
- Sample format: `48000:16:2`

### Quick test (noise)

This should make connected clients play noise:

- `kubectl -n snapcast-prod exec deploy/snapcast -- sh -c 'cat /dev/urandom > /tmp/snapfifo'`

(Use `snapcast-stage` instead of `snapcast-prod` for staging.)

### Stream real audio via `ffmpeg` (ad-hoc)

If you have `ffmpeg` locally, you can pipe raw PCM into the FIFO through `kubectl exec`:

- `ffmpeg -re -i <input> -f s16le -acodec pcm_s16le -ac 2 -ar 48000 - | kubectl -n snapcast-prod exec -i deploy/snapcast -- sh -c 'cat > /tmp/snapfifo'`

This is good for testing, but for “always-on” audio sources you’ll usually want a dedicated player/source (e.g. Music Assistant, Mopidy/MPD, Shairport-sync, librespot) feeding Snapcast.

## Spotify Connect (librespot)

The Snapserver config includes a `spotify` stream which is fed by a **go-librespot sidecar** writing raw PCM into a shared FIFO (so Snapserver itself stays on the stock image).

- The device should appear in Spotify as `Snapcast`.
- Pair it once from the Spotify app (choose `Snapcast` in “Connect to a device”).
- Credentials/state are stored on the PVC `snapcast-spotify-state` mounted at `/config` inside the go-librespot container.

### How it works

- go-librespot outputs `s16le` PCM to `/audio/spotify.fifo`.
- snapserver reads from the same FIFO as a `pipe://` stream source.

### Notes

To avoid GitHub being required at pod startup, the sidecar uses a small `gjcourt/go-librespot:v0.6.2` image (built from [images/go-librespot/](../images/go-librespot/)). Build/push it once with buildx and then deployments won’t depend on GitHub availability.

If Spotify Connect discovery/pairing becomes flaky later (often due to zeroconf/mDNS in Kubernetes networks), consider:

- Switching the readiness probe to check go-librespot’s internal HTTP server port (instead of just “FIFO exists”) so “Ready” more closely reflects “Spotify is reachable/healthy”.
- Using `hostNetwork: true` for the go-librespot container (tradeoffs), or adding explicit network support for mDNS/Avahi in your cluster.

## Changing the stream source

Snapserver streams are configured in the ConfigMap:

- [apps/base/snapcast/configmap.yaml](../apps/base/snapcast/configmap.yaml)

If you’d rather push audio over the network instead of a FIFO, Snapcast also supports TCP-based sources; that’s a good next step if you want an external audio source process to feed the server without `kubectl exec`.
