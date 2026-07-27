# Incident: Jellyfin `/cache` refilled with orphaned transcodes (recurrence)

**Date:** 2026-07-26
**Status:** **Mitigated** — `/cache/transcodes` cleared (20 G → 121 M), service restored; durable fix (transcode-janitor sidecar) in review ([#1212](https://github.com/gjcourt/homelab/pull/1212))
**Severity:** Medium — user-facing degradation (broken thumbnails, laggy/broken playback, flaky resume) across devices; no data loss, no outage
**Environments affected:** `jellyfin-prod`
**Authors:** George Courtsunis

---

> **Direct recurrence of [2026-07-02](2026-07-02-jellyfin-cache-pvc-expand-simple-file-writer.md).**
> That incident bumped the cache PVC 5 Gi → 20 Gi and its "Prevent recurrence" section
> called for a transcode-cleanup CronJob/task — **which was never implemented**. With no
> cleanup, orphaned transcodes refilled the (now larger) 20 Gi volume in ~3.5 weeks.

## Summary

`jellyfin-cache-pvc` (20 Gi, `truenas-iscsi`, shared for image cache **and** transcodes)
hit **100% full (20 G / 20 G, 0 bytes free)**. `du` showed **`/cache/transcodes` = 20 G**
of orphaned transcode segments; `/cache/images` was only 120 M. Unlike 2026-07-02 (caught
by the `KubePersistentVolumeFillingUp` alert before user impact), this time it was noticed
via **user-facing symptoms** — the full volume triggered a cascade.

## Impact

- **Preview thumbnails stuck on blurhash** (image cache couldn't write new images).
- **Laggy / broken playback**, `FFmpeg exited with code <non-zero>` (13× in the retained
  log window) + `Error processing request … GET /videos/…/hls/…` (9×) — transcodes
  couldn't be written to the full volume, so FFmpeg died mid-stream.
- **Unreliable cross-device resume** — playback dying mid-stream truncated the periodic
  `POST /Sessions/Playing/Progress` and `…/Stopped` reports (`Unexpected end of request
  content`), so resume positions weren't saved.

## Timeline

1. User reports lag + broken preview images + flaky resume across devices (screenshots).
2. Server itself healthy at the k8s level (idle: 1m CPU / 508 Mi; nodes 6–11% CPU) → not
   resource starvation.
3. Jellyfin "Paths" screen showed `/cache`, `/cache/images`, `/cache/transcodes` all red
   at **19.6 / 19.6 GiB**.
4. In-pod `df` → `/cache` (`/dev/sdk`) **20 G, 100%**; `du -sh /cache/*` → `transcodes = 20 G`,
   `images = 120 M`.
5. **Mitigation:** `kubectl exec -n jellyfin-prod <pod> -- sh -c 'rm -rf /cache/transcodes/*'`
   → `/cache` 100% → **1% (121 M)**. FFmpeg immediately back to `exit code 0`.

## Root cause

**Jellyfin has no scheduled/periodic task that cleans transcodes** (confirmed via
`ScheduledTasks.TaskManager` logs: only Trickplay, Optimize-DB, Media-Segment-Scan,
Scan-Library run). Transcodes are deleted **only** (a) on a clean session-stop
(`TranscodeManager: Deleting partial stream file(s) …m3u8`) and (b) at server startup.

Sessions that end **abnormally** (client/network drops — normal client behaviour: closing
tabs, backgrounding apps, connection blips) leave orphaned transcode dirs. With **16 days
uptime / 0 restarts**, the startup-clear never ran and orphans accumulated until the volume
filled — then FFmpeg write-failures broke playback, which caused *more* abrupt disconnects
and *more* orphans: a self-feeding cycle.

The 2026-07-02 follow-up to add cleanup was never done, so the only thing that had changed
was the volume being 4× larger (20 Gi vs 5 Gi) — it just took longer to refill.

### What this was NOT

- **Not a gateway problem.** The HTTPRoute (`jellyfin-http/https` → `app-gateway-production`)
  sets no timeouts, and Jellyfin is **not** behind the Cloudflare tunnel (direct via the
  Cilium Gateway LB). The WebSocket "closed without completing handshake" + truncated POSTs
  were the disk-full cascade, not connection-layer timeouts.
- **Not a "resume only saves on stop" design flaw.** Jellyfin *does* report progress
  periodically (`/Sessions/Playing/Progress`, ~every 10 s). Resume broke because the full
  cache killed playback → both the periodic Progress and the final Stopped reports failed.

## How it was resolved

- **Immediate (done):** cleared `/cache/transcodes` (lossless — transcodes are ephemeral).
- **Durable ([#1212](https://github.com/gjcourt/homelab/pull/1212), in review):** a
  **`transcode-janitor` busybox sidecar** in `apps/base/jellyfin/deployment.yaml` that
  every 30 min deletes `/cache/transcodes` files unmodified for >4 h and prunes empty dirs
  — the periodic cleanup Jellyfin lacks. The >4 h threshold is safe for active/paused
  sessions (their segments keep a recent mtime).

  **Why a sidecar, not the CronJob the last incident suggested:** the cache PVC is **RWO**,
  so a separate CronJob pod can't reliably mount it while Jellyfin holds it (it would need
  to co-schedule on the same node). A sidecar shares the pod's mount — guaranteed and
  simple. busybox `find` has no `-delete`, so it uses `-exec rm -f {} +` / `-exec rmdir {} +`;
  runs `runAsUser: 0` because transcodes are root-owned (main container is privileged for GPU).

## Prevent recurrence

- The janitor sidecar (#1212) is the fix the 2026-07-02 incident called for.
- **Defense-in-depth (optional follow-up):** separate transcodes from the image cache
  entirely — mount `/cache/transcodes` as an `emptyDir` (with `sizeLimit`) so runaway
  transcodes can never again starve the image cache, and they self-clear on pod restart.

## Remaining follow-ups

- [ ] Merge #1212 (via staging validation).
- [ ] **Verify the `KubePersistentVolumeFillingUp` alert fired for this fill and is
      routed/visible** — 2026-07-02 was caught by the alert *before* user impact; this one
      reached users first, suggesting the alert was missed, silenced, or not routed.
- [ ] Optional: move transcodes to an `emptyDir` (defense-in-depth, above).

## References

- Precursor: [2026-07-02 — Jellyfin cache PVC full + iSCSI expansion](2026-07-02-jellyfin-cache-pvc-expand-simple-file-writer.md)
- Fix: [PR #1212](https://github.com/gjcourt/homelab/pull/1212) (transcode-janitor sidecar)
- `apps/base/jellyfin/deployment.yaml` — the sidecar + shared `jellyfin-cache` mount
