#!/usr/bin/env python3
"""Fail on Cilium policies the agent would reject as having no rules.

Cilium refuses a CiliumNetworkPolicy or CiliumClusterwideNetworkPolicy
whose spec has none of ingress, ingressDeny, egress or egressDeny with at
least one entry ("rule must have at least one of Ingress, IngressDeny,
Egress, EgressDeny"). Flux applies such an object without complaint, and
the API server accepts it, so the only symptom is a warning in every Cilium
agent's log and a policy that isn't enforced. The cluster-wide default-deny
sat like that from 2026-05-03 until it was found. `ingress: []` and
`egress: []` count as empty.

The check renders the same kustomize roots as `make test-kustomize` and
looks at every spec (and every entry of `specs`, for multi-rule policies).

Usage: scripts/check-cilium-policies.py [kustomize-root ...]
Needs PyYAML and `kubectl kustomize` (or `kustomize`) on PATH.
"""

import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("check-cilium-policies: needs PyYAML (pip install pyyaml)")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = ["apps/staging", "apps/production", "infra/configs", "infra/controllers"]
KINDS = {"CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy"}
RULE_KEYS = ("ingress", "ingressDeny", "egress", "egressDeny")


def render(path):
    if shutil.which("kubectl"):
        cmd = ["kubectl", "kustomize", str(ROOT / path)]
    else:
        cmd = ["kustomize", "build", str(ROOT / path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"check-cilium-policies: rendering {path} failed:\n{result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def problems(doc):
    meta = doc.get("metadata", {})
    name = f"{doc['kind']} {meta.get('namespace', '-')}/{meta.get('name')}"
    specs = []
    if isinstance(doc.get("spec"), dict):
        specs.append(("spec", doc["spec"]))
    for i, spec in enumerate(doc.get("specs") or []):
        specs.append((f"specs[{i}]", spec))
    if not specs:
        return [f"{name}: has neither spec nor specs"]
    out = []
    for where, spec in specs:
        if not any(isinstance(spec.get(k), list) and spec[k] for k in RULE_KEYS):
            out.append(
                f"{name} {where}: no non-empty {', '.join(RULE_KEYS)} — Cilium rejects it "
                "(rule must have at least one of Ingress, IngressDeny, Egress, EgressDeny)"
            )
    return out


def main(argv=None):
    roots = (argv if argv is not None else sys.argv[1:]) or DEFAULT_ROOTS
    failures, checked = [], 0
    for root in roots:
        for doc in render(root):
            if doc.get("kind") in KINDS:
                checked += 1
                failures += problems(doc)
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print(f"checked {checked} Cilium policies in {', '.join(roots)}: {len(failures)} invalid")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
