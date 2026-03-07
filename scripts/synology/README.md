# Synology iSCSI Operations Toolkit

Tools for managing Synology NAS iSCSI storage used by the Kubernetes cluster's
CSI driver (`synology-csi`).

## synology-tool (Go binary)

The primary tool is `synology-tool/`, a compiled Go binary that:

- **Audits** iSCSI LUNs and targets on the NAS against Kubernetes PVs.
- **Inspects** individual LUN backing files and checks btrfs health.
- **Copies** LUN backing files from the NAS for offline recovery.
- **Cleans up** orphaned LUNs (no matching K8s PV) via SSH + `synoiscsiwebapi`.
- **Cleans up** orphaned iSCSI targets (no matching LUN) via the DSM HTTPS API.
- **Enables** disabled iSCSI targets via the DSM HTTPS API.

See [`synology-tool/README.md`](synology-tool/README.md) for full documentation.

### Quick start

```bash
cd scripts/synology/synology-tool
go build -o synology-tool .

export SYNOLOGY_HOST="10.42.2.11"
export SYNOLOGY_USER="manager"
export SYNOLOGY_PASSWORD="..."

# Audit LUNs + targets vs K8s PVs
./synology-tool audit

# Clean up orphaned targets (prevents hitting 128-target limit)
./synology-tool cleanup-targets --dry-run
./synology-tool cleanup-targets

# Clean up orphaned LUNs
./synology-tool cleanup-luns --dry-run
./synology-tool cleanup-luns --workers 4

# Re-enable any disabled targets
./synology-tool enable-targets
```

## Prerequisites

- Go 1.22+ (for building `synology-tool`).
- SSH access to the Synology NAS (user in `administrators` group).
- DSM HTTPS API access (port 5001, same credentials as SSH).
- `kubectl` configured with cluster access (for K8s PV cross-referencing).
- `sshpass` (for the `copy` command only).

## Typical Recovery Workflow

```
1. Audit:            synology-tool audit
2. Clean targets:    synology-tool cleanup-targets --dry-run && synology-tool cleanup-targets
3. Clean LUNs:       synology-tool cleanup-luns --dry-run && synology-tool cleanup-luns
4. Enable targets:   synology-tool enable-targets
5. Verify:           synology-tool audit
```

## How It Works

- **DSM HTTPS API** (port 5001): Used for listing LUNs/targets, deleting
  targets, and enabling targets. Provides structured JSON responses. The API
  requires `target_id` to be JSON-quoted in URLs (e.g. `target_id=%223%22`).

- **SSH + CLI**: Used for LUN deletion (via `synoiscsiwebapi`), backing file
  inspection, and SCP copy. Commands run via `sudo`.

- **Name mapping**: The CSI driver creates LUNs named `k8s-csi-pvc-<UUID>` and
  targets with the same name. The tool matches them by stripping the `k8s-csi-`
  prefix from the LUN name to recover the Kubernetes PV name.

- **128-target limit**: Synology NAS has a hard limit of 128 iSCSI targets.
  Orphaned targets can silently accumulate and block new volume provisioning.
  The `audit` command warns when approaching this limit.
