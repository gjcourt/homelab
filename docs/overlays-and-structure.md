# Overlays and structure

This repo uses Kustomize overlays to separate shared app definitions from environment-specific deployment choices.

## Apps

- `apps/base/<app>/`: base definition for an app (Deployments/StatefulSets, Services, HTTPRoutes, PVCs, etc.)
- `apps/staging/<app>/`: staging overlay for that app (namespace patches, env-specific config/secrets/ingress)
- `apps/production/<app>/`: production overlay for that app

Environment entrypoints:

- `apps/staging/kustomization.yaml` lists which apps are deployed to staging
- `apps/production/kustomization.yaml` lists which apps are deployed to production

## Infra

Infra is split into two large buckets:

- `infra/controllers/`: operators/controllers installed into the cluster (often via HelmRelease)
- `infra/configs/`: cluster configuration that operators depend on (networking, IP pools, etc.)

## Naming conventions

- Staging namespaces generally use a `-stage` suffix.
- Production is intended to be unsuffixed (some existing namespaces may still include `-prod`).

## Pattern example

A typical lifecycle:

1. Add/modify the base app in `apps/base/<app>/`
2. Add/modify env overlay in `apps/staging/<app>/` and/or `apps/production/<app>/`
3. Ensure the app is referenced from the env entrypoint `apps/<env>/kustomization.yaml`
4. Commit + push → Flux reconciles and applies the change
