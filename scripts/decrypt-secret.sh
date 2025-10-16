#!/bin/sh
export SOPS_AGE_KEY_FILE=/Users/george/.sops/homelab-staging.agekey
sops --decrypt \
    --in-place \
    $@