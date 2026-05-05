FROM ghcr.io/hifiberry/extension_snapcast:0.28.0

USER root

# The upstream image builds snapclient from source but omits the runtime
# audio codec libraries from the final stage, so snapclient fails to start
# with "Error loading shared library libasound.so.2" etc.
#
# Additionally, the Python MPRIS wrapper (snapcastmpris.py) hardcodes
# /bin/snapclient but the binary lands at /usr/local/bin/snapclient.
RUN apk update && apk add --no-cache \
    alsa-lib \
    libvorbis \
    libogg \
    flac \
    opus \
    soxr && \
    ln -s /usr/local/bin/snapclient /bin/snapclient

USER snapcast
