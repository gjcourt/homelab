# snapserver-librespot image

This builds a Snapcast `snapserver` image that includes the `librespot` binary, so Snapserver can use `librespot://` stream sources (Spotify Connect).

## Build + push (multi-arch)

```sh
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t gjcourt/snapserver-librespot:0.34.0 \
  --push \
  images/snapserver-librespot
```

If you publish to a different registry/name, update the image reference in `apps/base/snapcast/deployment.yaml`.
