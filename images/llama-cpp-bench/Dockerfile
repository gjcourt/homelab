# llama.cpp image extended with llama-bench for kernel-level benchmarking.
#
# Base: official ggml-org/llama.cpp image (digest-pinned, CUDA-enabled, multi-arch).
# Adds: llama-bench built from the same source as the base image to ensure
#       GGUF format compatibility.
#
# Usage:
#   docker run --rm --gpus all -v /models:/models \
#     ghcr.io/gjcourt/llama-cpp-bench:2026-05-04 \
#     llama-bench -m /models/Qwen3.6-35B-A3B-UD-IQ4_NL.gguf --n-gpu-layers 99 -p 256,1024 -n 128 -r 5 -o md

ARG BASE_IMAGE=ghcr.io/ggml-org/llama.cpp@sha256:a8c56356fbfde209910b8098d0a060f4b84997f23b65491df3f2b61fae91dd7b
FROM ${BASE_IMAGE} AS ggml-base

# Build stage: use the base image itself (has CUDA toolkit) to build llama-bench
FROM ${BASE_IMAGE} AS build

# Install build dependencies (git, cmake, build-essential)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git cmake && \
    rm -rf /var/lib/apt/lists/*

# Clone the exact commit used by the base image
# The base image version is 8982 (b42c7fa5b)
ARG LLAMA_CPP_REF=b42c7fa5b
RUN git clone https://github.com/ggml-org/llama.cpp.git /tmp/llama.cpp && \
    cd /tmp/llama.cpp && \
    git checkout "${LLAMA_CPP_REF}"

# Build only llama-bench with CUDA support
WORKDIR /tmp/llama.cpp
RUN LLAMA_CUDA=1 make llama-bench -j$(nproc)

# Final stage: start from the base image and add llama-bench
FROM ${BASE_IMAGE}

# Copy the built llama-bench binary
COPY --from=build /tmp/llama.cpp/bin/llama-bench /usr/local/bin/llama-bench

# Verify the binary loads correctly and is compatible
RUN llama-bench --help | head -1
