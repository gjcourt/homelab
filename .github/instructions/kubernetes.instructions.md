---
applyTo: "apps/**,infra/**,clusters/**,kubecraft/**"
---

# Kubernetes & Kustomize conventions

## YAML (general)

- Use `.yaml` (not `.yml`) unless the repo already uses `.yml` for a specific file.
- Enforce 2-space indentation for all YAML in this repo (exactly 2 spaces per level). Never use tabs and never use 4-space indentation.
- Keep manifests readable: stable key ordering, consistent quoting, avoid line-wrapping that hurts diffs.
- Prefer explicit port names, resource requests/limits, and readiness/liveness probes where appropriate.
- Security: follow the repo's Pod Security stance (restricted-style) unless explicitly asked to relax it.

## Kustomize (base + overlays)

- Base should be reusable; overlays should be small patches.
- Use patches to rename namespaces and set env-specific labels/annotations.
- Avoid duplicating full resources in overlays unless necessary.
- When adding ingress/routes, ensure they match the cluster's conventions (e.g., Gateway API + namespace label requirements).

## Naming conventions

- **Storage**: Always name PersistentVolumeClaim and StorageClass files as `storage.yaml`. Do not use `pvc.yaml`, `pv.yaml`, or similar.
- **Config**: Use `configmap.yaml` and `secret.yaml`.
- **Workloads**: Use `deployment.yaml`, `statefulset.yaml`, `daemonset.yaml`.
- **Network**: Use `service.yaml`, `ingress.yaml`, `httproute.yaml`.
- **RBAC**: Use `rbac.yaml` or `serviceaccount.yaml`.
