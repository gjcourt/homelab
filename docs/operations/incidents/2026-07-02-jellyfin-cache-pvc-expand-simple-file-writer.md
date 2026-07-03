# Incident: Jellyfin cache PVC full + iSCSI PVC expansion broken (`simple-file-writer`)

**Date:** 2026-07-02
**Status:** Mitigated (cache cleared, alert cleared); durable fix staged in this PR (draft)
**Severity:** Low — single app; `KubePersistentVolumeFillingUp` (critical) but no outage
**Environments affected:** `jellyfin-prod` (production overlay)
**Authors:** George Courtois

---

## Summary

`jellyfin-cache-pvc` fired **`KubePersistentVolumeFillingUp` (critical)** — it was
**99.6% full (5.18 G / 5.2 G, 0 bytes free)**. Root cause of the *fill* was **4.8 G of
orphaned transcode segments** in `/cache/transcodes` (Jellyfin doesn't reliably clean
these when playback is aborted).

The obvious fix — bump the PVC 5 Gi → 20 Gi ([PR #1016](https://github.com/gjcourt/homelab/pull/1016),
merged) — then exposed **two independent, pre-existing bugs in iSCSI PVC expansion** on
this cluster. The zvol grew on TrueNAS but the PVC never reached 20 Gi. The alert was
instead cleared by **deleting the orphaned transcodes** (lossless — it's a cache).

## Impact

- `KubePersistentVolumeFillingUp` critical alert firing (routed to `gjcourt+alerts@gmail.com`).
- At 0 bytes free, Jellyfin transcoding + image caching would fail on new streams.
- No outage; Jellyfin kept serving from the full volume.
- After the 20 Gi bump merged: `jellyfin-cache-pvc` stuck `Resizing`, external-resizer
  throwing a `VolumeResizeFailed` warning every ~30 s (noisy, harmless).

## Timeline

1. Alert observed (screenshot to phone). Confirmed real via kubelet volume stats:
   `jellyfin-cache-pvc used=5.18G cap=5.2G avail=0.00G (99.6%)`.
2. Cause of fill: `du -sh /cache/*` → `/cache/transcodes = 4.8G`, `images = 22M`.
3. Merged [#1016](https://github.com/gjcourt/homelab/pull/1016) bumping the prod cache PVC 5 Gi → 20 Gi (sc `truenas-iscsi`,
   `allowVolumeExpansion=true`). Reconciled `apps-production`.
4. PVC went `Resizing` but never completed. `external-resizer` logs:
   `error reloading iscsi daemon: {"stderr":"sudo: simple-file-writer: command not found","code":1}`.
5. Verified on TrueNAS: **zvol already 20 G** (`zfs list` → `main/k8s/iscsi/pvc-315c631f… VOLSIZE 20G`).
   So the backend grew; only the iSCSI-target *reload* failed.
6. **Mitigation:** cleared `/cache/transcodes` → `/cache` 100% → 1% (22 M used).
   Alert cleared on next scrape. No stream interrupted.

## Root cause — two independent layers

### Layer 1 (controller): `simple-file-writer` missing on TrueNAS — upstream #390

After resizing the zvol, democratic-csi's `ControllerExpandVolume` tells SCST to
re-read the new LUN size by writing `1` to
`/sys/kernel/scst_tgt/devices/<extent>/resync_size` on the TrueNAS host.

- **v1.9.0 (what we run)** does that write via a helper: `execClient.buildCommand("simple-file-writer", …)`
  (`src/driver/freenas/ssh.js`). That binary **does not exist on TrueNAS SCALE**
  (Dragonfish 24.04+ / our 26.x) → `sudo: simple-file-writer: command not found`.
  Upstream issue: <https://github.com/democratic-csi/democratic-csi/issues/390>.
  (Predecessor bug #295 was the pre-1.9 `sudo sh -c echo 1 > …` form, where the `>`
  redirect ran in the *unprivileged* outer shell → `Permission denied`.)
- **v1.9.5 / master** drops `simple-file-writer` entirely and uses a direct, properly
  quoted `"echo 1 > /sys/kernel/scst_tgt/devices/${kName}/resync_size"`.

This is **not** the passwordless-sudo revocation and **not** a SOPS/driver-config issue —
`simple-file-writer` appears nowhere in our `driver-config-file.yaml`; it's baked into
the v1.9.0 image. `truenas_admin` sudo is `NOPASSWD: ALL` and works.

### Layer 2 (node): `resize2fs` EPERM on Talos — pre-existing, still open

Even with Layer 1 fixed, in-place growth **still won't complete**: the node-side
`NodeExpandVolume` online `resize2fs` hits **EPERM on `EXT4_IOC_RESIZE_FS`**
(cross-namespace, Talos kernel — not a caps/seccomp problem). Documented in
`infra/controllers/democratic-csi/values.yaml` and the SSH→API plan. So **online iSCSI
ext4 PVC growth does not work on this cluster at all**, independent of driver version.

## Current state (as of 2026-07-02)

- `jellyfin-cache-pvc`: request **20 Gi**, status.capacity **5 Gi**, condition `Resizing`
  (retrying forever on Layer 1). `/cache` usage ~22 M (cleared). **App healthy.**
- zvol `main/k8s/iscsi/pvc-315c631f-2139-44fa-9e94-0c411d45c756`: **VOLSIZE 20 G** on TrueNAS.
- Driver image: `democraticcsi/democratic-csi:v1.9.0` (this PR bumps to v1.9.5).
- You **cannot** walk the request back to 5 Gi (Kubernetes forbids shrinking a PVC request).

## Remediation

### Recommended: recreate `jellyfin-cache-pvc` (lossless — it's a cache)

Fresh provisioning at 20 Gi needs no `resize2fs`, so it sidesteps **both** layers.
Because `/cache` is regenerable, this is zero-risk:

```bash
flux suspend kustomization apps-production -n flux-system
kubectl -n jellyfin-prod scale deploy/jellyfin --replicas=0
kubectl -n jellyfin-prod delete pvc jellyfin-cache-pvc     # Retain PV → old zvol orphaned, safe to purge later
flux resume kustomization apps-production -n flux-system    # re-provisions fresh 20 Gi from the manifest
kubectl -n jellyfin-prod scale deploy/jellyfin --replicas=1
# verify: kubectl -n jellyfin-prod get pvc jellyfin-cache-pvc  → 20Gi Bound, no Resizing
```

This also clears the stuck `Resizing` condition and the noisy resizer retries.

### Staged in this PR: bump driver v1.9.0 → v1.9.5 (fixes Layer 1)

Removes the `simple-file-writer` blocker so `ControllerExpandVolume` succeeds for all
future resizes. **Draft** because George earmarked this as a *tested* change paired
with **chart 0.15.1** (`release.yaml`) — validate the image+chart together in staging
before merge. Note: v1.9.5 alone does **not** enable online growth (Layer 2 remains).

### Prevent recurrence (regardless of size)

20 Gi will refill the same way. Cap/clean transcodes — e.g. a CronJob:
`find /cache/transcodes -type f -mmin +720 -delete`, or Jellyfin's transcode-cleanup task.

### Open problem to solve for real online growth

Layer 2 (`resize2fs` EPERM on Talos) is unsolved. Until then, "grow" = "recreate".
Candidate directions: xfs storage class (untested — xfs grows via a different ioctl),
or the SSH→API driver migration (`docs/plans/2026-06-28-democratic-csi-ssh-to-api-driver.md`,
currently blocked — see that plan).

## Pick-up-from-cold checklist

- [ ] Validate v1.9.5 image (+ chart 0.15.1) in staging; merge this PR.
- [ ] Recreate `jellyfin-cache-pvc` at 20 Gi (steps above) — or leave 5 Gi + add transcode-cleanup CronJob and drop the size back on a future recreate.
- [ ] Purge the orphaned 20 G zvol `pvc-315c631f-…` on TrueNAS if the PVC is recreated (Retain leaves it behind).
- [ ] Passwordless `truenas_admin` sudo must be ON for any controller-side resize path (currently ON; George may re-revoke — flip back before retrying).
- [ ] Decide whether to pursue Layer 2 (xfs trial / SSH→API migration) so online growth works without recreate.

## References

- Upstream: [democratic-csi #390](https://github.com/democratic-csi/democratic-csi/issues/390) (`simple-file-writer`), [#295](https://github.com/democratic-csi/democratic-csi/issues/295) (predecessor redirect bug)
- Fix commit: `src/driver/freenas/ssh.js` v1.9.0 vs v1.9.5 (helper removed → direct `echo 1 > resync_size`)
- [PR #1016](https://github.com/gjcourt/homelab/pull/1016) — the 5 Gi → 20 Gi bump that surfaced this
- `infra/controllers/democratic-csi/values.yaml` — image pin + the Layer 2 EPERM warning
- `docs/plans/2026-06-28-democratic-csi-ssh-to-api-driver.md` — SSH→API migration (blocked)
