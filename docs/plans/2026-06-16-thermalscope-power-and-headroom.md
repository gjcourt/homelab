---
status: in-progress
last_modified: 2026-06-17
summary: "thermalscope: add power/energy/cost (RAPL), thermal headroom, and throttle/degradation signals — phases 1–3 live; phase 4 pending"
---

# thermalscope — Power, Energy, Headroom & Degradation

thermalscope today measures **heat** (CPU/NVMe/AMD-iGPU temps, fans, GPU power) well. The gaps
are **energy/cost**, **headroom** (how close to throttle), and **degradation** (cooling getting
worse over time). This plan adds those, scoped strictly to what the real nodes expose.

## Execution status (2026-06-17)

- **Phase 1 — Power & Energy:** ✅ live. RAPL package+core energy (per-node watts), hwmon SoC watts,
  recording rules (watts, kWh/day), and a dashboard "Power & Energy" row with a $/month cost stat
  (`cost_per_kwh` set to the real PG&E blended rate, 0.45). Root cause of an early failure —
  containerd masks `/sys/devices/virtual/powercap`; fixed by walking the device tree via a non-`/sys`
  mount (thermalscope PR #5).
- **Phase 2 — Headroom & throttle:** ✅ live. NVMe headroom from sysfs crit/max, CPU headroom via a
  Tjmax constant, throttle inference reusing node-exporter cpufreq; alerts + dashboard row.
- **Phase 3 — Degradation & fans:** ✅ live. CPU-utilization/disk-busy recording rules (re-keyed
  across exporters via `node_uname_info`), temp-vs-load + 7d-drift panels, fan-failing alert, fan
  curve. Plus a UniFi PDU per-outlet wall-power row (real per-device draw + whole-system cost).
- **Phase 4 — SMART/HDD temps, workload correlation, drop vestigial `/sys/class/thermal` mount:**
  ⏳ pending. SMART needs a smartctl exporter on hestia (operator-gated TrueNAS Custom App).

## Hardware feasibility — VERIFIED on `talos-18u-ski` (2026-06-16)

A privileged root probe of the actual node sysfs (not assumptions — this is the lesson from the
netscope BPF saga: confirm on real hardware before planning):

| Interface | Result | Consequence |
| :--- | :--- | :--- |
| `/sys/class/powercap/intel-rapl:0/energy_uj` (package-0) | **readable as root**, 31.9 GJ counter | ✅ RAPL power/energy is the headline feature |
| `/sys/class/powercap/intel-rapl:0:0/energy_uj` (core) | **readable**, 27.8 GJ | ✅ per-domain (package vs core) breakdown |
| `/sys/class/hwmon/*/power1_input` (amdgpu) | **32 W** instantaneous | ✅ secondary power source |
| NVMe `temp{1,2,3}_crit` / `_max` | **present** | ✅ NVMe headroom from sysfs thresholds |
| `cpufreq/scaling_cur_freq` + `cpuinfo_max_freq` | cur 4.12 / max 4.25 GHz | ✅ throttle inference via freq ratio |
| `/sys/class/thermal/thermal_zone*` | **NONE** | ❌ no trip-point headroom; daemonset mount is vestigial |
| k10temp / amdgpu `temp*_crit` | **none** | ⚠️ CPU/GPU headroom needs config Tjmax, not sysfs |
| Intel `core_throttle_count` | **absent** (AMD) | ❌ no throttle counters; use freq ratio |

RAPL `energy_uj` requires root — thermalscope already runs `runAsUser: 0`, so this works under
Talos lockdown (RAPL reads are root-gated by the Platypus mitigation, which we satisfy).

## Review findings — checked against live Prometheus (2026-06-16)

Verified what `kube-prometheus-stack` node-exporter already exposes, to avoid duplicating it:

| Metric | Present? | Decision |
| :--- | :--- | :--- |
| `node_rapl_package_joules_total` | **absent** | node-exporter runs non-root; RAPL is root-gated under lockdown so its `rapl` collector yields nothing. **thermalscope (root) is genuinely needed** — Phase 1 is not redundant. |
| `node_cpu_scaling_frequency_hertz` | **present (72 series)** | **Do NOT add a cpufreq metric to thermalscope.** Phase 2 throttle inference uses node-exporter's existing series; thermalscope contributes only Tjmax constants + headroom rules. |
| `node_hwmon_power_average_watt` | **absent** | thermalscope surfacing hwmon `power1_input` is non-redundant. |
| `thermalscope_cpu_temperature_celsius` | present (11 series) | thermalscope healthy; build on it. |

## Repos touched

- **thermalscope** (`~/src/thermalscope`) — new collectors (Go), following the existing
  `internal/hwmon` + `internal/gpu` collector pattern (one package per source, `prometheus.Collector`,
  table-driven tests with a fake sysfs root).
- **homelab** — dashboard panels (`infra/configs/dashboards/thermalscope-cm.yaml`), recording rules
  + alerts (`infra/configs/thermalscope/prometheus-rule.yaml`), image bump.

## Phase 1 — Power & Energy (the headline) ⭐

**Collector** `internal/power`:

- Read every `/sys/class/powercap/intel-rapl:*/` domain: `name` (package-0/core), `energy_uj`,
  `max_energy_range_uj` (for wraparound).
- Expose `thermalscope_rapl_energy_microjoules_total{domain}` as a **counter** (monotonic; on
  wraparound add `max_energy_range_uj`). Watts is derived at query time with `rate()` — never
  compute the rate in the agent.
- Also surface hwmon `power1_input` as `thermalscope_power_watts{chip}` (instantaneous gauge).
- `thermalscope_power_up` health metric, mirroring the hwmon/gpu collectors.

**Recording rules** (`infra/configs/thermalscope/`):

- `instance:thermalscope_power_watts = rate(thermalscope_rapl_energy_microjoules_total{domain="package-0"}[5m]) / 1e6`
- `instance:thermalscope_energy_kwh_daily = increase(...[24h]) / 3.6e12`
- cost via a config constant (`$/kWh`) — a recording rule or a Grafana variable.

**Dashboard** — new "Power & Energy" row: live watts per node, stacked package-vs-core, **kWh/day**,
and a **$/month cost** stat. This is the single most compelling missing view.

**Alert** (optional): node power budget exceeded for 15m.

## Phase 2 — Headroom & throttle (adjusted to verified reality)

- **NVMe headroom** (sysfs-backed): expose `thermalscope_nvme_temperature_threshold_celsius{device,sensor,level="crit"|"max"}`
  from the present `temp*_crit`/`temp*_max`. Dashboard panel: `crit − current` per drive; alert on
  headroom < 5 °C (portable across drives, unlike the hardcoded `>65` today).
- **CPU/GPU headroom** (config Tjmax, since sysfs has no crit): a small per-chip threshold map
  (k10temp Tjmax, amdgpu junction max) in the agent config or as a recording-rule constant.
- **Throttle inference:** reuse node-exporter's existing `node_cpu_scaling_frequency_hertz` and
  `node_cpu_scaling_frequency_max_hertz` (both already scraped — do not duplicate in thermalscope);
  alert/panel when `cur/max < 0.85` *while* thermalscope temp is high (real throttling, not a fixed
  temp threshold). thermalscope adds only the per-chip Tjmax constants for the headroom calc.

## Phase 3 — Degradation & correlation (the predictive angle)

- **Temp-at-constant-load trend:** recording rule joining `thermalscope_*_temperature_celsius` with
  CPU/IO utilization (node-exporter) — surface "this NVMe runs N °C hotter than 30 days ago at the
  same load/ambient." Predictive-maintenance signal (dust, dried paste, failing fan).
- **Fan health:** RPM-below-floor-while-temp-rising alert (data already collected).
- **Fan curve panel:** RPM vs temp scatter (data already collected; viz only).

## Phase 4 — Smaller adds (opportunistic)

- SATA/HDD SMART temps (smartctl) for TrueNAS spinning disks — currently NVMe-only.
- Workload correlation: annotate thermal spikes with the causing workload (link to netscope / pod
  events).
- Remove the vestigial `/sys/class/thermal` mount from the daemonset (no zones exist).

## Rollout & verification

1. Phase 1 collector + tests in thermalscope → PR → CI → image.
2. **Verify the new metrics actually populate on a Talos node** before wiring dashboards — run the
   agent (or `cmd/cismoke`-style check) on a real node; confirm `energy_uj` deltas produce sane watts
   (sanity: package-0 should read tens of watts for these APUs, matching the 32 W amdgpu reading).
3. homelab: image bump + recording rules + dashboard row + alert → staging → verify panels render →
   production.
4. Each phase ships independently; Phase 1 is self-contained and the highest value.

## Non-goals

- No per-process power attribution (needs eBPF/RAPL-perf; out of scope).
- No control-plane (thermalscope observes; it never sets fan curves or power limits).
- No thermal_zone work — they don't exist on these nodes.

## Risks

| Risk | Mitigation |
| :--- | :--- |
| RAPL counter wraparound mishandled → negative rates | Track `max_energy_range_uj`; expose as counter and let PromQL `rate()` handle resets |
| RAPL root-gating changes under a future Talos lockdown tightening | Agent already root; `power_up=0` + alert if reads start failing |
| Cost constant drift ($/kWh) | Single source (one recording rule / Grafana var), documented |
| Per-chip Tjmax guesses wrong | Conservative defaults; headroom is advisory, NVMe headroom is sysfs-exact |
