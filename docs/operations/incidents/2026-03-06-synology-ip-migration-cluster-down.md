# Incident: Synology NAS IP Migration — Full Cluster Outage

**Date:** 2026-03-06
**Status:** Partially Resolved (iSCSI provisioning still blocked pending DSM host registration)
**Severity:** Critical — all iSCSI-backed PVCs Pending, ~50 pods unschedulable, Flux stuck
**Duration:** ~20+ hours (Flux divergence pre-dated detection; all PVCs Pending from IP change onward)
**Environments affected:** Production + Staging
**Authors:** Copilot

---

## Summary

The Synology NAS IP address changed from `192.168.5.8` to `10.42.2.21`. This single event
triggered a catastrophic cascade across the cluster: Flux GitRepository was already stuck 2
commits behind `master` (due to an unrelated SSH egress block), which meant the CSI driver
secret update that fixed the NAS IP had never been applied. All 42 iSCSI-backed PVCs entered
`Pending` state because the CSI driver could not reach the NAS at its old IP. NFS-backed PVs
for Jellyfin, Navidrome, and Immich had immutable `nfs.server` specs pointing to the old IP,
blocking the `apps-production` and `apps-staging` Flux kustomizations. Additionally, 64
orphaned LUNs accumulated on the NAS during the outage period.

---

## Affected Services

| Service | Environment | Impact |
|---|---|---|
| All iSCSI-backed apps | Production + Staging | All PVCs `Pending`; pods unable to start |
| Flux `flux-system` | Cluster | Stuck at `0eb93ae3`; 2 commits behind `master` |
| Flux `apps-production` | Cluster | Blocked by NFS PV immutable spec conflict |
| Flux `apps-staging` | Cluster | Blocked by NFS PV immutable spec conflict |
| Flux `infra-controllers` | Cluster | Failed: `Secret/client-info-secret namespace not specified` |
| Jellyfin | Production + Staging | NFS PVs pointing to old NAS IP |
| Navidrome | Production + Staging | NFS PVs pointing to old NAS IP |
| Immich | Production + Staging | NFS PVs pointing to old NAS IP |
| synology-csi | Cluster | CSI secret had old IP → provisioning broken |

---

## Root Cause Chain

This incident had four distinct, compounding root causes:

### Root Cause 1: Flux GitRepository SSH Egress Block (pre-existing)

Egress to `github.com:22` (SSH) was blocked from inside the cluster. The Flux
`flux-system` GitRepository used `ssh://git@github.com/...` with a deploy key.
With port 22 blocked, Flux could not pull new commits and remained pinned at
`0eb93ae3`, which was 2 commits behind `master`. This meant the NAS IP fix
(commit `766ab51`) — already merged — had never been applied to the cluster.

### Root Cause 2: Synology CSI Secret Had Old IP

The `infra/controllers/synology-csi/secret-client-info.yaml` SOPS secret had been
updated to `10.42.2.21` in commit `0eb93ae`. However, because Flux was stuck
(Root Cause 1), the live `client-info-secret` in the cluster still contained
`192.168.5.8`. The Synology CSI driver used this secret to connect to the NAS;
with the wrong IP, **all iSCSI volume provisioning failed**. All 42 pending PVCs
reported `ProvisioningFailed`.

### Root Cause 3: NFS PVs With Immutable Old IP

10 NFS PersistentVolumes (2× Immich, 6× Jellyfin, 2× Navidrome) had
`spec.nfs.server: 192.168.5.8` as an immutable field. Git had been updated to
`10.42.2.21` in a prior commit, so Flux tried to apply the updated PV specs.
Kubernetes rejects in-place mutation of `spec.nfs.server`, causing the
`apps-production` and `apps-staging` Flux kustomizations to block with:

```
PersistentVolume.v1 "immich-photos-pv-prod" is invalid:
  spec.nfs.server: Forbidden: field is immutable after creation
```

### Root Cause 4: `infra-controllers` Namespace Bug (pre-existing)

An unrelated bug in `infra-controllers` — the `client-info-secret` Secret was
missing a `namespace:` field — caused the kustomization to fail even after Flux
was unblocked. This was already fixed in commit `766ab51` but had never reached
the cluster (see Root Cause 1). Without `infra-controllers` reconciling, the
updated CSI secret could not be applied from git.

---

## Timeline

| Time | Event |
|---|---|
| ~2026-02-X | NAS IP changed from `192.168.5.8` → `10.42.2.21` |
| ~2026-02-X | `infra-controllers` fix committed (`766ab51`); CSI secret updated to new IP (`0eb93ae`) |
| ~2026-02-X | Flux blocks on SSH egress (port 22 to GitHub); never pulls new commits |
| ~2026-03-05 | Live CSI `client-info-secret` still has old IP; all New PVC provisioning fails |
| 2026-03-06 | Investigation begins; ~50 pods found Pending, all PVCs unbound |
| 2026-03-06 | **Root cause identified**: Flux stuck at `0eb93ae3` via SSH; CSI secret stale |
| 2026-03-06 | Flux GitRepositories patched from SSH → HTTPS; `secretRef` removed |
| 2026-03-06 | `flux reconcile source git flux-system` succeeds; Flux advances to `766ab51` |
| 2026-03-06 | `flux-system` kustomization reconciled; `infra-controllers` reconciled |
| 2026-03-06 | CSI secret manually applied (SOPS-decrypted, namespace-injected via kubectl) |
| 2026-03-06 | `synology-csi-controller` + `synology-csi-node` restarted; pods Reading new secret |
| 2026-03-06 | 10 NFS PVs (Immich ×2, Jellyfin ×6, Navidrome ×2) deleted via finalizer patch and recreated from git manifests with new IP |
| 2026-03-06 | All 10 NFS PVs re-bound to their PVCs |
| 2026-03-06 | `apps-production` + `apps-staging` kustomizations unblocked and reconciled |
| 2026-03-06 | PR #214 created to persist Flux HTTPS change |
| 2026-03-06 | `synology-tool audit`: 64 orphaned LUNs found (all `k8s-csi-pvc-*`, no matching K8s PVs) |
| 2026-03-06 | `synology-tool cleanup-luns`: 64 orphans deleted from NAS (64/64 OK) |
| 2026-03-06 | **Remaining blocker**: CSI `Couldn't find any host available to create Volume` — node IQN not registered in Synology DSM SAN Manager |

---

## Resolution

### Applied Fixes

#### Fix 1: Flux GitRepository SSH → HTTPS

The Flux `flux-system` GitRepository and `repo-staging` GitRepository were both
using SSH URLs with a deploy key. Port 22 egress to GitHub was blocked in-cluster.

Live patch:
```bash
kubectl patch gitrepository flux-system -n flux-system \
  --type=json -p '[
    {"op":"replace","path":"/spec/url","value":"https://github.com/gjcourt/homelab"},
    {"op":"remove","path":"/spec/secretRef"}
  ]'
kubectl patch gitrepository flux-system-staging -n flux-system \
  --type=json -p '[
    {"op":"replace","path":"/spec/url","value":"https://github.com/gjcourt/homelab"},
    {"op":"remove","path":"/spec/secretRef"}
  ]'
flux reconcile source git flux-system
flux reconcile source git flux-system-staging
```

Persisted in git: `clusters/melodic-muse/flux-system/gotk-sync.yaml` and
`clusters/melodic-muse/repo-staging.yaml` updated (PR #214, branch
`fix/flux-https-git-url`).

#### Fix 2: Synology CSI Secret Applied Manually

The SOPS-encrypted secret `infra/controllers/synology-csi/secret-client-info.yaml`
already contained the correct IP (`10.42.2.21`) in git, but had never been applied.
Decrypted and applied manually with namespace injection:

```bash
sops -d infra/controllers/synology-csi/secret-client-info.yaml \
  | perl -0777 -pe 's/(kind: Secret\n)/\$1metadata:\n  namespace: synology-csi\n/' \
  | kubectl apply -f -
kubectl rollout restart statefulset/synology-csi-controller -n synology-csi
kubectl rollout restart daemonset/synology-csi-node -n synology-csi
```

#### Fix 3: NFS PVs Recreated With New IP

10 NFS PersistentVolumes with immutable `spec.nfs.server: 192.168.5.8` were deleted
(with finalizer removal to force deletion) then recreated from the current git
manifests. Pattern for each PV:

```bash
kubectl patch pv <pv-name> -p '{"metadata":{"finalizers":null}}'
kubectl delete pv <pv-name> --grace-period=0 2>/dev/null || true
kubectl apply -f apps/<env>/<app>/nfs-*.yaml
```

PVs recreated:
- `immich-photos-pv-prod`, `immich-photos-pv-staging`
- `jellyfin-movies-pv-prod/staging`, `jellyfin-tvanime-pv-prod/staging`,
  `jellyfin-tvshows-pv-prod/staging`
- `navidrome-music-pv-prod`, `navidrome-music-pv-stage`

#### Fix 4: Orphaned LUN Cleanup

64 orphaned iSCSI LUNs (`k8s-csi-pvc-*`) with no matching K8s PVs were found and
deleted from the NAS. During this repair, the lun-manager was also updated to use
`/usr/local/bin/synoiscsiwebapi` instead of the removed `/usr/syno/bin/synoiscsitool`
(path changed in newer DSM versions), and now gracefully skips the unmap and
delete-target steps when a LUN has no associated target (TID empty).

```bash
cd scripts/synology/lun-manager
SYNOLOGY_HOST=10.42.2.21 SYNOLOGY_USER=manager SYNOLOGY_PASSWORD='...' \
  ./lun-manager audit           # 64 ORPHAN, 0 Bound, 0 Released
SYNOLOGY_HOST=10.42.2.21 SYNOLOGY_USER=manager SYNOLOGY_PASSWORD='...' \
  ./lun-manager cleanup --dry-run
SYNOLOGY_HOST=10.42.2.21 SYNOLOGY_USER=manager SYNOLOGY_PASSWORD='...' \
  ./lun-manager cleanup         # Deleted 64/64
```

### Outstanding Blocker: iSCSI CSI Provisioning

The CSI driver reports `Couldn't find any host available to create Volume` for all
new PVC provisioning attempts. This means the Kubernetes node's iSCSI initiator
IQN is not registered as a "Host" in the Synology DSM SAN Manager.

**Node IQN:** `iqn.2017-11.dev.talos:6343ec686596213800761170f72f363e`

**Manual action required in Synology DSM:**

1. Open DSM → SAN Manager → iSCSI → Hosts tab
2. Click **Add** → enter the node IQN above → save
3. After registering the host, restart synology-csi:

```bash
kubectl rollout restart statefulset/synology-csi-controller -n synology-csi
kubectl rollout restart daemonset/synology-csi-node -n synology-csi
```

4. Force reconcile PVCs to trigger reprovisioning:
```bash
flux reconcile kustomization apps-production apps-staging -n flux-system
```

---

## Impact

- **All iSCSI-backed pods**: Unable to start for 20+ hours. Zero data loss (PVs
  were not deleted, and the CSI provisioner was failing at new allocation, not at
  existing volume attachment for running apps).
- **NFS-backed apps** (Jellyfin, Navidrome, Immich media): NFS PVs were in `Lost`
  state due to IP mismatch, causing pod scheduling failures. Fully resolved.
- **Flux reconciliation**: Was 2 commits behind master, meaning any infrastructure
  changes in those commits (including the CSI IP fix) were not applied for an
  unknown duration (likely days to weeks).
- **Monitoring/observability**: Assumed degraded (kube-prometheus-stack, loki,
  vector HelmReleases in failed state) due to missing PVCs.
- **Persistent data**: Safe. No data loss for any app. All PVCs for existing
  running workloads remained attached.

---

## Code Changes

### Tracked in git (PR #214 — `fix/flux-https-git-url`)

| File | Change |
|---|---|
| `clusters/melodic-muse/flux-system/gotk-sync.yaml` | SSH URL → HTTPS; removed `secretRef` from GitRepository |
| `clusters/melodic-muse/repo-staging.yaml` | SSH URL → HTTPS; removed `secretRef` from GitRepository |

### Tracked in git (same branch, lun-manager fix)

| File | Change |
|---|---|
| `scripts/synology/lun-manager/main.go` | Updated `deleteLUN` to use `synoiscsiwebapi` (new DSM path); skip unmap/delete-target when TID is empty |

### Not tracked in git (imperative cluster state fixes)

| Action | Rationale |
|---|---|
| Manual `kubectl apply` of CSI secret | Bypassed Flux to unblock CSI while Flux was being fixed |
| NFS PV delete + recreate | Cannot patch immutable PV spec fields via `kubectl apply` |
| `kubectl rollout restart` synology-csi | Reload secret after manual apply |

---

## Detection & Alerting Gaps

This incident was not detected until manual inspection was triggered. The cluster
had no alerting for:

- Flux `GitRepository` falling behind master for >1 hour
- Flux kustomization `lastAppliedRevision` not matching `lastAttemptedRevision`
- PVC `Pending` for >30 minutes
- CSI provisioner error rate >0

The Flux SSH egress block likely occurred days or weeks before this was noticed.

---

## Action Items

| Priority | Item |
|---|---|
| **CRITICAL** | Register node IQN in Synology DSM SAN Manager → Hosts (unblocks all PVC provisioning) |
| HIGH | Merge PR #214 to persist Flux HTTPS change before anyone reverts the live patch |
| HIGH | Add PrometheusRule: alert when any Flux kustomization `lastAppliedRevision != lastAttemptedRevision` for >30m |
| HIGH | Add PrometheusRule: alert when PVC `Pending` for >30m |
| HIGH | Add PrometheusRule: alert when Flux GitRepository `lastFetchedRevision` has not changed for >2h |
| MEDIUM | Test egress to `github.com:443` and `github.com:22` from cluster; document in network policy |
| MEDIUM | Re-trigger failed HelmReleases once CSI provisioning is restored: `barman-cloud`, `cnpg`, `cilium`, `cert-manager`, `kube-prometheus-stack`, `loki`, `synology-csi`, `vector` |
| MEDIUM | Add a periodic `lun-manager audit` job (CronJob or manual runbook step) after any NAS maintenance |
| LOW | Investigate why node IQN was not pre-registered in DSM — was this lost during NAS migration/reconfiguration? |
| LOW | Document NAS IP change procedure: update CSI secret, NFS PVs, DSM host registration — as a runbook in `docs/operations/` |

---

## References

- [PR #214 — fix(flux): switch GitRepositories from SSH to HTTPS](https://github.com/gjcourt/homelab/pull/214)
- [infra/controllers/synology-csi/secret-client-info.yaml](../../../reference/controllers/synology-csi/secret-client-info.yaml)
- [scripts/synology/lun-manager/main.go](../../scripts/synology/lun-manager/main.go)
- [docs/operations/incidents/2026-02-15-iscsi-targets-disabled.md](2026-02-15-iscsi-targets-disabled.md)
- [docs/operations/incidents/2026-02-28-iscsi-mass-readonly-cnpg-loki-immich.md](2026-02-28-iscsi-mass-readonly-cnpg-loki-immich.md)
- [docs/operations/synology-iscsi-operations.md](../synology-iscsi-operations.md)
