#!/usr/bin/env bash
set -euo pipefail

# Diagnose common Synology-backed storage read-only failures from the Kubernetes side.
#
# Usage:
#   KUBECTL=kubectl SYNO_NS=synology-csi ./scripts/synology-diag.sh

KUBECTL="${KUBECTL:-kubectl}"
SYNO_NS="${SYNO_NS:-synology-csi}"

have_cluster_access() {
	$KUBECTL version >/dev/null 2>&1
}

section() {
	echo
	echo "== $1 =="
}

if ! command -v "$KUBECTL" >/dev/null 2>&1; then
	echo "ERROR: kubectl not found (set KUBECTL=... or install kubectl)." >&2
	exit 2
fi

if ! have_cluster_access; then
	echo "ERROR: kubectl cannot reach a cluster (check context/kubeconfig/VPN)." >&2
	echo "Try: $KUBECTL config current-context" >&2
	exit 2
fi

section "Cluster Context"
$KUBECTL config current-context || true
$KUBECTL version || true

section "Synology CSI Namespace"
$KUBECTL get ns "$SYNO_NS" -o name || true

section "Synology CSI Workloads (pods)"
$KUBECTL -n "$SYNO_NS" get pods -o wide || true

section "Synology CSI Workloads (deployments/daemonsets)"
$KUBECTL -n "$SYNO_NS" get deploy,ds -o wide || true

section "StorageClasses (Synology-related)"
$KUBECTL get storageclass -o wide | grep -Ei 'synology|csi\.san\.synology\.com' || true

section "Recent Synology CSI Events (may include mount errors)"
$KUBECTL -n "$SYNO_NS" get events --sort-by=.metadata.creationTimestamp | tail -n 80 || true

section "Recent Cluster-Wide Storage/Mount Errors"
# Typical error strings:
# - Read-only file system
# - I/O error
# - stale file handle
# - mount failed
$KUBECTL get events -A --sort-by=.metadata.creationTimestamp \
	| grep -Ei 'read-only|readonly|i/o error|stale file handle|mount(ing)? .*fail|nfs' \
	| tail -n 120 || true

section "PVCs using synology storage classes"
# Show PVCs that look like they are provisioned by Synology CSI (storageClassName matches synology-*).
$KUBECTL get pvc -A -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,SC:.spec.storageClassName,STATUS:.status.phase,VOLUME:.spec.volumeName' \
	| (head -n 1 && grep -E '\ssynology-' || true)

section "PV summary (NFS + CSI)"
# This is intentionally high-level and non-parsing to avoid jq/yq dependencies.
$KUBECTL get pv -o wide | grep -Ei 'synology|nfs|csi' || true


cat <<'EOF'

== What this usually means ==

There are two common "read-only" failure modes:

1) NAS-side: Synology volume/filesystem is forced read-only (usually due to disk / filesystem errors).
   - Fix is on Synology: Storage Manager → check Volume status, run a data scrub, check SMART,
     and remediate disk/array issues. Kubernetes restarts won't make the NAS writable.

2) Client-side: A node mount flips to read-only (NFS hiccup, I/O errors, stale handles).
   - Fix is usually: restart the Synology CSI pods + restart affected workloads to force remount.

Next suggested commands:
  make synology-csi-restart
	make synology-speedtest
EOF
