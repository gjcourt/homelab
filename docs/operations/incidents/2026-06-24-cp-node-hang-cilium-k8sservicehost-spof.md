# Incident: Control-Plane Node Hang Cascades to Cluster-Wide DNS Outage via Cilium `k8sServiceHost` SPOF

**Date:** 2026-06-24 (trigger 2026-06-21)
**Status:** Mitigated (live patch); permanent fix in this PR
**Severity:** High — cluster-wide DNS down; storage, databases, and most apps unavailable
**Duration:** ~2.5 days undetected (node hung 2026-06-21 21:16 UTC → diagnosed/mitigated 2026-06-24 ~05:00 UTC)
**Environments affected:** Whole cluster (`melodic-muse`)
**Authors:** George Courtois

---

## Summary

Control-plane node `talos-ykb-uir` (`10.42.2.20`) hung at the OS level on
**2026-06-21 21:16 UTC**. The node still answered ICMP and accepted TCP, but its
`kubelet` stopped posting status and Talos `apid` could no longer complete a TLS
handshake (even routed through a healthy node) — i.e. `machined`/`apid` were stuck,
not just unreachable.

A single dead node should be a non-event on a 3× control-plane cluster. Instead it
took out cluster DNS and cascaded to storage, databases, and nearly every app —
because **Cilium was pinned to that node's apiserver** (`k8sServiceHost: 10.42.2.20`).

## Impact

- `cilium-operator` crashlooping on every healthy node (dialing `https://10.42.2.20:6443`).
- `CoreDNS` 0/2 ready — both new replicas failed readiness (`dial 10.96.0.1:443: i/o timeout`),
  so `kube-dns` had **zero endpoints** → cluster DNS dead.
- With no DNS backend, Cilium rejected traffic to `10.96.0.10:53`
  (`connect: operation not permitted`), so everything that resolves a name failed:
  democratic-csi (`getaddrinfo EAI_AGAIN truenas-api-proxy...`), cloudflared, the CNPG
  operator, Flux `source-controller`, cert-manager, and all DB-backed apps (immich,
  golinks, flashcards, linkding, home-assistant, …).
- ~57 pods stuck `Terminating` on the dead node (kubelet gone, deletion unconfirmable).
- etcd ran at **2/3 quorum** the entire window — healthy but fragile (losing `.21` or
  `.23` would have been a full outage).

## Root cause

Two layers:

1. **Trigger:** `.20` hung hard (resource/IO stall or kernel-level hang). Because
   `apid` was unresponsive, `talosctl reboot/shutdown/reset` could not work — recovery
   requires a physical power-cycle (the Talos nodes have no BMC/IPMI; only hestia does).

2. **Why one node became a cluster-wide outage (the real bug):** Cilium runs *beneath*
   Kubernetes Service networking, so it cannot reach the API via the `kubernetes`
   ClusterIP (`10.96.0.1`) — it needs a direct apiserver address (`k8sServiceHost`).
   That value was hardcoded to a single control-plane node, `10.42.2.20`, making that
   node a single point of failure for the CNI, and therefore for DNS and everything
   downstream. (`docs/architecture/networking/addressing.md` already annotated `.20` as
   the "`k8sServiceHost` SPOF" — known risk, unmitigated.)

## Mitigation (live, already applied)

1. Suspended the Cilium HelmRelease so Flux would not revert the patch:
   `kubectl -n kube-system patch helmrelease cilium --type merge -p '{"spec":{"suspend":true}}'`
2. Repointed Cilium to a healthy apiserver:
   `kubectl -n kube-system set env deploy/cilium-operator KUBERNETES_SERVICE_HOST=10.42.2.21`
   `kubectl -n kube-system set env ds/cilium KUBERNETES_SERVICE_HOST=10.42.2.21`

DNS recovered within ~1 minute (operator healthy, CoreDNS 2/2, kube-dns endpoints
populated), and the cascade cleared (CSI 6/6, CNPG operator up, Flux/cloudflared up,
databases back). `kubectl`/`talosctl` were also repointed to `.21` for the duration
(both configs still target the dead `.20`).

## Permanent fix (this PR)

Point `k8sServiceHost` at **Talos KubePrism** instead of any node IP:

```yaml
k8sServiceHost: "localhost"
k8sServicePort: "7445"
```

KubePrism runs on every node (loopback `127.0.0.1:7445`), load-balances across all
control-plane apiservers, and health-checks dead ones out. Verified enabled and
`healthy: true` on `.21` and `.23` at mitigation time
(`talosctl get kubeprismstatus`), with all apiservers as upstreams — so no Talos
machine-config change is required. After this merges and reconciles, any single
control-plane node can hang without affecting Cilium, DNS, or apps.

## Follow-up / restore checklist

- [ ] Merge this PR; let Flux reconcile the Cilium HelmRelease values.
- [ ] **Resume the Cilium HelmRelease** (`flux resume hr cilium -n kube-system` /
      unset `spec.suspend`) — it is suspended right now; do not resume *before* this
      merges or Flux will re-apply `k8sServiceHost: 10.42.2.20` and re-break the cluster.
      The resume will roll the agents/operator onto the KubePrism value, superseding the
      live `set env` patch.
- [ ] Power-cycle `.20` (physical — no BMC). Mind the `.20/.21` shared switch/PSU path;
      quorum is 2/3, so do not drop `.21` with it. On boot it rejoins etcd (→ 3/3) and
      the ~57 `Terminating` zombie pods clear.
- [ ] Repoint local `~/.kube/config` and `~/.talos/config` off `.20` (or to a VIP).
- [ ] Investigate `.20`'s hang root cause on next boot: BMC/SEL log, BIOS/POST memory
      training, dmesg — see `docs/operations/2026-06-17-talos-node-maintenance.md`.
      (`.22` was previously decommissioned for a bad DIMM; watch for recurrence.)

## Lessons

- **Never pin a CNI's apiserver endpoint to a single node.** Use Talos KubePrism
  (`localhost:7445`) or a control-plane VIP. A documented "SPOF" annotation is a bug to
  fix, not a label to keep.
- **`kubeconfig`/`talosconfig` pointing at a single node IP is its own fragility** —
  losing that node looks like a total outage from the workstation even when the cluster
  is up. Prefer a VIP / multiple endpoints.
- A NotReady node whose `apid` won't handshake cannot be recovered via `talosctl`;
  without a BMC, that means physical access. Out-of-band management for the cluster
  nodes would have turned a 2.5-day outage into a remote reboot.
