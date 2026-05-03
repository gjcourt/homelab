# llms — GPU inference on hestia

LLM inference services for the GPU box on hestia (RTX 4090, 24 GB VRAM).

## Services

| File | Custom App | Image | Model |
|------|------------|-------|-------|
| `docker-compose-llama.yml` | `llama` | `ghcr.io/ggml-org/llama.cpp:server-cuda` (digest-pinned) | Qwen3.6-35B-A3B (GGUF IQ4_NL) on `/mnt/main/ai/models/gguf` |
| `docker-compose-vllm.yml` | `vllm` | (see file) | (see file) — keep stopped on 24 GB VRAM; OOM-prone |

## Deployment

Each file is deployed as a separate **TrueNAS Custom App**. Paste the YAML into SCALE UI → Apps → Custom App. The compose YAML in git is the canonical source — never edit the SCALE UI copy without updating git first.

## GPU notes

- 24 GB VRAM (RTX 4090) — prefer GGUF Q4/Q5 quants with llama.cpp over full-precision vLLM
- vLLM has caused repeated OOM crashes on this hardware; use llama.cpp as default
- For models exceeding 24 GB, use CPU offload with llama.cpp (`-ngl` to control GPU layers)

## Updating the model

1. Drop the new GGUF into `/mnt/main/ai/models/gguf/` on hestia
2. Edit `docker-compose-llama.yml` — update the `-m` arg and `--alias`
3. PR + merge
4. SCALE UI → Apps → `llama` → Edit → paste → Save (TrueNAS will recreate the container)
