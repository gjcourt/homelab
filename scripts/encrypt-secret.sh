#!/bin/sh
# Encrypts Kubernetes secrets in-place using SOPS
# Configuration is read from .sops.yaml at repo root
sops --encrypt --in-place "$@"