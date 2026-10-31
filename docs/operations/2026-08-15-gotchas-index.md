---
title: Gotchas index
status: Stable
created: 2026-08-15
updated: 2026-10-06
updated_by: gjcourt
tags: [operations, gotchas, index]
---

# Gotchas index

Hard-won operational behaviour that isn't obvious from the manifests or the upstream docs — the
things that cost real debugging time once and shouldn't cost it twice.

These were accumulated as working notes outside the repo. Promoted here 2026-08-15 so they're
available to whoever is actually debugging at 2am, rather than only inside a tooling session.

| Runbook | Covers |
| ------------------------------------------------------------ | ------------------------------------------------- |
| [Networking](./2026-08-15-networking-gotchas.md) | Cilium netpol for Gateway API, LoadBalancer SNAT, Cloudflare tunnel apex + Access, Loki fill from Cilium debug logging |
| [Storage](./2026-08-15-storage-gotchas.md) | PVC/PV immutability, Retain recovery, ZFS busy, TrueNAS apps, Synology, rsync verification, democratic-csi driver + PVC expansion, hestia media-library moves |
| [Cluster](./2026-08-15-cluster-gotchas.md) | Talos topology and sysfs, kubectl selectors, Flux (suspend, volumeName, readiness metrics, SSA vs `strategy: Recreate`) |
| [Services](./2026-08-15-services-gotchas.md) | CNPG, SOPS, mosquitto, Spotify, Mopidy |
| [Operating principles](./2026-08-15-operating-principles.md) | Staging, registries, cluster access, API versions |

Related references that live outside these five: the TrueNAS 26.x API (method names, `wss://`
only) in [reference/storage.md](../reference/storage.md#11-truenas-api); per-app traps in
[apps/snapcast.md](./apps/snapcast.md) (Spotify re-auth), [apps/finance-dashboard.md](./apps/finance-dashboard.md)
(cache-busting deploy checks) and [apps/bluetooth-presence/espresense-nodes.md](./apps/bluetooth-presence/espresense-nodes.md)
(IRK enrolment).

**Read the storage runbook before destroying anything.** Several entries there are data-loss
adjacent — PVCs holding SQLite databases that look empty, PVs whose Retain policy is the only thing
standing between you and a rebuild, ZFS datasets that report busy for a reason you won't guess.

## Adding to these

A gotcha earns a place here when it (a) cost real time, (b) is not discoverable from the manifest or
the upstream docs, and (c) will recur. If it's a one-off caused by a specific broken state, it
belongs in the incident write-up, not here.
