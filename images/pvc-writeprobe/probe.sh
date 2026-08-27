#!/bin/bash
# pvc-writeprobe — proves PVCs are actually writable, rather than trusting status.
#
# WHY THIS EXISTS. On 2026-08-13 both AdGuard `work` volumes remounted read-only
# after an iSCSI error and stayed that way for FOURTEEN DAYS. Nothing noticed:
#
#   * the pods stayed Running and Ready — status lies for this failure
#   * NodeFilesystemReadOnly did not fire, and NOT because the mount was
#     unmonitored. node_filesystem_readonly DOES scrape the CSI globalmounts
#     (69 series at time of writing). The metric was scraped, and it was WRONG.
#
# Measured 2026-08-26, both facts at the same instant:
#
#   $ kubectl -n adguard-prod exec adguard-0 -c adguard -- \
#       sh -c 'printf ok > /opt/adguardhome/work/.p'
#     sh: can't create ...: Read-only file system      <-- hard EROFS
#
#   node_filesystem_readonly{fstype="ext4"} == 1  ->  0 series   <-- "all fine"
#
# ext4's emergency_ro error mode fails every write with EROFS but leaves the
# mount still advertising `rw`. node-exporter reports what the mount says, so
# the gauge stays 0 forever. No mount-flag scan can close this gap, because the
# mount flag is the thing that is lying. Only an actual write distinguishes a
# working volume from a dead one.
#
# The only thing that distinguishes a working volume from a dead one is trying
# to write to it. So that is what this does.
#
# WHAT IT TOUCHES. One file, at a fixed predictable path, in the root of each
# mount:  .homelab-writeprobe
# A dotfile so directory scanners skip it, a handful of bytes, removed
# immediately. It never reads, moves, or overwrites anything the app owns, and
# the name is unmistakably ours.
#
# A few bytes rather than a bare touch on purpose: creating a zero-byte file can
# succeed on a filesystem with no free space, so `touch` alone would miss a full
# volume. Capacity is separately covered by KubePersistentVolumeFillingUp; this
# is belt and braces.
set -uo pipefail

PROBE_FILE="${PROBE_FILE:-.homelab-writeprobe}"
INTERVAL="${INTERVAL_SECONDS:-300}"
OUT="${OUT_FILE:-/metrics/metrics}"
# Namespaces to skip entirely (system namespaces own no app data worth probing).
SKIP_NS="${SKIP_NS:-kube-system,kube-public,kube-node-lease}"
# Opt-out, set as a pod annotation:
#   homelab.burntbytes.com/writeprobe-skip: "all"
#   homelab.burntbytes.com/writeprobe-skip: "some-pvc,other-pvc"
# Use it for any volume where even a transient dotfile is unwelcome.
SKIP_ANNOTATION="homelab.burntbytes.com/writeprobe-skip"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

emit() {
  local tmp="${OUT}.tmp"
  {
    echo "# HELP homelab_pvc_writable Whether a real write to the PVC succeeded (1) or failed (0)."
    echo "# TYPE homelab_pvc_writable gauge"
    cat "$1"
    echo "# HELP homelab_pvc_writeprobe_last_run_seconds Unix time of the last completed sweep."
    echo "# TYPE homelab_pvc_writeprobe_last_run_seconds gauge"
    echo "homelab_pvc_writeprobe_last_run_seconds $(date +%s)"
  } > "$tmp"
  mv "$tmp" "$OUT"   # atomic: the scraper never reads a half-written file
}

sweep() {
  local body; body="$(mktemp)"
  # Every running pod, with the PVCs it mounts and the container to exec into.
  kubectl get pods -A -o json --field-selector=status.phase=Running 2>/dev/null \
  | jq -r --arg skip "$SKIP_NS" --arg ann "$SKIP_ANNOTATION" '
      ($skip | split(",")) as $s
      | .items[]
      | select(.metadata.namespace as $n | ($s | index($n)) | not)
      | . as $pod
      | ($pod.spec.volumes // [])[]
      | select(.persistentVolumeClaim != null)
      | {vol: .name, pvc: .persistentVolumeClaim.claimName} as $v
      | select(
          (($pod.metadata.annotations // {})[$ann] // "") as $skip
          | ($skip != "all")
            and (($skip | split(",") | map(ltrimstr(" ") | rtrimstr(" ")) | index($v.pvc)) | not)
        )
      | ($pod.spec.containers[])
      | . as $c
      | (($c.volumeMounts // [])[] | select(.name == $v.vol and (.readOnly // false) == false))
      | [$pod.metadata.namespace, $pod.metadata.name, $c.name, $v.pvc, .mountPath]
      | @tsv' 2>/dev/null \
  | sort -u -k1,1 -k4,4 \
  | while IFS=$'\t' read -r ns pod ctr pvc mnt; do
      [ -z "${mnt:-}" ] && continue
      local target="${mnt%/}/${PROBE_FILE}"
      if kubectl exec -n "$ns" "$pod" -c "$ctr" -- \
           sh -c "printf ok > '$target' && rm -f '$target'" >/dev/null 2>&1; then
        echo "homelab_pvc_writable{namespace=\"$ns\",pvc=\"$pvc\",pod=\"$pod\",mountpath=\"$mnt\"} 1" >> "$body"
      else
        echo "homelab_pvc_writable{namespace=\"$ns\",pvc=\"$pvc\",pod=\"$pod\",mountpath=\"$mnt\"} 0" >> "$body"
        log "NOT WRITABLE ${ns}/${pvc} at ${mnt} (pod ${pod})"
      fi
    done
  emit "$body"
  rm -f "$body"
}

mkdir -p "$(dirname "$OUT")"
log "pvc-writeprobe starting; interval=${INTERVAL}s probe_file=${PROBE_FILE}"
while true; do
  sweep
  log "sweep complete: $(grep -c '^homelab_pvc_writable' "$OUT" 2>/dev/null || echo 0) volume(s)"
  sleep "$INTERVAL"
done
