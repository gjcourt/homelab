# hestia iSCSI storage maintenance (safe restart procedure)

## Purpose

`hestia` (TrueNAS, `10.42.2.10`) is the **single iSCSI target backing every RWO PVC in the
cluster** — 36 namespaces, including all CNPG databases and the monitoring TSDB. When the target
goes away mid-I/O (reboot, upgrade, disk recovery, crash), the Talos clients do the correct thing
and remount the ext4 volumes **read-only** (`errors=remount-ro`). Stateful pods then wedge —
CrashLooping on a read-only filesystem — until they are bounced to force a clean re-attach.

This is **structural**, not a TrueNAS-version bug (a stable release does not fix it — see
[#1080](https://github.com/gjcourt/homelab/issues/1080)). The only way to make a hestia restart
non-disruptive is to **quiesce I/O to the iSCSI volumes before the target goes away**, then bring
workloads back after it returns. This runbook is that procedure.

> For **unplanned** hestia restarts (crash), skip to [§9 Reactive recovery](#9-reactive-recovery-if-you-did-not-drain-first).

## When to use

Any **planned** hestia event that restarts the box or the iSCSI (`scst`) service: OS upgrade,
pool/disk maintenance, hardware work, a scheduled reboot. If in doubt, drain — it is cheap
relative to a cluster-wide read-only wedge.

## Background — why draining works

- iSCSI is block storage; the client owns the ext4 filesystem. `errors=remount-ro` is a safety
  default that prevents corruption when writes start failing.
- Stopping the pods stops the in-flight I/O, so when the target vanishes there is nothing to fail
  read-only. On return, a fresh pod gets a clean read-write attach.
- CNPG Postgres **must not** be left running against a disappearing volume — hibernate it (clean
  shutdown) rather than letting it hit a read-only WAL.

---

## 1. Pre-flight

```bash
# Confirm what will be affected (namespaces with iSCSI PVCs)
kubectl get pvc -A -o json | jq -r '.items[] | select(.spec.storageClassName|test("iscsi")) | .metadata.namespace' | sort | uniq -c

# List CNPG clusters (these get hibernated, not scaled)
kubectl get cluster -A

# Snapshot current replica counts so restore is exact (keep this output!)
kubectl get deploy,statefulset -A -o json | jq -r '.items[] | "\(.kind) \(.metadata.namespace)/\(.metadata.name) \(.spec.replicas)"' | sort > /tmp/hestia-maint-replicas.txt
cat /tmp/hestia-maint-replicas.txt
```

Decide on **monitoring**: Prometheus/Alertmanager TSDB is on iSCSI (`monitoring` ns, 3 PVCs).
Draining it means going blind for the window; leaving it up risks its PVC going read-only. Default:
**leave monitoring up** and accept it may need a bounce afterward (it is not data-critical) — but if
the window is long, drain it too.

## 2. Suspend Flux (so it doesn't fight the scale-down)

```bash
flux suspend kustomization apps-production apps-staging -n flux-system
flux suspend kustomization infra-controllers -n flux-system   # zigbee2mqtt lives here
flux get kustomizations -A | grep -iE 'apps-|infra-controllers'   # confirm SUSPENDED=True
```

## 3. Hibernate CNPG databases (clean Postgres shutdown)

Requires the `cnpg` kubectl plugin. For **each** cluster from §1 (`kubectl get cluster -A`):

```bash
kubectl cnpg hibernate on <cluster-name> -n <namespace>
# verify: pods gone, PVCs detached
kubectl -n <namespace> get pods,cluster
```

(See [`2026-05-02-cnpg-backup-recovery.md`](./2026-05-02-cnpg-backup-recovery.md) for CNPG tooling.)

## 4. Scale down the remaining iSCSI-backed workloads

```bash
# Scale every Deployment/StatefulSet that mounts an iSCSI PVC to 0.
# (Run the print first; sanity-check the list before piping to xargs.)
for ns in $(kubectl get pvc -A -o json | jq -r '.items[] | select(.spec.storageClassName|test("iscsi")) | .metadata.namespace' | sort -u); do
  for wl in $(kubectl -n "$ns" get deploy,statefulset -o name 2>/dev/null); do
    echo "kubectl -n $ns scale $wl --replicas=0"
  done
done
# review, then re-run piping the echoed lines to `sh`, OR scale namespace-by-namespace.

# Wait until no pods hold an iSCSI mount:
kubectl get pods -A --field-selector=status.phase=Running -o wide | grep -vE 'kube-system|cilium|flux-system'
```

Give it a minute; confirm the target has no active sessions from workload pods:

```bash
ssh truenas_admin@10.42.2.10 'sudo -n dmesg -T | grep -i "iscsi.*session" | tail'
```

## 5. Restart hestia

Do the maintenance (reboot / upgrade / disk work). Nothing in the cluster is writing to iSCSI now,
so the target can disappear safely.

## 6. Post-restart — verify the target before bringing workloads back

```bash
ssh truenas_admin@10.42.2.10 'uptime; sudo -n systemctl is-active scst; sudo -n dmesg -T | grep -i iscsi | tail'
# democratic-csi + iSCSI monitor healthy:
kubectl -n democratic-csi get pods
kubectl -n truenas-iscsi-monitor get pods
# no read-only mounts asserted:
kubectl get prometheusrule -n monitoring homelab-infrastructure-alerts -o yaml | grep -A2 NodeFilesystemReadOnly  # (should not be firing in Alertmanager)
```

## 7. Restore (reverse order)

```bash
# 7a. Un-hibernate CNPG (per cluster from §3)
kubectl cnpg hibernate off <cluster-name> -n <namespace>

# 7b. Resume Flux — it restores desired replicas from Git
flux resume kustomization infra-controllers apps-production apps-staging -n flux-system
flux reconcile kustomization apps-production -n flux-system

# 7c. For anything Flux does NOT own the replica count of, restore from the snapshot:
#     compare against /tmp/hestia-maint-replicas.txt and `kubectl scale` back up as needed.
```

## 8. Verification checklist

- [ ] `kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded` → only expected
- [ ] Every CNPG `cluster` reports healthy (`kubectl get cluster -A`), primary + replicas Ready
- [ ] `flux get kustomizations -A` all Ready, none SUSPENDED
- [ ] No `NodeFilesystemReadOnly` / `ContainerCrashLoopBackOff` alerts in Alertmanager
- [ ] Spot-check a stateful app writes OK (e.g. AdGuard saves a setting, z2m controls a device)
- [ ] Replica counts match `/tmp/hestia-maint-replicas.txt`

---

## 9. Reactive recovery (if you did NOT drain first)

An **unplanned** hestia restart (or a forgotten drain) leaves RWO PVCs read-only and pods
CrashLooping. Recovery is a clean re-attach per affected workload once hestia is back:

```bash
# Identify wedged pods (CrashLoopBackOff / read-only errors)
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded

# For each affected Deployment/StatefulSet, force a clean iSCSI detach+attach:
kubectl -n <ns> scale deploy/<name> --replicas=0
# wait for the pod to fully terminate (clean detach), then:
kubectl -n <ns> scale deploy/<name> --replicas=1
```

- **zigbee2mqtt** ([#1176](https://github.com/gjcourt/homelab/pull/1176)) now fails its init
  container fast with the exact recovery command in its logs instead of burning hundreds of
  restarts — check `kubectl -n zigbee2mqtt logs <pod> -c init-config`.
- **CNPG** clusters: if a Postgres pod is stuck read-only, delete the pod (the operator reattaches);
  if the primary won't recover, see the CNPG runbook.
- z2m lives under `infra-controllers` (with `wait: true`), so a stuck z2m can stall the Flux infra
  chain — recovering z2m unblocks it.

## Related

- [#1080](https://github.com/gjcourt/homelab/issues/1080) — the underlying resilience gap (single
  iSCSI target; RWO PVCs remount read-only on any target restart). Future structural options:
  auto-recovery controller (watch `NodeFilesystemReadOnly` → bounce affected pods), or replicated
  storage (Longhorn) to remove the single-target SPOF.
- [`2026-06-17-talos-node-maintenance.md`](./2026-06-17-talos-node-maintenance.md) — node-level maintenance.
- [`2026-05-02-cnpg-backup-recovery.md`](./2026-05-02-cnpg-backup-recovery.md) — CNPG hibernate/restore.
