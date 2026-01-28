# Snapcast (snapserver) image

This folder builds a **Snapcast snapserver** container image using Alpine's `snapcast-server` package (which includes Snapweb).

Why this exists:
- The upstream `badaix/snapcast:*` image reference used previously is not reliably pullable.
- Building our own gives us a stable, multi-arch image and lets us pin versions.

## What’s included
- `snapserver` (includes **Snapweb**, served by snapserver on port `1780`)

## Build (local)

```bash
docker build -t gjcourt/snapcast:0.34.0 --build-arg SNAPCAST_PKG_VERSION=0.34.0-r0 images/snapcast
```

Alpine note: this Dockerfile uses `alpine:edge` because `snapcast-server=0.34.0-r0` is available there.

## Build + push (multi-arch)

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t gjcourt/snapcast:0.34.0 \
  -t gjcourt/snapcast:latest \
  --push \
  --build-arg SNAPCAST_PKG_VERSION=0.34.0-r0 \
  images/snapcast
```

If your cluster can’t pull from Docker Hub, swap the tag/registry (e.g. your private registry).
