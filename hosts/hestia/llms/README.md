# llms — GPU inference on hestia

LLM inference services for the GPU box on hestia (RTX 4090, 24 GB VRAM).

## Services

| File | Model | Notes |
|------|-------|-------|
| `docker-compose-llama.yml` | llama.cpp inference server | Preferred for 24 GB VRAM; GGUF quants |
| `docker-compose-vllm.yml` | vLLM | Higher throughput; has caused OOM on 24 GB — prefer llama.cpp |

## Deployment

Both files are deployed as separate TrueNAS Custom Apps. Paste the relevant YAML into SCALE UI → Apps → Custom App.

## GPU notes

- 24 GB VRAM (RTX 4090) — prefer GGUF Q4/Q5 quants with llama.cpp over full-precision vLLM
- vLLM has caused repeated OOM crashes on this hardware; use llama.cpp as default
- For models exceeding 24 GB, use CPU offload with llama.cpp (`-ngl` to control GPU layers)
