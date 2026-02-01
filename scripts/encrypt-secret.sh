#!/usr/bin/env bash
set -euo pipefail

# Encrypts Kubernetes secrets in-place using SOPS.
# Configuration is read from .sops.yaml at repo root.

if [[ $# -lt 1 ]]; then
	echo "Usage: $(basename "$0") <file> [file...]" >&2
	exit 2
fi

for file in "$@"; do
	sops --encrypt --in-place "$file"
done
