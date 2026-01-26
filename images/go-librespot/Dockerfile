# Multi-arch go-librespot image (no runtime GitHub dependency)
# Downloads a pinned release binary at build time.

FROM debian:bookworm-slim

ARG GO_LIBRESPOT_VERSION="v0.6.2"

# Release checksums from https://api.github.com/repos/devgianlu/go-librespot/releases/latest
ARG GO_LIBRESPOT_SHA256_AMD64="5dce7e0902c414ec5d7637c1a529f4fc0dd8f93c3db1020de0625900fca00a15"
ARG GO_LIBRESPOT_SHA256_ARM64="39eda84dad7e28ad0326e00eeea6ff508ce6c8779aefab952296dfb576b8f60c"

ARG TARGETARCH

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl tar \
  && rm -rf /var/lib/apt/lists/*

RUN set -euo pipefail; \
  case "${TARGETARCH}" in \
    amd64) file="go-librespot_linux_x86_64.tar.gz"; sha256="${GO_LIBRESPOT_SHA256_AMD64}" ;; \
    arm64) file="go-librespot_linux_arm64.tar.gz"; sha256="${GO_LIBRESPOT_SHA256_ARM64}" ;; \
    *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
  esac; \
  url="https://github.com/devgianlu/go-librespot/releases/download/${GO_LIBRESPOT_VERSION}/${file}"; \
  curl -fsSL -o /tmp/go-librespot.tgz "${url}"; \
  echo "${sha256}  /tmp/go-librespot.tgz" | sha256sum -c -; \
  tar -xzf /tmp/go-librespot.tgz -C /usr/bin; \
  chmod +x /usr/bin/go-librespot; \
  rm -f /tmp/go-librespot.tgz

USER 65532:65532

ENTRYPOINT ["/usr/bin/go-librespot"]
