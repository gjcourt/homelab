#!/bin/sh
echo 'Step 1'
ssh truenas_admin@10.42.2.10 'docker ps --filter name=llama --format "{{.Image}}\t{{.Status}}"'

echo 'Step 2 - skipped'
# ssh truenas_admin@10.42.2.10 'while true; do nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits; sleep 1; done' | tee /tmp/vram-fresh.txt

echo 'Step 3'
BASE_URL=http://10.42.2.10:8000/v1 python3 /Users/george/src/homelab/scripts/llama-cpp-bench.py --jsonl /tmp/llama-bench-fresh.jsonl

