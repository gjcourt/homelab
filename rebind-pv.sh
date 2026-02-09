#!/bin/bash
set -euo pipefail

NAMESPACE="$1"
PVC_NAME="$2"
OLD_PV="$3"
SIZE="$4"
STORAGE_CLASS="synology-iscsi"

echo "=== Rebinding $NAMESPACE/$PVC_NAME -> $OLD_PV ($SIZE) ==="

echo "  Deleting current PVC..."
kubectl delete pvc -n "$NAMESPACE" "$PVC_NAME" --wait=true 2>/dev/null || true

echo "  Clearing claimRef on old PV $OLD_PV..."
kubectl patch pv "$OLD_PV" --type json -p '[{"op": "remove", "path": "/spec/claimRef"}]'

echo "  Waiting for PV to become Available..."
for i in $(seq 1 30); do
  PHASE=$(kubectl get pv "$OLD_PV" -o jsonpath='{.status.phase}')
  if [ "$PHASE" = "Available" ]; then
    echo "  PV is Available"
    break
  fi
  sleep 1
done

echo "  Creating PVC $PVC_NAME -> $OLD_PV..."
kubectl apply -f - <<PVCEOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: $PVC_NAME
  namespace: $NAMESPACE
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: $SIZE
  storageClassName: $STORAGE_CLASS
  volumeName: $OLD_PV
PVCEOF

echo "  Waiting for PVC to bind..."
for i in $(seq 1 30); do
  PHASE=$(kubectl get pvc -n "$NAMESPACE" "$PVC_NAME" -o jsonpath='{.status.phase}')
  if [ "$PHASE" = "Bound" ]; then
    echo "  PVC Bound to $OLD_PV"
    break
  fi
  sleep 1
done

FINAL_PHASE=$(kubectl get pvc -n "$NAMESPACE" "$PVC_NAME" -o jsonpath='{.status.phase}')
if [ "$FINAL_PHASE" != "Bound" ]; then
  echo "  FAILED: PVC not bound! Phase: $FINAL_PHASE"
  exit 1
fi
echo "  SUCCESS"
echo ""
