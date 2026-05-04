# llama.cpp image extended with llama-bench for kernel-level benchmarking.
#
# Base: official ggml-org/llama.cpp image (digest-pinned, CUDA-enabled).
# Adds: llama-bench binary from the Ubuntu x64 release.
#
# Usage:
#   docker run --rm --gpus all -v /models:/models \
#     ghcr.io/gjcourt/llama-cpp-bench:2026-05-04 \
#     llama-bench -m /models/Qwen3.6-35B-A3B-UD-IQ4_NL.gguf --n-gpu-layers 99 -p 256,1024 -n 128 -r 5 -o md

# Stage 1: pull the base image and give it a name we can COPY from
ARG BASE_IMAGE=ghcr.io/ggml-org/llama.cpp@sha256:a8c56356fbfde209910b8098d0a060f4b84997f23b65491df3f2b61fae91dd7b
FROM ${BASE_IMAGE} AS ggml-base

# Stage 2: build on top of the base, adding llama-bench
FROM ${BASE_IMAGE}

ARG LLAMA_CPP_RELEASE=b9012
ARG UBUNTU_TARBALL_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_CPP_RELEASE}/llama-${LLAMA_CPP_RELEASE}-bin-ubuntu-x64.tar.gz"

# Download the Ubuntu release tarball and extract only llama-bench
RUN set -eu; \
    tmp="$(mktemp -d)"; \
    curl -fsSL -o "${tmp}/llama.tar.gz" "${UBUNTU_TARBALL_URL}"; \
    tar xzf "${tmp}/llama.tar.gz" -C "${tmp}"; \
    cp "${tmp}/llama-${LLAMA_CPP_RELEASE}/llama-bench" /usr/local/bin/llama-bench; \
    chmod 755 /usr/local/bin/llama-bench; \
    rm -rf "${tmp}"

# Copy the base image's shared libraries into /usr/local/lib/ so llama-bench
# can find them at runtime. We copy everything from /app/ to be safe.
COPY --from=ggml-base /app/ /usr/local/lib/

# Create symlinks for the versioned library files so the dynamic linker
# can resolve SONAMEs. Only process concrete versioned files (not symlinks).
RUN set -eu; \
    cd /usr/local/lib; \
    for f in libggml-base.so.* libggml.so.* libllama-common.so.* libllama.so.* libmtmd.so.*; do \
        [ -f "$f" ] || continue; \
        [ -L "$f" ] && continue; \
        base="${f%%.*}"; \
        mid="${f%.*}"; \
        ln -sf "$f" "$mid"; \
        ln -sf "$mid" "$base"; \
    done; \
    ldconfig

# Verify the binary loads correctly
RUN llama-bench --help | head -1
