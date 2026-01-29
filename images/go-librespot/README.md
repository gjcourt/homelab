# go-librespot image

This is a small multi-arch image that contains the `go-librespot` binary.

It avoids GitHub being required at **pod startup** (the download happens at **image build** time instead).

Notes:

- The binary is installed at `/usr/local/bin/go-librespot`.
- `docker build` will work for your current architecture; use `buildx` if you want a multi-arch manifest.
- The image is multi-stage: it downloads/extracts the release tarball in a builder stage, then copies only the binary into a small Alpine runtime image.
- The runtime image includes `gcompat` and `alsa-lib` so the upstream release binary can run on Alpine.

## Build + push (multi-arch)

```sh
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t gjcourt/go-librespot:v0.6.2-1 \
  --push \
  images/go-librespot
```
