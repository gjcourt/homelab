---
title: Cluster gotchas — Talos, kubectl, Flux
status: Stable
created: 2026-08-15
updated: 2026-08-15
updated_by: gjcourt
tags: [operations, talos, kubectl, flux, gotchas]
---

# Cluster gotchas — Talos, kubectl, Flux

Node topology, kubectl selector traps, and Flux behaviours that differ from what the manifests imply. Promoted 2026-08-15.

---

## talos shelf correlated failure

**melodic-muse node physical topology + the correlated-failure trap behind the 2026-06-18 quorum-loss outage**

melodic-muse (Talos cluster) physical layout, learned during the 2026-06-18 maintenance:
**`.22`+`.23` share one shelf; `.24`+`.25` share another; `.20`/`.21` are separate.**
4 independent PSUs, but the shelf mechanically couples its two nodes (pulling the shelf to
service one node takes both down). `.21` and `.25` also share a switch/power path with the
maintenance shelves.

**Why:** During the `.22` DIMM work, an action on the shared switch/power path dropped
`.21` (a control-plane survivor that was supposed to be untouched) **and** `.25` at once —
not 5 independent failures, one infra fault. With `.22` already down, losing `.21` left
etcd at 1/3 → **full control-plane outage** (`etcdserver: no leader`, apiserver `etcd failed`).
The "2/3 quorum is safe" assumption was wrong because the survivors weren't failure-independent.

**How to apply:** During any cluster op, treat `.20`/`.21` **and the switch/PSU/shelf they
share** as off-limits — confirm survivor independence before relying on quorum. Don't uncordon
maintenance nodes until the operator confirms physical work is fully done and nodes are staying
up (mid-work uncordon churned workloads onto `.23`, which then dropped again). See
[[feedback_flux_suspend_during_cluster_ops]], [[project_homelab_cluster]].

---

## talos sysfs powercap mask

**Talos/containerd masks /sys/devices/virtual/powercap with an empty tmpfs; reading RAPL in a pod needs a non-/sys hostPath mount + walking the device tree, not /sys/class symlinks**

Reading **RAPL energy** (`/sys/class/powercap/intel-rapl:*/energy_uj`) from a pod on the Talos cluster fails in a non-obvious way: containerd stacks an **empty read-only tmpfs over `/sys/devices/virtual/powercap`** (default sysfs masking). The `/sys/class/powercap/intel-rapl:*` entries are **relative symlinks** into `../../devices/virtual/powercap/...`, so they resolve *through* the mask → ENOENT for both shell `cat` and Go `os.ReadFile` inside the long-running pod.

**Why it's a trap:** a fresh single-mount busybox probe reads RAPL fine (its mount overlays the mask), so manual verification passes while the deployed agent gets nothing. Diagnosed live on thermalscope 2026-06-16 after the collector showed `power_up=1` (ReadDir of the class dir succeeds) but **zero energy series** (every per-domain read returned empty).

**How to do it right (works):**
- Mount the hostPath `/sys/devices/virtual/powercap` at a **non-`/sys` path** in the container (e.g. `/host/sys/powercap`). Anything remounted under `/sys/...` gets re-masked.
- Point the collector at that path (thermalscope: `THERMALSCOPE_POWERCAP_ROOT=/host/sys/powercap`) and **walk the device tree** (real dirs, nested `intel-rapl/intel-rapl:0/` = package-0, `.../intel-rapl:0:0/` = core), not the flat `/sys/class/powercap` symlinks. De-dup domains by `name` so the `intel-rapl-mmio` mirror doesn't double-count.
- RAPL `energy_uj` is **root-gated** (Platypus mitigation); the agent must run `runAsUser: 0` (node-exporter runs non-root, which is why `node_rapl_*` is absent in the cluster — verified). Expose energy as a monotonic counter; `rate()/1e6` = watts. Verified sane on the APU nodes: package-0 ≈ 2–6 W.

**Why it matters:** cost a 3-round deploy-verify saga. Contrast hwmon, which works with a plain `/sys/class/hwmon` mount because its symlink targets are *real PCI devices* the container's base sysfs exposes — only the *virtual* powercap subtree is masked. Same family of "verify on the real node" lesson as [[feedback_homelab_lan_access]]; the netscope BPF lockdown bug the same day is the analog for tracing programs.

---

## kubectl selector notin pitfall

**A `--selector='key notin (a,b,c)'` matches pods that have the key with any other value AND pods that DON'T HAVE THE KEY AT ALL. Will sweep up unrelated pods (e.g., CNPG postgres pods that don't carry the `job-name` label) on broad delete operations.**

The Kubernetes label-selector grammar for `notin` evaluates against the key's value, but the special case of "pod doesn't have the key at all" is also treated as "not in the listed values." Result: a delete with `--selector='job-name notin (restore-foo, restore-bar)'` intended to clean squatter Deployment pods will ALSO delete every CNPG / StatefulSet / cron-job-created pod in the namespace that has no `job-name` label whatsoever.

**Why:** Burned by this in the alcatraz → hestia preserve-7 migration (2026-05-23). Wrote a broad `notin (restore-*)` selector to force-delete app pods that had grabbed the freshly-recreated PVCs (see [[flux-suspend-during-cluster-ops]]). Accidentally killed `immich-db-staging-cnpg-v1-5` and `v1-6` postgres pods because they didn't have a `job-name` label. CNPG operator recovered them via rebootstrap, but it was an avoidable 2-minute outage for the cluster's primary postgres.

**How to apply:**

Prefer the **positive selector** that lists exactly what you want to delete:

```bash
# Bad: sweeps everything in the namespace without job-name label
kubectl delete pods -n <ns> --selector='job-name notin (restore-foo)' --force --grace-period=0

# Good: explicit `in` set
kubectl delete pods -n <ns> --selector='app in (mealie, audiobookshelf, jellyfin)' --force --grace-period=0

# Also good: name-targeted
kubectl delete pod -n <ns> <specific-pod-name> --force --grace-period=0
```

If you must use `notin` (e.g., "delete everything except the migration Jobs"), add a second selector that restricts the result to a known label class:

```bash
# Restrict notin to pods that have the `app` label set at all
kubectl delete pods -n <ns> --selector='app,job-name notin (restore-foo)' --force --grace-period=0
```

The bare `app` term means "has the key" — so the selector now matches only pods that have an `app` label AND a job-name not in the exclusion list. CNPG/STS pods without `app` are excluded.

**Same pitfall in other Kubernetes APIs:**
- `kubectl get pods --selector='env notin (prod)'` includes pods without `env` set
- `client-go` LabelSelector with `Operator: NotIn` has the same semantics
- Cilium NetworkPolicy `endpointSelector` with `NotIn` — same gotcha

**Related cross-references:**
- [[flux-suspend-during-cluster-ops]] — the upstream cause; suspending Flux would have prevented the squatter-pod problem and made the broad selector unnecessary

---

## flux suspend during cluster ops

**Before delete-and-recreate PVC migrations, suspend the affected Flux Kustomization. Otherwise Flux re-applies the Deployment spec mid-flight and the workload pod grabs the new PVC before your data-restore Job can mount it (Multi-Attach error).**

When a migration plan involves "scale workload to 0 → delete PVC → wait for recreation → run rsync data-restore Job → scale workload back up," Flux's default 10-minute reconciliation interval will race with the manual steps. The reconciler sees `replicas: 1` in the source manifest and scales the deployment back up, the workload pod grabs the freshly-recreated PVC (RWO), and your restore-rsync Job is stuck in `ContainerCreating` with `Multi-Attach error for volume X: Volume is already used by pod(s) <app-pod>`.

**Why:** Hit this in production during the alcatraz → hestia preserve-7 migration (2026-05-23). Six of seven restore-rsync Jobs sat in ContainerCreating for 21 minutes because Flux scaled the deployments back up after I'd scaled them to 0. Cost: a manual force-delete sweep that accidentally killed CNPG postgres pods too (see [[kubectl-selector-notin-pitfall]]).

**How to apply:**

Before starting any migration that involves deleting+recreating PVCs, suspend the Flux Kustomization that manages those workloads:

```bash
# Suspend
flux suspend kustomization apps-staging -n flux-system

# Do the migration work: scale to 0, delete PVCs, restore data, etc.

# Resume only after restore Job has bound the dest PVC AND finished
flux resume kustomization apps-staging -n flux-system
```

Same pattern for `apps-production` when doing Phase 1.2 prod migrations. Be explicit about which Kustomization owns which workloads — `flux get kustomizations -A` shows the mapping.

**When NOT to suspend:**
- If the migration is short enough (< 1 minute end-to-end) and the Kustomization's reconcile interval is longer (default 10m), the race window may be small enough to skip suspension. But "small enough" is a guess; suspend is cheap insurance.
- If the migration touches resources outside the Kustomization's scope (e.g., a manual PVC not in the manifests), suspension may be unnecessary — Flux won't re-create what it doesn't know about.

**Related cross-references:**
- [[pvc-storage-class-migration]] — the underlying migration pattern that triggers this race
- [[cnpg-promote-pg-resetwal]] — CNPG-specific recovery that ALSO benefits from suspending the operator's reconcile loop (`cnpg.io/reconciliationLoop=disabled` annotation)

---

## flux pvc volumename anti pattern

**Don't use `volumeName` in the spec of Flux-managed PVCs to bind to a specific PV. Flux SSA tries to unset volumeName because the manifest doesn't include it, producing 'spec is immutable' errors. Use PV `claimRef` pre-binding instead.**

When statically binding a PVC to an existing PV (e.g., after a PV-Retain recovery or storage migration), there are two binding patterns:

1. **PVC-side**: set `pvc.spec.volumeName: <pv-name>` — works for kubectl-applied PVCs
2. **PV-side**: set `pv.spec.claimRef: {namespace, name}` (no UID) — binds the PV to the future PVC of that name

**For Flux-managed PVCs, only the PV-side `claimRef` pattern works.**

**Why:** Burned on this in the alcatraz → hestia migration (2026-05-24). After migrating 13 production preserve PVCs, I rebound each PVC via `kubectl apply` with `volumeName: pvc-XXX` set. Flux's strategic-merge-patch dry-run then showed:

```
- "VolumeName": "pvc-XXX-truenas",
+ "VolumeName": "",
```

Flux's manifest doesn't include volumeName, so its SSA computes "would set to empty string." Kubernetes rejects with `spec is immutable after creation`. Flux Kustomization stays `ReconciliationFailed` indefinitely.

**How to apply:**

When you need to bind a Flux-managed PVC to a pre-existing PV:

```bash
# 1. Reset the PV's claimRef (remove old UID so binding is "wildcard")
kubectl patch pv $PV --type=json -p '[{"op":"remove","path":"/spec/claimRef"}]'
sleep 2

# 2. Set claimRef to namespace + name only (no UID)
kubectl patch pv $PV --type=merge -p '{
  "spec": {
    "claimRef": {
      "apiVersion": "v1",
      "kind": "PersistentVolumeClaim",
      "namespace": "'$NS'",
      "name": "'$PVC'"
    }
  }
}'

# 3. Apply the PVC manifest WITHOUT volumeName (let Flux's manifest stand)
kubectl apply -f - <<YAML
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: $PVC
  namespace: $NS
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: <whatever the manifest says>
  resources:
    requests:
      storage: $SIZE
YAML
# The binder will see the PV's matching claimRef and bind without volumeName being set by the manifest.
```

**Why the patch-then-remove dance:** if you only patch `claimRef.uid: null` on a PV that has a stale UID, strategic merge keeps the old UID — the binder rejects the bind silently and the storageclass provisioner provisions a fresh empty PV (DATA LOSS risk). Remove claimRef entirely first.

**Storage-class admission gotcha:** If your manifest has no `storageClassName`, the default-storage-class admission plugin sets it to the cluster's default at PVC creation time. The resulting cluster PVC has `storageClassName: synology-iscsi` (or whatever default) even though the manifest had nothing. If a later Flux apply has a different value (or null), it's a strategic-merge-patch mismatch. **Best practice: always pin `storageClassName` explicitly in PVC manifests** to avoid drift between cluster state and Git.

**Related cross-references:**
- [[audit-pvc-before-lossy-destroy]] — verify before destroying so you don't need this recovery
- [[pv-retain-recovery-pattern]] — the lower-level mechanism; this is the Flux-compatible variant
- [[pvc-storage-class-migration]] — the higher-level migration pattern that triggers the need to rebind

---

## flux ga no reconcile condition

**GA Flux controllers don't emit gotk_reconcile_condition; use kube-state-metrics gotk_resource_info for readiness alerts**

GA Flux controllers (helm-controller v1.x, kustomize-controller v1.x, source-controller v1.x on melodic-muse) **do not expose the `gotk_reconcile_condition` gauge** — it was removed from the v2 GA line. The controllers emit `gotk_reconcile_duration_seconds`, `gotk_event_http_*`, `controller_runtime_*`, but nothing carrying per-object Ready state. Any alert rule referencing `gotk_reconcile_condition` is inert (loads as `health=ok` but never fires — no series to match).

**Per-object readiness comes from kube-state-metrics instead** (the upstream `flux2-monitoring-example` pattern): a `customResourceState` config makes ksm emit `gotk_resource_info{customresource_kind, exported_namespace, name, ready, suspended, revision}` (an Info gauge, value always 1). Alert on `gotk_resource_info{customresource_kind="Kustomization", ready="False"} == 1`. Note the namespace label is **`exported_namespace`**, not `namespace`.

Implemented in homelab PRs #937 (PodMonitor for controller metrics) + #940 (ksm CRS config + rewritten rules). Config lives in `infra/controllers/kube-prometheus-stack/values.yaml` under `kube-state-metrics.customResourceState` + `rbac.extraRules` (list/watch on the Flux API groups). Served CRD versions when built: Kustomization v1, HelmRelease v2, GitRepository v1 — verify with `kubectl get crd <x> -o jsonpath='{.spec.versions[?(@.served==true)].name}'` before pinning. This was Phase 3 of the homelab monitoring-enhancement plan (`docs/plans/2026-05-09-monitoring-enhancement.md`). Relates to [[feedback_verify_api_versions]].

---
