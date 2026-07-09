---
status: planned
last_modified: 2026-06-28
summary: "Migrate democratic-csi from the SSH-based driver to the API driver (freenas-api-iscsi) to remove the SSH+sudo dependency"
---

# Democratic-CSI: Migrate from the SSH driver to the API driver (`freenas-api-iscsi`)

## Context

democratic-csi (ns `democratic-csi`, CSI driver name `org.democratic-csi.truenas-iscsi`) is
configured with `driver: truenas-iscsi`, which resolves at runtime to the **`FreeNASSshDriver`**
class. On **TrueNAS Scale**, that driver's `ControllerExpandVolume` shells over SSH (as Unix user
`truenas_admin`) and runs a **`sudo`** sysfs write to reload the SCST extent size:

```
echo 1 > /sys/kernel/scst_tgt/devices/<name>/resync_size   # via: sudo sh -c ...
```

Because `truenas_admin` lacked passwordless sudo, **every PVC expansion failed cluster-wide**
(`sudo: a password is required`). This blocked `storage-loki-0` (40→80Gi) for ~24h on 2026-06-28.
The apiVersion-2 `POST /service/reload` branch only runs on **non-Scale** systems, so on Scale the
SSH driver *always* takes the sudo'd sysfs path — the bug is structural to the SSH driver here.

**Interim mitigation (applied 2026-06-28):** granted `truenas_admin` passwordless sudo
("Allow all sudo commands with no password"). This unblocks expansion immediately and is **fully
reversible** — it should be **reverted once this migration lands** (the API driver needs no SSH).

The **API driver** (`freenas-api-iscsi` → `FreeNASApiDriver`) performs all operations over the
TrueNAS middleware API via the existing `httpConnection` (api-proxy + the `melodic-muse-csi` API
key) — **no SSH, no sudo**. This is the correct end state and the natural completion of the
least-privilege work in [`2026-05-09-democratic-csi-least-privilege-key.md`](./2026-05-09-democratic-csi-least-privilege-key.md).

## Goal

Swap `driver: truenas-iscsi` → `freenas-api-iscsi` so the driver no longer SSHes/sudos to TrueNAS
(**api-key-only auth**), **without re-provisioning any of the 170 existing PVs**, then remove the
`sshConnection` and revert the temporary passwordless sudo.

### Scope note (learned during the 2026-06-28 Loki incident)

This migration removes the **controller-side** SSH/sudo dependency only. It does **not** fix
**node-side** filesystem expansion: on Talos, the node plugin's *online* `resize2fs` fails with
`Permission denied` (EPERM) **even though the node plugin is privileged + SYS_ADMIN** — a separate
kernel/mount-namespace issue that breaks PVC *growth* regardless of SSH-vs-API driver. So:
- Do **not** expect this swap to make `kubectl patch pvc <grow>` work end-to-end on its own.
- The Loki PVC-full incident was ultimately resolved by **recreating** the PVC (fresh volume) +
  planning to cut log ingestion to fit retention — not by expansion.
- Track the node-side `resize2fs` EPERM as a **separate** follow-up (likely a democratic-csi node
  strategy / capability fix for Talos).

The value of *this* migration is **security/auth** (least-privilege api-key, no SSH key, no sudo,
no `truenas_admin` shell access) — not expansion.

## Blast radius

**170 PVs** on `org.democratic-csi.truenas-iscsi` across 3 StorageClasses (`truenas-iscsi`,
`truenas-iscsi-ssd`, `truenas-iscsi-ephemeral`) + the `truenas-iscsi-snapshot` VolumeSnapshotClass:

| Workload | Criticality |
|---|---|
| 13 CNPG Postgres clusters (flashcards/golinks/immich/linkding/memos/overture/vitals, prod+stage) | **CRITICAL** |
| Immich (db + 500Gi prod uploads, 100Gi stage) | **CRITICAL** |
| Loki (`storage-loki-0`) | High |
| Authelia, Adguard, Home Assistant, Mosquitto, Hermes, Jellyfin, Navidrome, Audiobookshelf, Mealie, Snapcast | Med/Low |

This driver backs every database in the cluster — hence the test gate before cutover.

## Compatibility verdict — existing PVs survive ✅ (one hard caveat)

- **`csiDriver.name` stays `org.democratic-csi.truenas-iscsi`** → all 170 PVs keep binding;
  external-attacher/resizer leases are keyed on this name. **Do not change it.**
- **Volume-handle format is identical.** `volume_id` is the PV name (`pvc-<uuid>`), assigned by
  external-provisioner — driver-agnostic. Both classes derive resources identically:
  `datasetName = datasetParentName + "/" + volume_id`; extent/target name from the leaf +
  `namePrefix`/`nameSuffix`/`nameTemplate`. It is an in-place `driver:`-value swap, not a re-handle.
- ⚠️ **CAVEAT (the silent-orphan risk):** the new config **must reproduce
  `zfs.datasetParentName` and `iscsi.namePrefix` / `nameSuffix` / `nameTemplate` byte-for-byte**, or
  the API driver looks for differently-named extents and fails to attach/expand/delete the existing
  170 PVs. Existing PVs show `iqn...:csi-pvc-<uuid>-k8s` (prefix `csi-`, suffix yielding `-k8s`).
  These values live in the SOPS secret — **operator must verify identity during the edit.**

## Pre-conditions (both operator-only, both gate the cutover)

1. **API role needs iSCSI WRITE.** The `melodic-muse-csi` role is iSCSI-**read**-only today
   (`SHARING_ISCSI_GLOBAL_READ`) because the SSH driver did iSCSI writes over SSH. The API driver
   does target/extent writes over the API → add **`SHARING_ADMIN`** (bundles all iSCSI
   extent/target/targetextent/portal/auth/initiator write), or the granular set:
   `SHARING_ISCSI_EXTENT_WRITE`, `SHARING_ISCSI_TARGET_WRITE`, `SHARING_ISCSI_TARGETEXTENT_WRITE`
   (+ `PORTAL_WRITE`/`INITIATOR_WRITE`/`AUTH_WRITE` if those objects are managed). Already present
   and still needed: `DATASET_WRITE`, `DATASET_DELETE`, `SNAPSHOT_WRITE`, `SNAPSHOT_DELETE`,
   `POOL_READ`, `SERVICE_READ`. **No `SERVICE_WRITE`/reload needed on Scale.** Verify with the
   JSON-RPC `auth.me` probe from the least-privilege plan + a write smoke test.
2. **Verify byte-identical naming** in the new secret (see caveat above).

## Config changes

### A. SOPS secret `democratic-csi-driver-config` → `driver-config-file.yaml` (operator-applied)

```diff
- driver: truenas-iscsi
+ driver: freenas-api-iscsi

  httpConnection:            # KEEP as-is (proxy + apiVersion 2 + melodic-muse-csi key)
    protocol: http
    host: truenas-api-proxy.democratic-csi.svc
    port: 80
    apiVersion: 2
    apiKey: <unchanged>
    allowInsecure: true

- sshConnection:            # DROP entirely
-   host: 10.42.2.10
-   username: truenas_admin
-   privateKey: <...>

  zfs:                       # KEEP byte-for-byte
    datasetParentName: <unchanged — MUST match>
    detachedSnapshotsDatasetParentName: <unchanged>
  iscsi:                     # KEEP byte-for-byte
    namePrefix: csi-         # MUST match (PVs are csi-…)
    nameSuffix: <unchanged — yields …-k8s>
    nameTemplate: <unchanged if set>
    targetPortal: "10.42.2.10:3260"
    # …extent options unchanged
```

The only changed field is `driver:`; `sshConnection` is dropped; everything else stays identical.

### B. `infra/controllers/democratic-csi/values.yaml` (branch + PR; cosmetic — secret is authoritative)

```diff
 driver:
   config:
-    driver: truenas-iscsi
+    driver: freenas-api-iscsi
   existingConfigSecret: democratic-csi-driver-config
   existingConfigSecretKey: driver-config-file.yaml
```

`csiDriver.name`, all 3 `storageClasses`, the `volumeSnapshotClasses[].driver`, and the `node:`
(Talos nsenter/iscsiadm) block all stay unchanged.

## Rollout

1. **Operator:** add iSCSI write to `melodic-muse-csi` (grant `SHARING_ADMIN`); verify via the
   JSON-RPC probe. Leave the `truenas_admin` SSH key + sudo in place for rollback.
2. **Operator:** edit the SOPS secret (driver → `freenas-api-iscsi`, drop `sshConnection`, keep the
   rest byte-identical). Open the `values.yaml` PR; let CI pass; merge.
3. Reconcile + `kubectl rollout restart deploy/democratic-csi-controller -n democratic-csi`; confirm
   it logs `driver: FreeNASApiDriver`.
4. **THROWAWAY-PVC TEST GATE** (before trusting any DB): on a 1Gi PVC in `truenas-iscsi` —
   provision → bind, mount in a busybox pod + write a file, **expand 1Gi→2Gi and confirm it leaves
   `Resizing`** with `df` showing 2Gi after a pod restart, snapshot+restore, then delete and confirm
   the zvol + extent are fully gone (no orphans). Watch controller logs for `permission denied` /
   role errors → add the missing privilege and retry.
5. **Only after the gate passes:** existing iSCSI sessions (CNPG/Immich) keep running throughout;
   they exercise the new driver only on their next create/expand/snapshot.
6. **Cleanup:** once stable, **revert the temporary passwordless sudo** on `truenas_admin` and
   (optionally) remove its SSH key from TrueNAS — the API driver no longer uses it.

## Rollback (fast, one revert)

Revert the SOPS secret (`driver:` back to `truenas-iscsi`, restore `sshConnection`) + revert the
`values.yaml` PR; reconcile; restart the controller. The `truenas_admin` SSH key + sudo were never
removed, so SSH-driver ops resume immediately. Existing PVs are unaffected by the flip-flop (the
csiDriver name and volume naming never changed).

## Risk

**MEDIUM — GO**, gated on the two operator pre-conditions (byte-identical naming + iSCSI-write role).
Lower than it looks because: same csiDriver name + volume-handle scheme, no PV re-provisioning,
one-revert rollback with the SSH path retained, and an end-to-end throwaway-PVC test before any DB
is touched. Residual risk: the API drivers are upstream-flagged "experimental" and the API
`expandVolume` is effectively a no-op post-resize (relies on the zvol size being read dynamically on
Scale at next iSCSI login — confirmed acceptable, must be test-verified).

## References

- [`2026-05-09-democratic-csi-least-privilege-key.md`](./2026-05-09-democratic-csi-least-privilege-key.md) — role set + JSON-RPC verification probe to reuse
- democratic-csi `examples/freenas-api-iscsi.yaml` (httpConnection-only); driver factory maps
  `truenas-iscsi`/`freenas-iscsi` → `FreeNASSshDriver`, `freenas-api-iscsi` → `FreeNASApiDriver`
- Files: `infra/controllers/democratic-csi/values.yaml`,
  `infra/controllers/democratic-csi/secret-driver-config.yaml` (SOPS),
  `infra/controllers/democratic-csi/truenas-api-proxy.yaml`
