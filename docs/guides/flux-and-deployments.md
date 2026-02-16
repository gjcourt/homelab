# Flux and deployments

This repo is applied to clusters via Flux `Kustomization` resources under `clusters/`.

## Entry points

The cluster `melodic-muse` runs both staging and production from a single set of Flux Kustomizations:

- `clusters/melodic-muse/apps-staging.yaml` (applies `./apps/staging`)
- `clusters/melodic-muse/apps-production.yaml` (applies `./apps/production`)
- `clusters/melodic-muse/infra.yaml` (applies `./infra/controllers` + `./infra/configs`)

All of these reference the `GitRepository` named `flux-system` and use SOPS decryption via the `sops-agekey` secret.

## What gets applied (high level)

- `infra/controllers/`: operators and shared cluster services (cert-manager, monitoring/logging, CSI, etc.)
- `infra/configs/`: networking/storage config (Cilium, cert-manager issuers, etc.)
- `apps/<env>/`: environment overlay that selects which apps are deployed to that env

## Common reconcile commands

Staging:

- `flux reconcile source git flux-system`
- `flux reconcile kustomization infra-controllers -n flux-system`
- `flux reconcile kustomization infra-configs -n flux-system`
- `flux reconcile kustomization apps -n flux-system`

Production:

- `flux reconcile source git flux-system`
- `flux reconcile kustomization infra-controllers -n flux-system`
- `flux reconcile kustomization infra-configs -n flux-system`
- `flux reconcile kustomization apps-production -n flux-system`

## Debug checklist

- If a reconcile times out, check the failing controller/webhook first (e.g., cert-manager webhook availability).
- `kubectl -n flux-system get kustomizations,gitrepositories`
- `flux get kustomizations -A`
- `kubectl -n <ns> get events --sort-by=.lastTimestamp | tail -n 50`
