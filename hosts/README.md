# hosts/

Host-specific artifacts for services that run directly on physical or virtual hosts (not managed by Flux on melodic-muse).

## Directory layout

```
hosts/
  <hostname>/
    README.md              # IP, SSH user, dataset paths, what runs here
    <service>/
      docker-compose.yml   # canonical source; operator pastes into SCALE UI or runs directly
      README.md            # env vars, secrets, sync instructions, runbook
```

## Boundaries

| Location | What lives here |
|----------|----------------|
| `hosts/` | docker-compose files, host-side scripts, and dataset-path docs for services deployed on specific hosts (TrueNAS, VMs) outside Kubernetes |
| `apps/` | Kubernetes manifests for services managed by Flux on melodic-muse |
| `images/` | Dockerfile + source for container images we author |
| `infra/` | Cluster-level controllers, CRDs, and config for melodic-muse |

## Sync policy

For TrueNAS Custom Apps: the YAML in this directory is the canonical source of truth.
To apply a change, open SCALE UI → Apps → `<app>` → Edit → paste the updated YAML → Save.
Never edit the compose in the SCALE UI without updating git — that creates drift.
