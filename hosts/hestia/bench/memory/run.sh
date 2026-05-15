#!/usr/bin/env bash
# hestia memory benchmark host wrapper.
#
# Purpose:
#   Apply host-level performance controls (CPU governor, transparent huge
#   pages, page-cache drop) and run the containerized STREAM + Intel MLC
#   benchmark. Intended for comparing 6-DIMM vs 8-DIMM populated configs
#   on hestia (EPYC 8004 / Siena, 6-channel DDR5, TrueNAS SCALE host).
#
# Prerequisites:
#   - Run as root (touches /sys, /proc/sys).
#   - vLLM (or any GPU inference workload) must be stopped: the script
#     verifies via midclt if available; if it reports RUNNING, the script
#     refuses to continue.
#   - Docker image hestia-memory-bench:1 already built locally.
#   - $RESULTS_DIR (default /mnt/tank/bench/memory) writable.
#
# Usage:
#   sudo ./run.sh 6dimm
#   sudo RESULTS_DIR=/mnt/data/bench/memory ./run.sh 8dimm
#
# Output:
#   ${RESULTS_DIR}/<label>-<utc-ts>.preflight.json   (host state snapshot)
#   ${RESULTS_DIR}/<label>-<utc-ts>.jsonl            (STREAM + MLC events)
#
# Note: if your TrueNAS pool isn't named 'tank', override RESULTS_DIR.

set -euo pipefail

# ----------------------------------------------------------------------------
# Args
# ----------------------------------------------------------------------------
usage() {
    cat >&2 <<EOF
usage: $0 <label>
  label: identifier for the run, e.g. 6dimm, 8dimm
  env RESULTS_DIR: output directory (default: /mnt/tank/bench/memory)
EOF
}

if [[ $# -lt 1 || -z "${1:-}" ]]; then
    usage
    exit 64
fi
LABEL="$1"

RESULTS_DIR="${RESULTS_DIR:-/mnt/tank/bench/memory}"
IMAGE="${IMAGE:-hestia-memory-bench:1}"

# ----------------------------------------------------------------------------
# Pre-checks
# ----------------------------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
    echo "error: must run as root (touches /sys and /proc/sys)" >&2
    exit 77
fi

mkdir -p "${RESULTS_DIR}"
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
PREFLIGHT="${RESULTS_DIR}/${LABEL}-${TS}.preflight.json"

# --- Verify vLLM is stopped ---
if command -v midclt >/dev/null 2>&1; then
    state="$(midclt call app.query '[["name","=","vllm"]]' 2>/dev/null \
        | python3 -c 'import sys,json
apps=json.load(sys.stdin)
print(apps[0]["state"] if apps else "absent")' 2>/dev/null || echo "unknown")"
    case "$state" in
        RUNNING)
            cat >&2 <<EOF
error: vLLM app is RUNNING on hestia. Memory benchmark must not contend
with GPU inference workloads for the host memory bus.

Stop it first:
    midclt call app.stop vllm
    nvidia-smi   # verify GPU idle / no python processes attached

Then re-run this script.
EOF
            exit 2
            ;;
        absent|STOPPED|CRASHED|DEPLOYING|"")
            echo "info: vllm state=${state}; proceeding." >&2
            ;;
        *)
            echo "warning: unrecognized vllm state '${state}'; proceeding anyway." >&2
            ;;
    esac
else
    echo "warning: midclt not found; skipping vLLM check (non-TrueNAS host?)." >&2
fi

# ----------------------------------------------------------------------------
# Capture prior governor (for restoration on exit)
# ----------------------------------------------------------------------------
PRIOR_GOV=""
if [[ -r /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]]; then
    PRIOR_GOV="$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || true)"
fi

restore_governor() {
    local target="${PRIOR_GOV:-ondemand}"
    if [[ -z "$target" ]]; then
        target="ondemand"
    fi
    echo "info: restoring CPU governor to ${target}" >&2
    if command -v cpupower >/dev/null 2>&1; then
        cpupower frequency-set -g "$target" >/dev/null 2>&1 || true
    else
        # nullglob: if cpufreq isn't present, expand to nothing instead of
        # silently iterating over the literal glob string.
        shopt -s nullglob
        for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
            [[ -w "$f" ]] && echo "$target" > "$f" 2>/dev/null || true
        done
        shopt -u nullglob
    fi
}
trap restore_governor EXIT

# ----------------------------------------------------------------------------
# Apply host controls
# ----------------------------------------------------------------------------
echo "info: setting CPU governor to performance" >&2
if command -v cpupower >/dev/null 2>&1; then
    cpupower frequency-set -g performance >/dev/null 2>&1 || {
        echo "warning: cpupower failed; falling back to sysfs writes" >&2
        # nullglob: if cpufreq isn't present, expand to nothing instead of
        # silently iterating over the literal glob string.
        shopt -s nullglob
        for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
            [[ -w "$f" ]] && echo performance > "$f"
        done
        shopt -u nullglob
    }
else
    echo "warning: cpupower not installed; using sysfs fallback" >&2
    shopt -s nullglob
    for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        [[ -w "$f" ]] && echo performance > "$f"
    done
    shopt -u nullglob
fi

echo "info: enabling transparent hugepages (always/always)" >&2
[[ -w /sys/kernel/mm/transparent_hugepage/enabled ]] \
    && echo always > /sys/kernel/mm/transparent_hugepage/enabled || true
[[ -w /sys/kernel/mm/transparent_hugepage/defrag ]] \
    && echo always > /sys/kernel/mm/transparent_hugepage/defrag || true

echo "info: dropping page caches" >&2
sync
[[ -w /proc/sys/vm/drop_caches ]] && echo 3 > /proc/sys/vm/drop_caches || true

# ----------------------------------------------------------------------------
# Preflight snapshot
# ----------------------------------------------------------------------------
echo "info: writing preflight snapshot to ${PREFLIGHT}" >&2

dmidecode_mem=""
if command -v dmidecode >/dev/null 2>&1; then
    dmidecode_mem="$(dmidecode -t memory 2>/dev/null \
        | grep -E "Locator|Size|Speed|Configured|Manufacturer|Part Number" || true)"
else
    dmidecode_mem="dmidecode not available"
fi

lscpu_out="$(lscpu 2>/dev/null || echo "lscpu not available")"
numactl_out="$(numactl --hardware 2>/dev/null || echo "numactl not available")"
cmdline_out="$(cat /proc/cmdline 2>/dev/null || echo "")"
gov_out="$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo unknown)"
thp_out="$(cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || echo unknown)"
thp_defrag_out="$(cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || echo unknown)"

nvidia_smi_out=""
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia_smi_out="$(nvidia-smi --query-gpu=name,utilization.gpu,memory.used --format=csv 2>/dev/null || echo "nvidia-smi failed")"
else
    nvidia_smi_out="nvidia-smi not available"
fi

# Build JSON via python3 to avoid hand-rolled escaping.
export LABEL TS PRIOR_GOV GOV_OUT="$gov_out" THP_OUT="$thp_out" \
    THP_DEFRAG_OUT="$thp_defrag_out" CMDLINE_OUT="$cmdline_out" \
    DMIDECODE_MEM="$dmidecode_mem" LSCPU_OUT="$lscpu_out" \
    NUMACTL_OUT="$numactl_out" NVIDIA_SMI_OUT="$nvidia_smi_out"

python3 - "$PREFLIGHT" <<'PYEOF'
import json, sys, os
path = sys.argv[1]
doc = {
    "label": os.environ.get("LABEL", ""),
    "ts": os.environ.get("TS", ""),
    "prior_governor": os.environ.get("PRIOR_GOV", ""),
    "applied": {
        "governor": os.environ.get("GOV_OUT", ""),
        "thp_enabled": os.environ.get("THP_OUT", ""),
        "thp_defrag": os.environ.get("THP_DEFRAG_OUT", ""),
    },
    "cmdline": os.environ.get("CMDLINE_OUT", ""),
    "dmidecode_memory": os.environ.get("DMIDECODE_MEM", "").splitlines(),
    "lscpu": os.environ.get("LSCPU_OUT", "").splitlines(),
    "numactl_hardware": os.environ.get("NUMACTL_OUT", "").splitlines(),
    "nvidia_smi": os.environ.get("NVIDIA_SMI_OUT", "").splitlines(),
}
with open(path, "w") as f:
    json.dump(doc, f, indent=2)
PYEOF

# ----------------------------------------------------------------------------
# Run the container
# ----------------------------------------------------------------------------
echo "info: launching container ${IMAGE} for label=${LABEL}" >&2
docker run --rm \
    --privileged \
    --network=host \
    --ipc=host \
    --pid=host \
    -v "${RESULTS_DIR}:/results" \
    "${IMAGE}" "${LABEL}"

# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
latest="$(ls -1t "${RESULTS_DIR}/${LABEL}-"*.jsonl 2>/dev/null | head -n 1 || true)"
if [[ -n "$latest" && -f "$latest" ]]; then
    size="$(stat -c '%s' "$latest" 2>/dev/null || wc -c < "$latest")"
    echo "result: ${latest} (${size} bytes)" >&2
else
    echo "warning: no JSONL file matching ${RESULTS_DIR}/${LABEL}-*.jsonl was produced" >&2
fi
