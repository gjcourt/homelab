# Multi-arch go-librespot image (no runtime GitHub dependency)
# Downloads a pinned release binary at build time.

FROM alpine:3.20

ARG GO_LIBRESPOT_VERSION="v0.6.2"

# Release checksums from https://api.github.com/repos/devgianlu/go-librespot/releases/latest
ARG GO_LIBRESPOT_SHA256_AMD64="5dce7e0902c414ec5d7637c1a529f4fc0dd8f93c3db1020de0625900fca00a15"
ARG GO_LIBRESPOT_SHA256_ARM64="39eda84dad7e28ad0326e00eeea6ff508ce6c8779aefab952296dfb576b8f60c"

# Provided automatically by buildx, but optional for plain `docker build`
ARG TARGETARCH

RUN apk add --no-cache ca-certificates curl tar

RUN set -eu; \
  arch="${TARGETARCH:-}"; \
  if [ -z "${arch}" ]; then \
    apk_arch="$(apk --print-arch)"; \
    case "${apk_arch}" in \
      x86_64) arch="amd64" ;; \
      aarch64) arch="arm64" ;; \
      *) echo "Unsupported apk arch: ${apk_arch}" >&2; exit 1 ;; \
    esac; \
  fi; \
  case "${arch}" in \
    amd64) file="go-librespot_linux_x86_64.tar.gz"; sha256="${GO_LIBRESPOT_SHA256_AMD64}" ;; \
    arm64) file="go-librespot_linux_arm64.tar.gz"; sha256="${GO_LIBRESPOT_SHA256_ARM64}" ;; \
    *) echo "Unsupported arch: ${arch}" >&2; exit 1 ;; \
  esac; \
  url="https://github.com/devgianlu/go-librespot/releases/download/${GO_LIBRESPOT_VERSION}/${file}"; \
  curl -fsSL -o /tmp/go-librespot.tgz "${url}"; \
  echo "${sha256}  /tmp/go-librespot.tgz" | sha256sum -c -; \
  tar -xzf /tmp/go-librespot.tgz -C /usr/local/bin; \
  chmod +x /usr/local/bin/go-librespot; \
  rm -f /tmp/go-librespot.tgz

ENTRYPOINT ["/usr/local/bin/go-librespot"]
