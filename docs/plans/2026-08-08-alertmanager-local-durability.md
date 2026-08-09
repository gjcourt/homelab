---
status: planned
last_modified: 2026-08-09
summary: "Gap 2 (deadman) resolved 2026-08-09; the remaining open item is node-local volumes for state across a simultaneous restart"
---

# Plan: Alertmanager local durability + deadman

## Context

[#1278](https://github.com/gjcourt/homelab/pull/1278) moved Alertmanager from a
single `emptyDir` pod to **2 gossip replicas with hard anti-affinity**. That
covers pod restart, node drain, node loss and rolling chart upgrades without
introducing a storage dependency.

It deliberately did **not** add a PVC. Every StorageClass in this cluster is
`democratic-csi` against hestia, so a volume would make Alertmanager
unschedulable whenever the iSCSI target is unavailable — alert delivery would
stop during precisely the storage incident that most needs it. Flux would not
surface it either: Helm does not own the operator-created StatefulSet, so the
HelmRelease reports `Ready` with the pod `Pending`.

Two gaps remain.

## Gap 1 — state is lost if every replica restarts at once

Gossip replicates between live peers. A simultaneous restart of both — cluster-wide
power event, a chart upgrade that rolls both faster than gossip re-syncs, both
nodes drained together — still drops silences and the notification log.

Low impact today: the live silence inventory at the time of #1278 was **one**
entry, itself redundant with a permanent `null` route already in values.yaml.

### Option A — node-local volumes alongside HA (the complete answer)

Each replica gets a volume on the node it runs on. Gossip covers single-pod
restarts; the disk covers a full restart. Every failure mode is then handled.

Needs a local provisioner, which this cluster does not have. Candidates:

| Approach | Notes |
|---|---|
| `local-path-provisioner` (Rancher) | Simplest; needs a Talos machine-config patch for the host directory, since the filesystem is otherwise immutable |
| Static `local` PVs | No new controller, but manual PV-per-node — poor fit for GitOps |

**Cost:** a new cluster component with its own upgrade and failure story, to
protect against a scenario where the cluster is already down. Weigh honestly.

### Option B — accept it

Document the residual risk and move on. Reasonable while the silence inventory
stays near zero.

## Gap 2 — silent alerting loss is undetectable (the bigger one) — **RESOLVED 2026-08-09**

> Closed by the healthchecks.io dead man's switch: `Watchdog` now routes to a
> `deadman` receiver that POSTs to an off-cluster check every 5m (15m grace,
> ~20m worst-case detection). The ping URL lives in the SOPS-encrypted
> `alertmanager-deadman` secret and is referenced via `url_file`.
>
> The follow-on local mesh — cross-monitoring between the cluster, hestia and
> alcatraz to say *which* component died and to tell a WAN drop from a power
> cut — is specified in
> [2026-08-09-local-deadman-mesh.md](2026-08-09-local-deadman-mesh.md).
>
> Original analysis retained below.

`Watchdog` routes to the `null` receiver, and there is no external check on
`alerts.burntbytes.com`. If Alertmanager dies, or the notification path breaks,
**nothing tells you.** Every alert in the cluster goes quiet and the failure is
indistinguishable from silence-because-all-is-well.

This is worth more than Gap 1. Durability protects silences; a deadman protects
the entire alerting function.

### Sketch

Route `Watchdog` (which fires continuously by design) to an external endpoint
that alerts on *absence*:

| Option | Notes |
|---|---|
| healthchecks.io | Free tier; a webhook receiver pinging a check with a grace period |
| Cloudflare Worker + cron | Self-hosted, already have the account, no new vendor |
| UptimeRobot keyword monitor | Watches `alerts.burntbytes.com`; catches a dead AM but not a broken notification path |

The receiver must live **off this cluster** — a deadman that shares failure
domains with the thing it watches is decoration.

**Open question for the operator:** which external service. Deliberately not
chosen here; picking a vendor is not a decision to make inside a plan doc.

## Recommendation

1. ~~**Gap 2 first.** Highest value, smallest change, no new cluster
   components.~~ **Done 2026-08-09** — healthchecks.io, as above.
2. **Gap 1 only if the silence inventory grows** enough that losing it during a
   full-cluster restart would actually hurt. Revisit if a local provisioner
   arrives for other reasons.

Gap 1 remains the only open item here, and it is still judged not worth a new
cluster component. This plan stays `planned` on that basis alone.
