# Multi-arch Mopidy image for the Snapcast pod's MPD/Subsonic sidecar.
#
# Mopidy bridges Navidrome (Subsonic API) into Snapcast: MPD clients connect on
# 6600, Mopidy fetches the library via mopidy-subidy, and the GStreamer audio
# pipeline writes raw S16LE PCM into a shared FIFO that snapserver reads as the
# `navidrome` source. See docs/plans/2026-03-14-navidrome-snapcast-mopidy.md.
#
# Built on Debian (NOT python:slim): Mopidy needs GStreamer's GObject
# introspection bindings — the Python `gi` module — which cannot be installed
# with pip (per Mopidy's docs). They come from the system python3-gi /
# gir1.2-gst-* packages. Earlier python:3.x-slim builds crashed first on
# `pkg_resources` (no setuptools) and then on `No module named 'gi'`.
FROM debian:bookworm-slim

# System deps:
#   - python3 / python3-venv — interpreter + venv tooling.
#   - python3-gi, python3-gi-cairo, gir1.2-glib-2.0, gir1.2-gstreamer-1.0,
#     gir1.2-gst-plugins-base-1.0 — the GObject-introspection bindings + typelibs
#     Mopidy imports as `gi` / `Gst`. NOT pip-installable.
#   - gstreamer1.0-plugins-{base,good} + tools — audio pipeline elements
#     (audioresample, audioconvert, filesink) + gst-inspect-1.0 for debugging.
#   - libcairo2 — Mopidy's Pykka/dbus stack.
#   - gettext-base — provides `envsubst`, which the snapcast pod's mopidy
#     entrypoint runs to expand ${NAVIDROME_*} env vars from the Subsonic
#     credentials Secret into /tmp/mopidy.conf before invoking mopidy.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-gi \
        python3-gi-cairo \
        gir1.2-glib-2.0 \
        gir1.2-gstreamer-1.0 \
        gir1.2-gst-plugins-base-1.0 \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-tools \
        libcairo2 \
        gettext-base \
    && rm -rf /var/lib/apt/lists/*

# Debian's system python3 is externally managed (PEP 668). Install Mopidy into
# a venv created with --system-site-packages so the pip-installed Mopidy can
# import the apt-provided gi + GStreamer bindings (a plain venv would hide
# them). setuptools<81 provides the legacy pkg_resources Mopidy 3.4.2 imports.
RUN python3 -m venv --system-site-packages /opt/mopidy-venv
ENV PATH="/opt/mopidy-venv/bin:$PATH"
RUN pip install --no-cache-dir \
        "setuptools<81" \
        mopidy==3.4.2 \
        mopidy-mpd==3.3.0 \
        mopidy-subidy==1.1.0 \
        mopidy-local==3.2.1

# Match the snapcast pod's runAsUser: 1000 / fsGroup: 1000. The sidecar only
# writes to /tmp (cache) and the mopidy-state PVC at /mopidy-state, both
# writable by UID 1000 via fsGroup, so no build-time chown is needed.
RUN groupadd --system --gid 1000 mopidy \
    && useradd --system --uid 1000 --gid 1000 --home-dir /mopidy-state --shell /usr/sbin/nologin mopidy

USER 1000:1000

EXPOSE 6600

# The snapcast deployment overrides this with an envsubst wrapper that execs
# `mopidy` (on PATH via the venv); kept for standalone `docker run`.
ENTRYPOINT ["mopidy"]
