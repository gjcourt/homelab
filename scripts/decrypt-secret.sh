#!/usr/bin/env bash
set -euo pipefail

# Decrypts SOPS-encrypted Kubernetes secrets in-place.
# Requires SOPS_AGE_KEY_FILE env var set in shell profile.

if [[ $# -lt 1 ]]; then
	echo "Usage: $(basename "$0") <file> [file...]" >&2
	exit 2
fi

for file in "$@"; do
	sops --decrypt --in-place "$file"
done
