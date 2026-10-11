# Network Policies

Cluster-wide NetworkPolicy scaffolding for `melodic-muse`. Tracks Phase 1 / PR
1.1 of `docs/plans/2026-05-02-critique-remediation.md`.

## What ships here

- `default-deny.yaml` — a `CiliumClusterwideNetworkPolicy` named `default-deny`
  that puts pods into default-deny for ingress and egress, but **only** pods
  whose namespace carries the label `network-policies: enforced`. It does
  this with one empty rule per direction (`- {}`), which allows nothing on
  its own; each app's own `CiliumNetworkPolicy` allows what it needs.
- **Rollout status:** staging namespaces only for now (the selector also
  lists `adguard-stage`, `excalidraw-stage`, `llmux-stage`,
  `openwebui-stage`). A follow-up extends it to the labelled `-prod`
  namespaces after a staging soak.
- **History:** until the fix that added this note, the policy used
  `ingress: []` / `egress: []`, which Cilium rejects as having no rules, so
  it was never enforced (agents logged "Unable to add" from 2026-05-03).
  `make check-cilium-policies` now catches that.

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
pods immediately. Note that this does not open the namespace up: any pod a
per-app `CiliumNetworkPolicy` selects stays in default-deny for each
direction that policy has rules for (Cilium does that on its own). Removing
the label only frees pods that no per-app policy selects.

## Apps onboarded

| App | Namespace | Per-app policy | Label applied |
|-----|-----------|----------------|---------------|
| adguard | `adguard-stage` | `apps/base/adguard/networkpolicy.yaml` (+ `adguard-sync`) | yes (stage) |
| excalidraw | `excalidraw-stage`, `excalidraw-prod` | `apps/base/excalidraw/networkpolicy.yaml` | yes |
| llmux | `llmux-stage`, `llmux-prod` | `apps/base/llmux/networkpolicy.yaml` | yes |
| openwebui | `openwebui-stage`, `openwebui-prod` | `apps/base/openwebui/networkpolicy.yaml` | yes |

As more apps onboard, append rows to this table in the PR that adds them.

## Extending it

Today the policy applies to labelled staging namespaces only. To extend it,
edit the second `matchExpressions` entry in `default-deny.yaml` (add the
`-prod` namespaces, or drop the entry so the label alone decides) in its own
PR, after a staging soak with `hubble observe --verdict DROPPED -n <ns>`
clean and the apps working. Before labelling any new namespace, check that
every pod in it is selected by a per-app policy covering both ingress and
egress, DNS included; a pod no policy selects loses all traffic.

## Related

- Plan: [`docs/plans/2026-05-02-critique-remediation.md`](../../../docs/plans/2026-05-02-critique-remediation.md) Phase 1 / PR 1.1
- Cilium docs: <https://docs.cilium.io/en/stable/security/policy/>
