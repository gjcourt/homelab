# Multi-arch Mopidy image for the Snapcast pod's MPD/Subsonic sidecar.
#
# Mopidy bridges Navidrome (Subsonic API) into Snapcast: MPD clients connect on
# 6600, Mopidy fetches the library via mopidy-subidy, and the GStreamer audio
# pipeline writes raw S16LE PCM into a shared FIFO that snapserver reads as the
# `navidrome` source. See docs/plans/2026-03-14-navidrome-snapcast-mopidy.md.

FROM python:3.12-slim-bookworm

# Runtime deps:
#   - gstreamer1.0-plugins-{base,good} + tools — the audio pipeline (audioresample,
#     audioconvert, filesink) and `gst-inspect-1.0` for debugging.
#   - libcairo2 — pulled in by Mopidy's Pykka/dbus stack.
#   - libgstreamer1.0-0 / libgstreamer-plugins-base1.0-0 — GStreamer core libs.
#   - gettext-base — provides `envsubst`, which the snapcast pod's mopidy
#     entrypoint runs to expand ${NAVIDROME_*} env vars from the Subsonic
#     credentials Secret into /tmp/mopidy.conf before invoking mopidy. Without
#     this the sidecar fails with `sh: envsubst: not found`.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gettext-base \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-tools \
        libcairo2 \
        libgstreamer-plugins-base1.0-0 \
        libgstreamer1.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        mopidy==3.4.2 \
        mopidy-mpd==3.3.0 \
        mopidy-subidy==1.1.0 \
        mopidy-local==3.2.1

# Match the snapcast pod's `runAsUser: 1000` / `fsGroup: 1000`. The sidecar
# only writes to /tmp (cache) and the mopidy-state PVC mounted at /mopidy-state,
# both writable by UID 1000 via fsGroup, so no extra chown is needed at build.
RUN groupadd --system --gid 1000 mopidy \
    && useradd --system --uid 1000 --gid 1000 --home-dir /mopidy-state --shell /usr/sbin/nologin mopidy

USER 1000:1000

EXPOSE 6600

ENTRYPOINT ["/usr/local/bin/mopidy"]
