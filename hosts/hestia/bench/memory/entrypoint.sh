#!/usr/bin/env bash
# Container entrypoint for the hestia memory benchmark.
#
# Usage: entrypoint.sh <label>
#   e.g. entrypoint.sh 6dimm
#        entrypoint.sh 8dimm
#
# Runs STREAM (5 iterations) and Intel MLC (idle latency, loaded latency,
# max bandwidth, bandwidth matrix), emitting a JSONL stream to
# /results/<label>-<utc-timestamp>.jsonl.

set -euo pipefail

# ----------------------------------------------------------------------------
# Args
# ----------------------------------------------------------------------------
if [[ $# -lt 1 || -z "${1:-}" ]]; then
    echo "error: label is required as first positional arg (e.g. 6dimm, 8dimm)" >&2
    exit 64
fi
LABEL="$1"

# ----------------------------------------------------------------------------
# Output setup
# ----------------------------------------------------------------------------
# Two formats on purpose:
#   - TS uses dashes (path-safe; colons break Windows + several CLI tools).
#   - TS_ISO is strict ISO 8601 with colons (for the JSONL `ts` field).
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
TS_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_EPOCH="$(date -u +%s)"

if [[ ! -d /results ]]; then
    echo "error: /results does not exist; mount a host directory with -v <host>:/results" >&2
    exit 65
fi
if [[ ! -w /results ]]; then
    echo "error: /results is not writable" >&2
    exit 66
fi

OUT="/results/${LABEL}-${TS}.jsonl"
: > "$OUT"

emit() {
    # emit <json-line>
    printf '%s\n' "$1" >> "$OUT"
}

warn() {
    local msg="$1"
    local esc
    esc=$(printf '%s' "$msg" | sed 's/\\/\\\\/g; s/"/\\"/g')
    emit "{\"event\":\"warning\",\"message\":\"${esc}\"}"
    echo "warning: ${msg}" >&2
}

# ----------------------------------------------------------------------------
# Meta event
# ----------------------------------------------------------------------------
host="$(uname -n || echo unknown)"
cpu_model="$(lscpu 2>/dev/null | awk -F: '/^Model name/ {sub(/^ +/, "", $2); print $2; exit}')"
cpu_model="${cpu_model:-unknown}"
numa_nodes="$(numactl --hardware 2>/dev/null | awk '/^available:/ {print $2; exit}')"
numa_nodes="${numa_nodes:-0}"
physical_cores="$(lscpu -p=Core,Socket 2>/dev/null | grep -v '^#' | sort -u | wc -l | tr -d ' ')"
physical_cores="${physical_cores:-1}"
total_threads="$(lscpu -p=CPU 2>/dev/null | grep -v '^#' | wc -l | tr -d ' ')"
if [[ "$physical_cores" -gt 0 && "$total_threads" -gt 0 ]]; then
    smt="$(( total_threads / physical_cores ))"
else
    smt=1
fi
thp_status="$(cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || echo unknown)"

esc_field() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

emit "{\"event\":\"meta\",\"label\":\"$(esc_field "$LABEL")\",\"ts\":\"${TS_ISO}\",\"host\":\"$(esc_field "$host")\",\"cpu\":\"$(esc_field "$cpu_model")\",\"numa_nodes\":${numa_nodes},\"physical_cores\":${physical_cores},\"smt_threads_per_core\":${smt},\"thp\":\"$(esc_field "$thp_status")\"}"

# ----------------------------------------------------------------------------
# STREAM
# ----------------------------------------------------------------------------
STREAM_FAILURES=0

run_stream() {
    local run_idx="$1"
    local tmp
    tmp="$(mktemp)"
    if ! OMP_NUM_THREADS="${physical_cores}" \
         OMP_PROC_BIND=close \
         OMP_PLACES=cores \
         numactl --cpunodebind=0 --membind=0 /usr/local/bin/stream > "$tmp" 2>&1; then
        warn "STREAM run ${run_idx} failed (exit $?)"
        # Emit null events for all 4 kernels so the JSONL is complete and the
        # aggregator can surface the partial-failure clearly, then mark this
        # run as failed for the post-loop exit check.
        local k
        for k in Copy Scale Add Triad; do
            emit "{\"event\":\"stream_run\",\"run\":${run_idx},\"kernel\":\"${k}\",\"rate_mb_per_s\":null,\"avg_time_s\":null,\"min_time_s\":null,\"max_time_s\":null}"
        done
        rm -f "$tmp"
        STREAM_FAILURES=$(( STREAM_FAILURES + 1 ))
        return 0
    fi

    # STREAM emits a block like:
    # Function    Best Rate MB/s  Avg time     Min time     Max time
    # Copy:          12345.6      0.123456     0.123456     0.123456
    # Scale:         ...
    # Add:           ...
    # Triad:         ...
    local kernel
    for kernel in Copy Scale Add Triad; do
        local line rate avg min_t max_t
        line="$(grep -E "^${kernel}:" "$tmp" | head -n 1 || true)"
        if [[ -z "$line" ]]; then
            warn "STREAM run ${run_idx}: kernel ${kernel} not parsed"
            emit "{\"event\":\"stream_run\",\"run\":${run_idx},\"kernel\":\"${kernel}\",\"rate_mb_per_s\":null,\"avg_time_s\":null,\"min_time_s\":null,\"max_time_s\":null}"
            continue
        fi
        # Strip the leading "Copy:" etc.
        rate=$(awk '{print $2}' <<<"$line")
        avg=$(awk '{print $3}' <<<"$line")
        min_t=$(awk '{print $4}' <<<"$line")
        max_t=$(awk '{print $5}' <<<"$line")
        emit "{\"event\":\"stream_run\",\"run\":${run_idx},\"kernel\":\"${kernel}\",\"rate_mb_per_s\":${rate:-null},\"avg_time_s\":${avg:-null},\"min_time_s\":${min_t:-null},\"max_time_s\":${max_t:-null}}"
    done
    rm -f "$tmp"
}

for i in 1 2 3 4 5; do
    run_stream "$i"
    if [[ "$i" -lt 5 ]]; then
        sleep 5
    fi
done

# More than half the STREAM runs failing means the bandwidth tables are
# unusable; fail loudly so the operator re-runs instead of aggregating noise.
if [[ "$STREAM_FAILURES" -ge 3 ]]; then
    warn "STREAM failed in $STREAM_FAILURES/5 runs; aborting before MLC"
    emit "{\"event\":\"done\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"duration_s\":$(( $(date -u +%s) - START_EPOCH )),\"status\":\"stream_failed\"}"
    exit 70
fi

# ----------------------------------------------------------------------------
# Intel MLC
# ----------------------------------------------------------------------------

mlc_run() {
    # mlc_run <flag>; echoes output to stdout, returns mlc exit code.
    local flag="$1"
    numactl --cpunodebind=0 --membind=0 /usr/local/bin/mlc "$flag" 2>&1
}

# --- Idle latency -----------------------------------------------------------
{
    if out="$(mlc_run --idle_latency)"; then
        # MLC prints lines like:
        #   Each iteration took ...
        #   Using small pages for ...
        # and ultimately:
        #   Idle latency = NNN.N ns          (random)
        # On some builds:
        #   Random idle latency: NNN.N ns
        #   Sequential idle latency: NNN.N ns
        random_ns="$(echo "$out" | grep -iE 'random.*latency' | grep -oE '[0-9]+\.[0-9]+' | head -n 1)"
        seq_ns="$(echo "$out" | grep -iE 'sequential.*latency' | grep -oE '[0-9]+\.[0-9]+' | head -n 1)"
        # Fallback: the unlabeled "Each iteration took N.N base cycles ( = N.N ns)" line is the random latency.
        if [[ -z "${random_ns}" ]]; then
            random_ns="$(echo "$out" | grep -iE 'idle latency' | grep -oE '[0-9]+\.[0-9]+' | head -n 1)"
        fi
        emit "{\"event\":\"mlc_idle_latency\",\"random_ns\":${random_ns:-null},\"sequential_ns\":${seq_ns:-null}}"
    else
        warn "mlc --idle_latency failed"
    fi
} || true

# --- Loaded latency ---------------------------------------------------------
{
    if out="$(mlc_run --loaded_latency)"; then
        # Output table looks like:
        #  Inject  Latency Bandwidth
        #  Delay   (ns)    MB/sec
        # ==========================
        #  00000   123.45   45678.90
        #  00002    ...
        echo "$out" | awk '
            BEGIN { in_table=0 }
            /^=+/ { in_table=1; next }
            in_table && NF>=3 && $1 ~ /^[0-9]+$/ {
                printf "{\"event\":\"mlc_loaded_latency\",\"inject_delay\":%d,\"latency_ns\":%s,\"bandwidth_mb_per_s\":%s}\n", $1+0, $2, $3
            }
        ' >> "$OUT"
    else
        warn "mlc --loaded_latency failed"
    fi
} || true

# --- Max bandwidth ----------------------------------------------------------
normalize_pattern() {
    # ALL Reads -> all_reads
    # 3:1 Reads-Writes -> 3_1_reads_writes
    # Stream-triad like -> stream_triad_like
    local s="$1"
    s="${s,,}"
    s="${s//:/ _ }"
    s="${s//-/ _ }"
    s="$(echo "$s" | tr -s ' ' '_' | sed 's/^_//; s/_$//; s/__*/_/g')"
    printf '%s' "$s"
}

{
    if out="$(mlc_run --max_bandwidth)"; then
        # Output looks like:
        #   ALL Reads        :  123456.78
        #   3:1 Reads-Writes :  111111.11
        #   2:1 Reads-Writes :  ...
        #   1:1 Reads-Writes :  ...
        #   Stream-triad like:  ...
        echo "$out" | grep -E ':[[:space:]]+[0-9]+\.[0-9]+' | while IFS= read -r line; do
            pattern="$(echo "$line" | sed 's/[[:space:]]*:[[:space:]]*[0-9.]\+.*$//' | sed 's/[[:space:]]*$//; s/^[[:space:]]*//')"
            bw="$(echo "$line" | grep -oE '[0-9]+\.[0-9]+' | tail -n 1)"
            norm="$(normalize_pattern "$pattern")"
            if [[ -z "$norm" || -z "$bw" ]]; then
                continue
            fi
            emit "{\"event\":\"mlc_max_bandwidth\",\"pattern\":\"${norm}\",\"bandwidth_mb_per_s\":${bw}}"
        done
    else
        warn "mlc --max_bandwidth failed"
    fi
} || true

# --- Bandwidth matrix -------------------------------------------------------
{
    if out="$(mlc_run --bandwidth_matrix)"; then
        # Output looks like:
        # Numa node
        # Numa node     0
        #        0   123456.7
        # On multi-node: a NxN grid with column header row then per-from-node rows.
        # Parse: find the header row of node indices, then each subsequent data row.
        echo "$out" | awk '
            BEGIN { header_seen=0; n=0 }
            /^[[:space:]]*Numa node[[:space:]]+[0-9]/ {
                # header row: "Numa node   0   1   2 ..."
                n = NF - 2
                for (i=0; i<n; i++) col[i] = $(i+3)
                header_seen=1
                next
            }
            header_seen && NF >= 2 && $1 ~ /^[0-9]+$/ {
                from = $1
                for (i=0; i<n; i++) {
                    val = $(i+2)
                    if (val ~ /^[0-9]+(\.[0-9]+)?$/) {
                        printf "{\"event\":\"mlc_bandwidth_matrix\",\"from_node\":%d,\"to_node\":%d,\"bandwidth_mb_per_s\":%s}\n", from+0, col[i]+0, val
                    }
                }
            }
        ' >> "$OUT"
    else
        warn "mlc --bandwidth_matrix failed"
    fi
} || true

# ----------------------------------------------------------------------------
# Done
# ----------------------------------------------------------------------------
END_EPOCH="$(date -u +%s)"
TS_END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DURATION=$(( END_EPOCH - START_EPOCH ))
emit "{\"event\":\"done\",\"ts\":\"${TS_END}\",\"duration_s\":${DURATION}}"

echo "JSONL written: ${OUT}" >&2
