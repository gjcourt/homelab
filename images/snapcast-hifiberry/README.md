# snapcast-hifiberry image

Patched version of `ghcr.io/hifiberry/extension_snapcast` for use on HifiBerry OS devices (kitchen at `10.42.2.38`, living-room at `10.42.2.39`).

## Why this exists

The upstream HifiBerry extension image (`ghcr.io/hifiberry/extension_snapcast:0.28.0`) has two bugs that prevent snapclient from running:

1. **Missing runtime audio libraries** — the multi-stage Dockerfile compiles snapclient with `alsa-lib`, `libvorbis`, `libogg`, `flac`, `opus`, and `soxr` in the builder stage but does not install their runtime counterparts in the final stage. snapclient fails at startup with `Error loading shared library libasound.so.2` (and similar for the others).

2. **Wrong binary path** — `snapcastmpris.py` (the Python MPRIS wrapper that manages snapclient) hardcodes `/bin/snapclient`, but the binary is installed at `/usr/local/bin/snapclient`. The subprocess call silently fails and snapclient never starts.

## What this image does

Layers on top of the upstream image (same `snapclient` binary, same Python wrapper, same entrypoint) and:
- Installs the six missing runtime codec/audio libraries via `apk`
- Creates a symlink `/bin/snapclient → /usr/local/bin/snapclient`

## Upstream reference

`ghcr.io/hifiberry/extension_snapcast:0.28.0`  
GitHub: https://github.com/hifiberry/extension_snapcast

## Build (local, arm64)

```bash
docker buildx build \
  --platform linux/arm64 \
  -t ghcr.io/gjcourt/snapcast-hifiberry:latest \
  --load \
  images/snapcast-hifiberry
```

## Deploying to HifiBerry devices

The docker-compose on each device (`/data/extensions/snapcast/docker-compose.yaml`) references this image. After a new image is published, update the tag and restart:

```bash
ssh root@10.42.2.38 "
  docker pull ghcr.io/gjcourt/snapcast-hifiberry:<tag>
  sed -i 's|image: ghcr.io/gjcourt/snapcast-hifiberry:.*|image: ghcr.io/gjcourt/snapcast-hifiberry:<tag>|' /data/extensions/snapcast/docker-compose.yaml
  docker-compose -f /data/extensions/snapcast/docker-compose.yaml up -d
"
```

Repeat for `10.42.2.39` (living-room).

## Upgrading the upstream image

When upgrading from `0.28.0` to a newer HifiBerry extension tag:
1. Update the `FROM` line in the Dockerfile
2. Verify the two bugs are still present (check with `docker run --rm <new-tag> ldd /usr/local/bin/snapclient` for missing libs, and `docker run --rm <new-tag> ls /bin/snapclient` for the symlink)
3. If upstream has fixed both issues, this image can be retired and the `docker-compose.yaml` on each device reverted to point at the upstream image directly
