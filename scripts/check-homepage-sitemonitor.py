#!/usr/bin/env python3
"""Fail if a homepage tile has a siteMonitor that its CiliumNetworkPolicy
won't let through the Gateway.

A siteMonitor probe to https://<app>.burntbytes.com goes to the Gateway VIP,
and the Gateway's Envoy then checks the homepage pod's egress policy against
the backend the route selects: the backend pod on its container port (or,
for a selector-less Service, the endpoint IP and port). If no egress rule
allows that, Envoy answers `403 Access denied`, which homepage shows as a
green dot. So a missing rule fails silently; this check makes it loud.

For each environment it reads the tiles' siteMonitor URLs from
apps/<env>/homepage/config/services.yaml, renders the repo (apps/<env> and
infra/controllers) to find the HTTPRoute, Service and workload behind each
URL, and checks that the homepage CNPs rendered into homepage-<env> have an
egress rule for that namespace, pod labels and port.

Backends whose Service comes from a Helm chart (not rendered here) are
described in HELM_BACKENDS below; keep that table in sync with the charts.

Usage: scripts/check-homepage-sitemonitor.py [--env production|staging ...]
Needs PyYAML and `kustomize` (or `kubectl kustomize`) on PATH.
"""

import argparse
import ipaddress
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parent.parent
GATEWAYS = {"production": "app-gateway-production", "staging": "app-gateway-staging"}

# (namespace, service, service port) -> (pod labels, container port) for
# Services created by Helm charts, which kustomize doesn't render.
HELM_BACKENDS = {
    ("monitoring", "kube-prometheus-stack-grafana", 80): ({"app.kubernetes.io/name": "grafana"}, 3000),
    ("monitoring", "kube-prometheus-stack-prometheus", 9090): ({"app.kubernetes.io/name": "prometheus"}, 9090),
    ("monitoring", "kube-prometheus-stack-alertmanager", 9093): ({"app.kubernetes.io/name": "alertmanager"}, 9093),
}

NS_KEYS = ("k8s:io.kubernetes.pod.namespace", "io.kubernetes.pod.namespace")


def render(path):
    if shutil.which("kustomize"):
        cmd = ["kustomize", "build", str(ROOT / path)]
    else:
        cmd = ["kubectl", "kustomize", str(ROOT / path)]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def site_monitors(env):
    groups = yaml.safe_load((ROOT / "apps" / env / "homepage" / "config" / "services.yaml").read_text())
    for group in groups or []:
        for group_name, tiles in group.items():
            for tile in tiles or []:
                for tile_name, spec in tile.items():
                    if isinstance(spec, dict) and spec.get("siteMonitor"):
                        yield f"{group_name}/{tile_name}", spec["siteMonitor"]


def find_backend(routes, gateway, url):
    """(namespace, service, port) of the backendRef the longest matching
    path prefix selects, or None."""
    parts = urlsplit(url)
    host, path = parts.hostname, parts.path or "/"
    best = None
    for route in routes:
        spec = route.get("spec", {})
        if host not in spec.get("hostnames", []):
            continue
        if gateway not in [p.get("name") for p in spec.get("parentRefs", [])]:
            continue
        for rule in spec.get("rules", []):
            refs = [b for b in rule.get("backendRefs") or [] if b.get("kind", "Service") == "Service"]
            if not refs:
                continue
            for match in rule.get("matches") or [{}]:
                prefix = (match.get("path") or {}).get("value", "/")
                if path.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                    ref = refs[0]
                    ns = ref.get("namespace", route["metadata"].get("namespace"))
                    best = (prefix, (ns, ref["name"], ref.get("port")))
    return best[1] if best else None


def resolve(objects, ns, svc_name, svc_port):
    """What the homepage pod's egress must allow for this backend:
    ("pods", pod labels, container port) or ("ips", [ip], port)."""
    if (ns, svc_name, svc_port) in HELM_BACKENDS:
        labels, port = HELM_BACKENDS[(ns, svc_name, svc_port)]
        return ("pods", labels, port)
    svc = objects.get(("Service", ns, svc_name))
    if svc is None:
        raise LookupError(f"Service {ns}/{svc_name} isn't in the rendered repo or HELM_BACKENDS")
    ports = [p for p in svc["spec"].get("ports", []) if p.get("port") == svc_port]
    if not ports:
        raise LookupError(f"Service {ns}/{svc_name} has no port {svc_port}")
    target = ports[0].get("targetPort", svc_port)
    selector = svc["spec"].get("selector")
    if not selector:
        for kind in ("EndpointSlice", "Endpoints"):
            for (k, n, _), obj in objects.items():
                if k != kind or n != ns:
                    continue
                if kind == "EndpointSlice":
                    if obj.get("metadata", {}).get("labels", {}).get("kubernetes.io/service-name") != svc_name:
                        continue
                    ips = [a for e in obj.get("endpoints", []) for a in e.get("addresses", [])]
                    eports = [p["port"] for p in obj.get("ports", []) if p.get("name") == ports[0].get("name")]
                else:
                    if obj["metadata"]["name"] != svc_name:
                        continue
                    subsets = obj.get("subsets", [])
                    ips = [a["ip"] for s in subsets for a in s.get("addresses", [])]
                    eports = [p["port"] for s in subsets for p in s.get("ports", [])]
                if ips:
                    return ("ips", ips, eports[0] if eports else target)
        raise LookupError(f"Service {ns}/{svc_name} has no selector and no rendered endpoints")
    pod_labels, container_port = None, target if isinstance(target, int) else None
    # A selector on statefulset.kubernetes.io/pod-name (set by the controller,
    # not in the pod template) names one pod of a StatefulSet: <sts>-<ordinal>.
    sts_pod = selector.get("statefulset.kubernetes.io/pod-name")
    for (kind, n, name), obj in objects.items():
        if kind not in ("Deployment", "StatefulSet", "DaemonSet") or n != ns:
            continue
        template = obj["spec"]["template"]
        labels = template.get("metadata", {}).get("labels", {})
        if sts_pod:
            matches = kind == "StatefulSet" and sts_pod.rsplit("-", 1)[0] == name
        else:
            matches = all(labels.get(k) == v for k, v in selector.items())
        if matches:
            pod_labels = labels
            if isinstance(target, str):
                for c in template["spec"].get("containers", []):
                    for p in c.get("ports", []):
                        if p.get("name") == target:
                            container_port = p["containerPort"]
            break
    if pod_labels is None:
        pod_labels = selector  # workload not rendered; the selector is a subset of the pod's labels
    if container_port is None:
        raise LookupError(f"Service {ns}/{svc_name} targets named port {target!r}, which no rendered workload defines")
    return ("pods", pod_labels, container_port)


def rule_ports(rule):
    return {int(p["port"]) for tp in rule.get("toPorts", []) for p in tp.get("ports", []) if str(p.get("port", "")).isdigit()}


def allowed(policies, need):
    kind, what, port = need
    for policy in policies:
        for rule in policy["spec"].get("egress", []):
            if port not in rule_ports(rule):
                continue
            if kind == "pods":
                for sel in rule.get("toEndpoints", []):
                    labels = dict(sel.get("matchLabels", {}))
                    ns = next((labels.pop(k) for k in NS_KEYS if k in labels), None)
                    if ns == what.get("__ns__") and all(what.get(k) == v for k, v in labels.items()):
                        return True
            else:
                cidrs = [c.get("cidr") for c in rule.get("toCIDRSet", [])] + list(rule.get("toCIDR", []))
                for cidr in cidrs:
                    net = ipaddress.ip_network(cidr, strict=False)
                    if all(ipaddress.ip_address(ip) in net for ip in what):
                        return True
    return False


def check(env):
    docs = render(f"apps/{env}") + render("infra/controllers")
    objects = {}
    for d in docs:
        meta = d.get("metadata", {})
        objects[(d.get("kind"), meta.get("namespace"), meta.get("name"))] = d
    routes = [d for d in docs if d.get("kind") == "HTTPRoute"]
    policies = [
        d
        for d in docs
        if d.get("kind") == "CiliumNetworkPolicy"
        and d["metadata"].get("namespace") == f"homepage-{'prod' if env == 'production' else 'stage'}"
        and d["spec"].get("endpointSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name") == "homepage"
    ]
    if not policies:
        return [f"{env}: no homepage CiliumNetworkPolicy rendered"]
    failures = []
    for tile, url in site_monitors(env):
        backend = find_backend(routes, GATEWAYS[env], url)
        if backend is None:
            failures.append(f"{env} {tile}: no HTTPRoute on {GATEWAYS[env]} for {url}")
            continue
        try:
            need = resolve(objects, *backend)
        except LookupError as exc:
            failures.append(f"{env} {tile}: {exc}")
            continue
        if need[0] == "pods":
            need = ("pods", {**need[1], "__ns__": backend[0]}, need[2])
        if allowed(policies, need):
            print(f"ok   {env} {tile}: {url} -> {backend[0]}/{backend[1]} port {need[2]}")
        else:
            if need[0] == "pods":
                labels = {k: v for k, v in need[1].items() if k != "__ns__"}
                target = f"pods in {backend[0]} with labels {labels}"
            else:
                target = f"IPs {need[1]}"
            failures.append(
                f"{env} {tile}: {url} -> {backend[0]}/{backend[1]}; no homepage egress rule allows {target} on port {need[2]}"
            )
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", action="append", choices=sorted(GATEWAYS), help="default: both")
    args = parser.parse_args(argv)
    failures = []
    for env in args.env or sorted(GATEWAYS):
        failures += check(env)
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
