#!/usr/bin/env bash
set -euo pipefail

# Runs a simple write/read test against Synology CSI provisioned storage.
# This repo's default/known-good dynamic provisioner is iSCSI (RWO).
# (Some environments may also have an NFS StorageClass, but in this cluster
# the CSI provisioner reports that NFS is an unsupported volume protocol.)
#
# Usage:
#   KUBECTL=kubectl SYNO_NS=synology-csi KEEP=0 ./scripts/synology-speedtest.sh

KUBECTL="${KUBECTL:-kubectl}"
SYNO_NS="${SYNO_NS:-synology-csi}"
MANIFEST="${MANIFEST:-infra/controllers/synology-csi/speedtest.yaml}"
KEEP="${KEEP:-0}"
PRE_CLEAN="${PRE_CLEAN:-1}"

if ! command -v "$KUBECTL" >/dev/null 2>&1; then
	echo "ERROR: kubectl not found (set KUBECTL=... or install kubectl)." >&2
	exit 2
fi

if ! $KUBECTL version >/dev/null 2>&1; then
	echo "ERROR: kubectl cannot reach a cluster (check context/kubeconfig/VPN)." >&2
	exit 2
fi

if [[ ! -f "$MANIFEST" ]]; then
	echo "ERROR: manifest not found: $MANIFEST" >&2
	exit 2
fi

if ! $KUBECTL get ns "$SYNO_NS" >/dev/null 2>&1; then
	echo "ERROR: namespace does not exist: $SYNO_NS" >&2
	echo "If Synology CSI is managed by Flux, reconcile it first." >&2
	exit 2
fi

cleanup() {
	if [[ "$KEEP" == "1" ]]; then
		echo "KEEP=1 set; leaving test resources in namespace $SYNO_NS"
		return 0
	fi

	$KUBECTL -n "$SYNO_NS" delete job/read job/write --ignore-not-found
	$KUBECTL -n "$SYNO_NS" delete pvc/test-claim --ignore-not-found
}

trap cleanup EXIT

if [[ "$PRE_CLEAN" == "1" ]]; then
	# Clear any previous run so we don't end up waiting on stale resources.
	$KUBECTL -n "$SYNO_NS" delete job/read job/write --ignore-not-found >/dev/null 2>&1 || true
	$KUBECTL -n "$SYNO_NS" delete pvc/test-claim --ignore-not-found >/dev/null 2>&1 || true
fi

echo "Applying speedtest manifest: $MANIFEST (namespace: $SYNO_NS)"
# The manifest is written to run in the synology-csi namespace.
$KUBECTL -n "$SYNO_NS" apply -f "$MANIFEST"

echo "Waiting for PVC to bind (up to 3m)..."
if ! $KUBECTL -n "$SYNO_NS" wait --for=jsonpath='{.status.phase}'=Bound pvc/test-claim --timeout=3m >/dev/null 2>&1; then
	echo "PVC did not bind. Details:" >&2
	$KUBECTL -n "$SYNO_NS" get pvc test-claim -o wide || true
	$KUBECTL -n "$SYNO_NS" describe pvc test-claim || true
	$KUBECTL -n "$SYNO_NS" get events --sort-by=.metadata.creationTimestamp | tail -n 80 || true
	exit 1
fi

# Wait for write job first; if storage is read-only, this is the one that fails.
echo "Waiting for write job to complete (up to 10m)..."
$KUBECTL -n "$SYNO_NS" wait --for=condition=complete job/write --timeout=10m || true

echo "Logs: job/write"
$KUBECTL -n "$SYNO_NS" logs job/write --all-containers=true || true

# Read job depends on test.img existing; it may fail if write failed.
echo "Waiting for read job to complete (up to 10m)..."
$KUBECTL -n "$SYNO_NS" wait --for=condition=complete job/read --timeout=10m || true

echo "Logs: job/read"
$KUBECTL -n "$SYNO_NS" logs job/read --all-containers=true || true

# If either job failed, show pod status to surface mount errors.
echo "Pods (speedtest):"
$KUBECTL -n "$SYNO_NS" get pods -l app=speedtest -o wide || true

echo "Events (tail):"
$KUBECTL -n "$SYNO_NS" get events --sort-by=.metadata.creationTimestamp | tail -n 80 || true
