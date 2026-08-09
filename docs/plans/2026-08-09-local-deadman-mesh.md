---
status: planned
last_modified: 2026-08-09
summary: "Cross-monitor k8s, hestia and alcatraz so each reports the others' death, with healthchecks.io as the arbiter that tells one-box failure from a site event"
blocked_on: "Operator: are the Talos nodes, hestia and alcatraz on the same UPS/circuit? Determines how much this buys."
---

# Plan: local dead man's mesh (k8s ↔ hestia ↔ alcatraz)

## Context

The external dead man's switch routes the always-firing `Watchdog` alert to a
healthchecks.io check that alerts on the ping's *absence*. That closes the
catastrophic case — total alerting failure no longer looks like silence.

It is deliberately coarse. healthchecks.io knows exactly one bit per check:
pings arrived, or they didn't. It cannot say which component died, and it
cannot distinguish a house-wide power cut from the ISP dropping.

## The thesis

**Each layer is blind to precisely what the other sees.**

| | External (healthchecks.io) | Local mesh |
|---|---|---|
| Survives whole-site failure | ✅ it isn't here | ❌ dies with everything |
| Identifies *which* component | ❌ one bit | ✅ names it, with diagnostics |
| Tells WAN-down from power-down | ❌ identical silence | ✅ peers still see each other |
| Detection latency | ~20 min (grace) | seconds to ~2 min |
| Needs maintenance | ❌ vendor's problem | ✅ ours, and it can rot |

This is not a replacement for the external check. It is the diagnostic tier
underneath it.

### The failure this actually fixes

The CODA-56 overheats and reboots (see `docs/STATUS.md`, modem thermal). Every
one of those events drops the WAN, so *every* external check goes red at once —
which reads as a site-level catastrophe. It isn't: every machine is healthy and
the modem is cooking. Today nothing can tell those apart. A local mesh records
"all peers up, WAN unreachable" and the story is unambiguous the moment
connectivity returns.

## Inventory and constraints

| Node | Role | Can it run a watcher? | Independent notification path |
|---|---|---|---|
| Talos cluster | 3 CP + workers | **Not on the host** — Talos is immutable and API-driven; no host cron. Must be a k8s CronJob or the Alertmanager fan-out | Gmail SMTP (existing) |
| hestia | TrueNAS SCALE; iSCSI/NFS backing store | Yes — SCALE Custom App (compose YAML canonical in-repo) | TrueNAS built-in alert email |
| alcatraz | Synology DSM | Yes — DSM Task Scheduler | DSM native email/push |

Two constraints fall out of that table and shape the whole design:

1. **Talos has no host-level cron.** The cluster cannot run an ordinary watcher
   daemon the way the other two can.
2. **hestia is a dependency of k8s, not a peer.** democratic-csi backs cluster
   PVCs over iSCSI from hestia, so "hestia down" *causes* "k8s degraded". These
   two are correlated by construction — treat hestia→k8s as a supervisor
   relationship, not two independent observers.

Only alcatraz is genuinely independent of the other two.

## Design

### Push, not poll, for the cluster's heartbeat

The cluster already emits `Watchdog` through the full chain. Have the local
watchers **receive** that webhook rather than poll an endpoint: polling
`/-/healthy` only proves the API answers, whereas receiving the Watchdog proves
rule evaluation → Alertmanager → routing → delivery all work. Same signal, same
pipeline, three destinations.

Keep the external receiver isolated from local flakiness by fanning out with a
`continue: true` route rather than adding local URLs to the existing `deadman`
receiver — a flapping LAN endpoint must never generate delivery errors on the
path that protects the catastrophic case:

```yaml
- receiver: 'deadman'          # healthchecks.io — must stay clean
  matchers: [alertname = "Watchdog"]
  continue: true
- receiver: 'deadman-local'    # hestia + alcatraz
  matchers: [alertname = "Watchdog"]
```

### Who watches whom

| Watcher | Watches | Mechanism |
|---|---|---|
| k8s | hestia, alcatraz | blackbox-exporter `Probe` + PrometheusRule (new; no blackbox exporter today) |
| hestia | k8s, alcatraz | Custom App: Watchdog webhook receiver + timer; poll alcatraz |
| alcatraz | k8s, hestia | DSM Task Scheduler script on 5m |

Each watcher notifies through **its own** path, so one broken mail
configuration cannot silence everything.

### healthchecks.io as arbiter

Give each box its own check. The *pattern* of which went quiet is the diagnosis
— information no single check can carry:

| Red | Reading |
|---|---|
| cluster only | Alerting or cluster failure; hestia + alcatraz supply detail |
| hestia only | Storage host down — expect PVC degradation next |
| alcatraz only | NAS down; lowest blast radius |
| all three | Site event: power or WAN |

## Honest limits

**WAN-down still can't be reported in real time.** If the modem drops while
every machine is healthy, nothing inside the house can reach you. The mesh's
value there is *post-hoc*: its logs prove it was the modem, not a power cut. The
only real-time fix is an out-of-band path (LTE), which is out of scope here.

**Who watches the watchers.** hestia's cron and the DSM task can fail silently,
which locally regresses forever. Terminating it is exactly what the external
tier is for — each watcher pings its own healthchecks.io check, so a dead
watcher surfaces as a red check rather than as false confidence.

**Correlated failure caps the value.** If everything shares a UPS and a switch,
the mesh covers strictly less than the table above implies — the failures it
uniquely catches are single-component ones, and those are already the ones
ordinary alerting handles when alerting is alive. This is the open question in
the frontmatter and it should be answered before Phase 2.

**Three more moving parts.** Two watchers and a blackbox exporter, each with its
own upgrade and failure story, to cover a scenario where the cluster is already
degraded. Worth it for the diagnostics; not worth it if it becomes another thing
that rots unnoticed.

## Phasing

1. **Cluster → hosts.** Deploy blackbox-exporter, probe hestia + alcatraz, alert
   on down. Smallest step, entirely in-repo, no new hosts touched, and it is
   independently useful (there is no host liveness monitoring at all today).
2. **alcatraz → cluster + hestia.** DSM Task Scheduler + native notification.
   Highest value per unit effort: alcatraz is the only genuinely independent
   node, and DSM's notification path is already configured and off-cluster.
3. **hestia → cluster + alcatraz.** SCALE Custom App receiving the
   `deadman-local` webhook. Do last — hestia is the most coupled to the cluster,
   so it adds the least independence.
4. **Per-box healthchecks.io checks** so the watchers are themselves watched,
   and the arbiter table above becomes readable.

Phase 1 is worth doing regardless of the UPS answer. Phases 2–4 should wait on
it.
