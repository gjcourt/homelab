# Incident: Jellyfin cache PVC full + iSCSI PVC expansion broken (`simple-file-writer`)

**Date:** 2026-07-02
**Status:** **Resolved** — driver bumped v1.9.0 → v1.9.5 ([#1019](https://github.com/gjcourt/homelab/pull/1019), merged 2026-07-03); expand completed online end-to-end
**Severity:** Low — single app; `KubePersistentVolumeFillingUp` (critical) but no outage
**Environments affected:** `jellyfin-prod` (production overlay)
**Authors:** George Courtois

---

> **Update 2026-07-03 — RESOLVED.** The v1.9.5 bump ([#1019](https://github.com/gjcourt/homelab/pull/1019))
> fixed the expand **end-to-end**. After the controller rolled to v1.9.5, the stuck
> `jellyfin-cache-pvc` resize completed on the next retry: `ControllerExpandVolume`
> succeeded (no more `simple-file-writer`), then **`NodeExpandVolume`/`resize2fs`
> succeeded too** — `/cache` grew 5 Gi → 20 Gi with the pod Running, no recreate.
> **Layer 2 below (the node-side EPERM) did NOT materialize on v1.9.5** — it was a
> v1.9.0-era artifact, not a standing Talos wall. Online iSCSI PVC growth now just
> works: bump the request, `flux reconcile`. **Keep `truenas_admin` passwordless sudo
> ON** — the SCST `resync_size` reload runs `sudo sh -c "echo 1 > …"` on every expand.

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

### Layer 2 (node): `resize2fs` EPERM — suspected, but DISPROVEN on v1.9.5

The old `values.yaml` comment warned that node-side `NodeExpandVolume` online
`resize2fs` hits **EPERM on `EXT4_IOC_RESIZE_FS`** (cross-namespace, Talos kernel),
so the fear was that even with Layer 1 fixed, growth wouldn't complete. **This did not
happen on v1.9.5** (see the resolution note at the top): the node resize succeeded
cleanly. The EPERM was a v1.9.0-era artifact, not a standing wall — online growth works.
Layer 2 is retained here only for the historical record.

## Current state (as of 2026-07-02)

**Final (post-#1019):**

- `jellyfin-cache-pvc`: request **20 Gi**, status.capacity **20 Gi**, **no conditions**,
  Bound. `/cache` = 20 G filesystem (22 M used, 1%). **App healthy.** Resizer clean
  (`Update capacity of PV to 20Gi succeeded`); retry loop stopped.
- Driver image: `democraticcsi/democratic-csi:v1.9.5`.
- The alert itself was cleared earlier by deleting the orphaned transcodes.

## How it was resolved

**Bump democratic-csi v1.9.0 → v1.9.5 ([#1019](https://github.com/gjcourt/homelab/pull/1019)).**
This removed the `simple-file-writer` call (Layer 1). Once the controller rolled to
v1.9.5, the external-resizer's next retry of the already-pending expand ran the direct
`sudo sh -c "echo 1 > .../resync_size"`, `ControllerExpandVolume` succeeded, and the
node-side `resize2fs` also succeeded — the PVC reached 20 Gi online with the pod up. No
recreate needed; the feared Layer-2 EPERM never occurred.

> **Growing a PVC from here on:** bump `spec.resources.requests.storage`, merge,
> `flux reconcile kustomization apps-production`. Works online. Keep `truenas_admin`
> passwordless (NOPASSWD) sudo ON — the SCST `resync_size` reload runs
> `sudo sh -c …` non-interactively over SSH on every expand.

### Prevent recurrence (the original alert)

20 Gi will refill the same way if transcodes keep accumulating. Cap/clean them — e.g. a
CronJob `find /cache/transcodes -type f -mmin +720 -delete`, or Jellyfin's
transcode-cleanup scheduled task. (Not yet done — follow-up.)

## Remaining follow-ups

- [ ] Add a transcode-cleanup CronJob (or Jellyfin task) so `/cache` doesn't refill.
- [ ] Optional: paired chart bump to 0.15.1 (`release.yaml`) to match the v1.9.5 image.
- [ ] Keep `truenas_admin` passwordless sudo ON (required for the reload on every expand).

## References

- Upstream: [democratic-csi #390](https://github.com/democratic-csi/democratic-csi/issues/390) (`simple-file-writer`), [#295](https://github.com/democratic-csi/democratic-csi/issues/295) (predecessor redirect bug)
- Fix: `src/driver/freenas/ssh.js` v1.9.0 vs v1.9.5 (helper removed → `sudo sh -c "echo 1 > …/resync_size"`)
- [PR #1016](https://github.com/gjcourt/homelab/pull/1016) — the 5 Gi → 20 Gi bump that surfaced this; [PR #1019](https://github.com/gjcourt/homelab/pull/1019) — the v1.9.5 fix
- `infra/controllers/democratic-csi/values.yaml` — image pin + the (now-resolved) expand notes
