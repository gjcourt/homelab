# go-librespot image

This is a small multi-arch image that contains the `go-librespot` binary.

It avoids GitHub being required at **pod startup** (the download happens at **image build** time instead).

## Build + push (multi-arch)

```sh
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t gjcourt/go-librespot:v0.6.2 \
  --push \
  images/go-librespot
```
