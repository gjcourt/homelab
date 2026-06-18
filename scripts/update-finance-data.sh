#!/usr/bin/env bash
# Rebuild + re-encrypt the finance-dashboard data secret from the local source
# YAMLs (positions / cashflow / str / runway / candidates), so a data change is
# just: edit a YAML → run this → commit/PR → rollout restart. No image rebuild.
#
# Run from the homelab repo root:  scripts/update-finance-data.sh
set -euo pipefail

SRC="${FINANCE_SRC:-$HOME/src/utility/portfolio}"
SECRET="apps/base/finance-dashboard/secret-finance-data.yaml"
PY="${PYTHON:-$SRC/.venv/bin/python}"

[ -f "$SECRET" ] || { echo "Run from the homelab repo root (or worktree)."; exit 1; }

"$PY" - "$SECRET" "$SRC" <<'PYEOF'
import sys, yaml
out, src = sys.argv[1], sys.argv[2]
files = ['positions.yaml', 'cashflow.yaml', 'str.yaml', 'runway.yaml', 'candidates.yaml']
sd = {f: open(f'{src}/{f}').read() for f in files}
secret = {'apiVersion': 'v1', 'kind': 'Secret',
          'metadata': {'name': 'finance-dashboard-data', 'namespace': 'finance-dashboard',
                       'labels': {'app': 'finance-dashboard'}},
          'type': 'Opaque', 'stringData': sd}
open(out, 'w').write(yaml.safe_dump(secret, sort_keys=False, default_flow_style=False,
                                    width=100000, allow_unicode=True))
PYEOF

sops -e -i "$SECRET"
grep -q 'positions.yaml: ENC\[' "$SECRET" || { echo "ERROR: secret not encrypted — aborting"; exit 1; }

echo "✓ re-encrypted $SECRET from $SRC"
echo "Next:"
echo "  git add $SECRET && git commit -m 'chore: update finance-dashboard data' && open a PR"
echo "  after merge:  kubectl -n finance-dashboard rollout restart deploy/finance-dashboard"
