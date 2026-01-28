# Multi-arch Snapcast snapserver image (Alpine)
#
# Uses Alpine's `snapcast-server` package (which includes Snapweb served by snapserver).
# Note: Snapcast 0.34.0 is available in Alpine edge at the time of writing.

FROM alpine:edge

ARG SNAPCAST_PKG_VERSION="0.34.0-r0"

RUN apk add --no-cache \
    ca-certificates \
    "snapcast-server=${SNAPCAST_PKG_VERSION}"

EXPOSE 1704 1705 1780

ENTRYPOINT ["/usr/bin/snapserver", "-c", "/etc/snapserver.conf"]
