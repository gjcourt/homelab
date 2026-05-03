# Network Policies

Cluster-wide NetworkPolicy scaffolding for `melodic-muse`. Tracks Phase 1 / PR
1.1 of `docs/plans/2026-05-02-critique-remediation.md`.

## What ships here

- `default-deny.yaml` — a `CiliumClusterwideNetworkPolicy` named `default-deny`
  that enforces deny-all ingress + egress, but **only** for pods whose
  namespace carries the label `network-policies: enforced`. No real
  namespace has that label yet, so the policy is inert on apply.

## How rollout works

The cluster runs Cilium (with Hubble enabled — see
`infra/controllers/cilium/values.yaml`), so we use Cilium-native policies for
richer L7 semantics.

The rollout is intentionally per-namespace and gated by a label so we can
canary one app at a time and watch `hubble observe --verdict DROPPED` before
expanding.

```
Per-app CiliumNetworkPolicy  ──►  Namespace opt-in  ──►  Default-deny enforces
   (allow rules ship first)      (label flips on)        (deny matches the ns)
```

### Opting a namespace in

1. Make sure the namespace already has a per-app `CiliumNetworkPolicy` (or
   `networking.k8s.io/v1.NetworkPolicy`) under `apps/base/<app>/networkpolicy.yaml`
   that allows the traffic the app needs (DNS to `kube-system`, ingress from the
   Gateway namespace `default`, egress to any backend services, etc.).
2. Add the label to the namespace manifest:
   ```yaml
   apiVersion: v1
   kind: Namespace
   metadata:
     name: <app>
     labels:
       network-policies: enforced
   ```
3. Open a PR, let staging reconcile, then watch:
   ```bash
   hubble observe --verdict DROPPED -n <app>
   ```
   for at least one full reconcile cycle (and ideally 24h) before merging
   to master.

### Backing a namespace out

Remove the label. The `default-deny` selector stops matching the namespace's
pods immediately and traffic flows again. The per-app `CiliumNetworkPolicy`
stays in place — it only narrows what the namespace will allow once the label
goes back on.

## Apps onboarded

| App | Namespace | Per-app policy | Label applied |
|-----|-----------|----------------|---------------|
| excalidraw | `excalidraw`, `excalidraw-stage`, `excalidraw-prod` | `apps/base/excalidraw/networkpolicy.yaml` | yes (opt-in is live, default-deny stays inert until other apps land) |

As more apps onboard, append rows to this table in the PR that adds them.

## When to flip the cluster-wide default-deny on

This file *is* the cluster-wide policy. It will become enforcing on any
namespace as soon as that namespace is labelled. There is no separate
"enable" switch — once every app namespace ships a per-app allow policy and
carries the label, every workload in the cluster is covered. At that point,
this README should be updated to drop the "inert" caveat.

## Related

- Plan: [`docs/plans/2026-05-02-critique-remediation.md`](../../../docs/plans/2026-05-02-critique-remediation.md) Phase 1 / PR 1.1
- Cilium docs: <https://docs.cilium.io/en/stable/security/policy/>
