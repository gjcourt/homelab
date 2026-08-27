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
# Seconds, not "15s": this is busybox `timeout`, not a kubectl flag.
#
# This was previously passed as a kubectl flag, and it silently broke
# EVERYTHING. Measured on kubectl v1.35.0 in-cluster: passing that flag at
# any position, on any subcommand, makes kubectl lose in-cluster config and
# fall back to http://localhost:8080, so every probe returned UNKNOWN while
# the tool looked healthy. Wrap the call; do not ask kubectl to time itself.
EXEC_TIMEOUT="${EXEC_TIMEOUT:-15}"
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
    echo "# HELP homelab_pvc_probe_unknown Probe could not determine writability (exec failed, or an unrecognised error)."
    echo "# TYPE homelab_pvc_probe_unknown gauge"
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
  | sort -u \
  `# Whole-line dedupe, NOT -k1,1 -k4,4. Keying on (namespace, pvc) drops every`\
  `# pod but one when several share an RWX volume -- immich-microservices runs 4`\
  `# replicas on immich-photos-pvc. emergency_ro is per-mount and per-node, so`\
  `# each pod's mount is a distinct thing that can die on its own.` \
  | while IFS=$'\t' read -r ns pod ctr pvc mnt; do
      [ -z "${mnt:-}" ] && continue
      local target="${mnt%/}/${PROBE_FILE}"
      # `container` is part of the identity, not decoration. Two containers in
      # one pod can mount the SAME pvc at the SAME path -- jellyfin and its
      # transcode-janitor sidecar both mount jellyfin-cache at /cache -- and
      # without this label those emit byte-identical series. That is invalid
      # exposition: the probe wrote 77 lines and Prometheus stored 75,
      # silently discarding the duplicates.
      local labels="namespace=\"$ns\",pvc=\"$pvc\",pod=\"$pod\",container=\"$ctr\",mountpath=\"$mnt\""
      local err="" rc=1 attempt=0

      # An exec can fail for reasons that have nothing to do with the volume:
      # the pod is Terminating, the container is restarting, the apiserver is
      # throttling, a node is draining. Those are exactly the events that hit
      # MANY pods at once, so treating a failed exec as "not writable" would
      # turn one cluster hiccup into a storm of critical storage pages.
      #
      # So: retry, then classify by what the kernel actually said. Only a real
      # filesystem refusal counts as 0. Anything we cannot classify is reported
      # as UNKNOWN -- no writable series at all -- because a wrong 0 pages
      # someone at 3am and a wrong 1 is a silent lie. Neither is acceptable, so
      # we say "don't know" instead and alert separately if that persists.
      while [ $attempt -lt 3 ]; do
        attempt=$((attempt + 1))
        err="$(timeout "${EXEC_TIMEOUT}" \
                 kubectl exec -n "$ns" "$pod" -c "$ctr" -- \
                 sh -c "printf ok > \"$target\" && rm -f \"$target\"" 2>&1)"
        rc=$?
        [ $rc -eq 0 ] && break
        case "$err" in
          *"Read-only file system"*|*"No space left on device"*) break ;;
        esac
        sleep 2
      done

      if [ $rc -eq 0 ]; then
        echo "homelab_pvc_writable{$labels} 1" >> "$body"
      else
        case "$err" in
          *"Read-only file system"*)
            echo "homelab_pvc_writable{$labels} 0" >> "$body"
            log "NOT WRITABLE (read-only) ${ns}/${pvc} at ${mnt} pod=${pod}" ;;
          *"No space left on device"*)
            echo "homelab_pvc_writable{$labels} 0" >> "$body"
            log "NOT WRITABLE (full) ${ns}/${pvc} at ${mnt} pod=${pod}" ;;
          *)
            # Could not reach the pod, or the write failed in a way we do not
            # recognise (a non-root container that cannot write the mount ROOT
            # while the app writes a subdirectory quite happily would land
            # here). Deliberately NOT a 0.
            echo "homelab_pvc_probe_unknown{$labels} 1" >> "$body"
            log "UNKNOWN ${ns}/${pvc} at ${mnt} pod=${pod}: $(echo "$err" | tr '\n' ' ' | cut -c1-160)" ;;
        esac
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
