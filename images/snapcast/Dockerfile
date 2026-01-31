# Multi-arch Snapcast snapserver image (Alpine)
#
# Uses Alpine's `snapcast-server` package (which includes Snapweb served by snapserver).
# Note: Snapcast 0.34.0 is available in Alpine edge at the time of writing.

FROM alpine:edge

ARG SNAPCAST_PKG_VERSION="0.34.0-r0"
ARG SNAPWEB_VERSION="v0.9.3"

RUN apk add --no-cache \
    ca-certificates \
        curl \
        unzip \
        "snapcast-server=${SNAPCAST_PKG_VERSION}"

# Alpine's snapcast-server package may ship a placeholder snapweb.
# Replace it with the full snapweb release assets.
RUN set -eu; \
    tmp="$(mktemp -d)"; \
    curl -fsSL -o "${tmp}/snapweb.zip" "https://github.com/badaix/snapweb/releases/download/${SNAPWEB_VERSION}/snapweb.zip"; \
    rm -rf /usr/share/snapserver/snapweb; \
    mkdir -p /usr/share/snapserver/snapweb; \
    unzip -q "${tmp}/snapweb.zip" -d /usr/share/snapserver/snapweb; \
    rm -rf "${tmp}"; \
    test -f /usr/share/snapserver/snapweb/index.html

EXPOSE 1704 1705 1780

ENTRYPOINT ["/usr/bin/snapserver", "-c", "/etc/snapserver.conf"]
