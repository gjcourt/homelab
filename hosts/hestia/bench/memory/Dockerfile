# Memory bandwidth benchmark image for hestia (EPYC 8004 / Siena).
#
# Builds STREAM (McCalpin) from upstream source and bundles Intel MLC
# (Memory Latency Checker). The MLC tarball must be downloaded manually
# from Intel's site and placed alongside this Dockerfile before building,
# because Intel requires accepting their EULA at download time.
#
# Build:
#   docker build \
#     --build-arg STREAM_SHA256=<sha256-of-stream.c> \
#     -t hestia-memory-bench:1 .
#
# After the first build, record the SHA256 of stream.c here so future builds
# can pin it. STREAM upstream has changed historically.
#
# STREAM_SHA256 reference (fill in after first successful build):
#   # TODO operator: pin SHA after first build, e.g.
#   #   sha256sum stream.c => <hex>

# Pinned by digest for reproducibility — refresh with:
#   docker buildx imagetools inspect debian:bookworm-slim --raw \
#     | python3 -c 'import hashlib,sys; print("sha256:"+hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
FROM debian:bookworm-slim@sha256:96e378d7e6531ac9a15ad505478fcc2e69f371b10f5cdf87857c4b8188404716

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        numactl \
        libnuma-dev \
        util-linux \
        procps \
        wget \
        ca-certificates \
        tar \
        gzip \
    && rm -rf /var/lib/apt/lists/*

# ----------------------------------------------------------------------------
# STREAM (McCalpin)
# ----------------------------------------------------------------------------
# 200M doubles per array = 1.6 GB/array; STREAM allocates 3 arrays => ~4.8 GB,
# which is comfortably larger than Siena's max L3 (4 CCDs * 32 MB = 128 MB)
# and clears the >= 4x-LLC headroom rule from McCalpin's notes.
ARG STREAM_URL=https://www.cs.virginia.edu/stream/FTP/Code/stream.c
ARG STREAM_SHA256=""
ARG STREAM_ARRAY_SIZE=200000000

RUN set -eux; \
    cd /tmp; \
    wget -q -O stream.c "${STREAM_URL}"; \
    if [ -n "${STREAM_SHA256}" ]; then \
        echo "${STREAM_SHA256}  stream.c" | sha256sum -c -; \
    else \
        echo "WARNING: STREAM_SHA256 not pinned; using upstream tip blindly."; \
        echo "Run 'sha256sum stream.c' after build and add it to the Dockerfile."; \
        sha256sum stream.c; \
    fi; \
    gcc -O3 -fopenmp -mcmodel=medium \
        -DSTREAM_ARRAY_SIZE=${STREAM_ARRAY_SIZE} \
        -DNTIMES=20 \
        stream.c -o /usr/local/bin/stream; \
    rm -f stream.c

# ----------------------------------------------------------------------------
# Intel MLC (Memory Latency Checker)
# ----------------------------------------------------------------------------
# Operator must place mlc_v3.12.tgz in the build context. Intel's EULA
# prevents an automated download. Missing-file failure surfaces as a
# COPY error from Docker; see README "Troubleshooting" for the download URL.
#   https://www.intel.com/content/www/us/en/download/736633/intel-memory-latency-checker-intel-mlc.html
COPY mlc_v3.12.tgz /tmp/mlc_v3.12.tgz
RUN set -eux; \
    mkdir -p /opt/mlc; \
    tar -xzf /tmp/mlc_v3.12.tgz -C /opt/mlc; \
    if [ ! -x /opt/mlc/Linux/mlc ]; then \
        chmod +x /opt/mlc/Linux/mlc || true; \
    fi; \
    ln -sf /opt/mlc/Linux/mlc /usr/local/bin/mlc; \
    rm -f /tmp/mlc_v3.12.tgz

# ----------------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------------
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
