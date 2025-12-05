#!/bin/sh
# Decrypts SOPS-encrypted Kubernetes secrets in-place
# Requires SOPS_AGE_KEY_FILE env var set in shell profile
sops --decrypt --in-place "$@"