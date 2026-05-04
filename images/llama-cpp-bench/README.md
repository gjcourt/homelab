# llama-cpp-bench

llama.cpp image extended with `llama-bench` for kernel-level benchmarking.

## Base image

Digest-pinned from `ghcr.io/ggml-org/llama.cpp` (CUDA-enabled).

## What's added

- `llama-bench` binary from the official Ubuntu x64 release tarball
- Required shared libraries (`libllama*.so*`, `libggml*.so*`, `libmtmd*.so*`)
- `ldconfig` run so the dynamic linker finds the libs

## Building

```bash
docker buildx build -t llama-cpp-bench:latest -f images/llama-cpp-bench/Dockerfile .
```

## CI

Built automatically on push to `master` (triggered by changes to `images/llama-cpp-bench/**`).
Also available via `workflow_dispatch` from the GitHub Actions tab.

Tags: `YYYY-MM-DD` (first build) / `YYYY-MM-DD-N` (retries).

## Usage

```bash
docker run --rm --gpus all -v /models:/models \
  ghcr.io/gjcourt/llama-cpp-bench:2026-05-04 \
  llama-bench -m /models/Qwen3.6-35B-A3B-UD-IQ4_NL.gguf \
    --n-gpu-layers 99 -p 256,1024 -n 128 -r 5 -o md
```
