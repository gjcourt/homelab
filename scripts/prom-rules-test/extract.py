"""Write the rule groups of every PrometheusRule given on argv to one promtool rules file.

Usage: python3 extract.py OUT.yaml infra/configs/homelabscope/prometheus-rule.yaml [...]
"""
import sys

import yaml

groups = []
for path in sys.argv[2:]:
    for doc in yaml.safe_load_all(open(path)):
        if doc and doc.get("kind") == "PrometheusRule":
            groups.extend(doc["spec"]["groups"])
with open(sys.argv[1], "w") as f:
    yaml.safe_dump({"groups": groups}, f, sort_keys=False)
