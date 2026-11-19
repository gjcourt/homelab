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

try:
    import yaml
except ImportError:
    sys.exit("check-homepage-sitemonitor: needs PyYAML (pip install pyyaml)")

ROOT = Path(__file__).resolve().parent.parent
GATEWAYS = {"production": "app-gateway-production", "staging": "app-gateway-staging"}

# (namespace, service, service port) -> (pod labels, container port) for
# Services created by Helm charts, which kustomize doesn't render. Keep in
# sync with infra/controllers/kube-prometheus-stack (chart values/version).
HELM_BACKENDS = {
    ("monitoring", "kube-prometheus-stack-grafana", 80): ({"app.kubernetes.io/name": "grafana"}, 3000),
    ("monitoring", "kube-prometheus-stack-prometheus", 9090): ({"app.kubernetes.io/name": "prometheus"}, 9090),
    ("monitoring", "kube-prometheus-stack-alertmanager", 9093): ({"app.kubernetes.io/name": "alertmanager"}, 9093),
}

NS_KEYS = ("k8s:io.kubernetes.pod.namespace", "io.kubernetes.pod.namespace")


def render(path):
    # Same renderer as `make test-kustomize` when kubectl is present.
    if shutil.which("kubectl"):
        cmd = ["kubectl", "kustomize", str(ROOT / path)]
    else:
        cmd = ["kustomize", "build", str(ROOT / path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"check-homepage-sitemonitor: rendering {path} failed:\n{result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def site_monitors(env):
    groups = yaml.safe_load((ROOT / "apps" / env / "homepage" / "config" / "services.yaml").read_text())
    for group in groups or []:
        for group_name, tiles in group.items():
            for tile in tiles or []:
                for tile_name, spec in tile.items():
                    if isinstance(spec, dict) and spec.get("siteMonitor"):
                        yield f"{group_name}/{tile_name}", spec["siteMonitor"]


def _path_match(match, path):
    """Rank of a Gateway API path match against path (higher wins), or None.
    Exact beats PathPrefix; a longer prefix beats a shorter one; a prefix
    matches whole path segments only (/api matches /api and /api/x, not
    /apix). Anything else (RegularExpression, header, query or method
    matches) is refused rather than guessed."""
    if set(match) - {"path"}:
        raise LookupError(f"route match {match} uses more than a path; the check can't evaluate it")
    kind = (match.get("path") or {}).get("type", "PathPrefix")
    value = (match.get("path") or {}).get("value", "/")
    if kind == "Exact":
        return (2, len(value)) if path == value else None
    if kind == "PathPrefix":
        prefix = value.rstrip("/")
        if not prefix or path == prefix or path.startswith(prefix + "/"):
            return (1, len(prefix))
        return None
    raise LookupError(f"route match type {kind!r} isn't supported by the check")


def find_backends(routes, gateway, url):
    """Every (namespace, service, port) the matching rule sends url to (all
    of a rule's weighted backendRefs), or None if no route matches. Only the
    route's HTTPS listeners count (sectionName https*, or none)."""
    parts = urlsplit(url)
    host, path = parts.hostname, parts.path or "/"
    best = None
    for route in routes:
        spec = route.get("spec", {})
        if host not in spec.get("hostnames", []):
            continue
        parents = [
            p
            for p in spec.get("parentRefs", [])
            if p.get("name") == gateway and (p.get("sectionName") or "https").startswith("https")
        ]
        if not parents:
            continue
        for rule in spec.get("rules", []):
            refs = [b for b in rule.get("backendRefs") or [] if b.get("kind", "Service") == "Service"]
            if not refs:
                continue  # redirect-only rule
            for match in rule.get("matches") or [{}]:
                rank = _path_match(match, path)
                if rank is not None and (best is None or rank > best[0]):
                    ns = route["metadata"].get("namespace")
                    best = (rank, [(r.get("namespace", ns), r["name"], r.get("port")) for r in refs if r.get("weight", 1) != 0])
    return best[1] if best else None


def resolve(objects, ns, svc_name, svc_port):
    """What the homepage pod's egress must allow for this backend, as a list
    (one entry per workload behind the Service): ("pods", pod labels,
    container port) or ("ips", [ip], port)."""
    if (ns, svc_name, svc_port) in HELM_BACKENDS:
        labels, port = HELM_BACKENDS[(ns, svc_name, svc_port)]
        return [("pods", labels, port)]
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
                    return [("ips", ips, eports[0] if eports else target)]
        raise LookupError(f"Service {ns}/{svc_name} has no selector and no rendered endpoints")
    needs = []
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
        if not matches:
            continue
        container_port = target if isinstance(target, int) else None
        if isinstance(target, str):
            for c in template["spec"].get("containers", []):
                for p in c.get("ports", []):
                    if p.get("name") == target:
                        container_port = p["containerPort"]
        if container_port is None:
            raise LookupError(f"{kind} {ns}/{name} has no container port named {target!r} (Service {svc_name})")
        needs.append(("pods", labels, container_port))
    if needs:
        return needs
    # Workload not rendered here. Only trust the selector if it carries no
    # controller-set label Cilium leaves out of identities.
    if sts_pod or not isinstance(target, int):
        raise LookupError(f"Service {ns}/{svc_name}: no rendered workload matches its selector {selector}")
    return [("pods", selector, target)]


def _port_allowed(rule, port):
    """True if the rule's toPorts allow TCP to port (no toPorts = any port)."""
    to_ports = rule.get("toPorts")
    if not to_ports:
        return True
    for tp in to_ports:
        for p in tp.get("ports", []):
            if p.get("protocol", "ANY").upper() not in ("TCP", "ANY"):
                continue
            first = str(p.get("port", "0"))
            if not first.isdigit():
                continue  # named ports aren't evaluated; treat as not allowing
            low, high = int(first), int(p.get("endPort", first))
            if int(first) == 0 or low <= port <= high:
                return True
    return False


def allowed(policies, need):
    kind, what, port = need
    for policy in policies:
        for rule in policy["spec"].get("egress", []):
            if not _port_allowed(rule, port):
                continue
            entities = set(rule.get("toEntities", []))
            if "all" in entities or (kind == "pods" and "cluster" in entities) or (kind == "ips" and "world" in entities):
                return True
            if kind == "pods":
                for sel in rule.get("toEndpoints", []):
                    if sel.get("matchExpressions"):
                        continue  # not evaluated; doesn't count as allowing
                    labels = dict(sel.get("matchLabels", {}))
                    ns = next((labels.pop(k) for k in NS_KEYS if k in labels), policy["metadata"].get("namespace"))
                    if ns == what.get("__ns__") and all(what.get(k) == v for k, v in labels.items()):
                        return True
            else:
                cidrs = [c.get("cidr") for c in rule.get("toCIDRSet", []) if not c.get("except")]
                cidrs += list(rule.get("toCIDR", []))
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
    namespace = f"homepage-{'prod' if env == 'production' else 'stage'}"
    deploy = objects.get(("Deployment", namespace, "homepage"))
    if deploy is None:
        return [f"{env}: no homepage Deployment rendered in {namespace}"]
    pod_labels = deploy["spec"]["template"].get("metadata", {}).get("labels", {})
    # Only policies that actually select the homepage pod count.
    policies = [
        d
        for d in docs
        if d.get("kind") == "CiliumNetworkPolicy"
        and d["metadata"].get("namespace") == namespace
        and not d["spec"].get("endpointSelector", {}).get("matchExpressions")
        and all(pod_labels.get(k) == v for k, v in d["spec"].get("endpointSelector", {}).get("matchLabels", {}).items())
    ]
    if not policies:
        return [f"{env}: no CiliumNetworkPolicy selects the homepage pod"]
    failures = []
    for tile, url in site_monitors(env):
        try:
            backends = find_backends(routes, GATEWAYS[env], url)
            if backends is None:
                failures.append(f"{env} {tile}: no HTTPRoute on {GATEWAYS[env]} for {url}")
                continue
            for backend in backends:
                for need in resolve(objects, *backend):
                    if need[0] == "pods":
                        need = ("pods", {**need[1], "__ns__": backend[0]}, need[2])
                    if allowed(policies, need):
                        print(f"ok   {env} {tile}: {url} -> {backend[0]}/{backend[1]} port {need[2]}")
                        continue
                    if need[0] == "pods":
                        labels = {k: v for k, v in need[1].items() if k != "__ns__"}
                        target = f"pods in {backend[0]} with labels {labels}"
                    else:
                        target = f"IPs {need[1]}"
                    failures.append(
                        f"{env} {tile}: {url} -> {backend[0]}/{backend[1]}; "
                        f"no homepage egress rule allows {target} on port {need[2]}"
                    )
        except LookupError as exc:
            failures.append(f"{env} {tile}: {exc}")
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
