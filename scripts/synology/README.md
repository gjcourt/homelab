# Synology iSCSI Operations Toolkit

Tools for managing Synology NAS iSCSI storage used by the Kubernetes cluster's
CSI driver (`synology-csi`).

## lun-manager (Go binary)

The primary tool is `lun-manager/`, a compiled Go binary that:

- **Audits** iSCSI LUNs on the NAS against Kubernetes PersistentVolumes.
- **Cleans up** orphaned LUNs and their Targets (LUNs with no matching K8s PV).

See [`lun-manager/README.md`](lun-manager/README.md) for full documentation.

### Quick start

```bash
cd scripts/synology/lun-manager
go build -o lun-manager .

export SYNOLOGY_HOST="192.168.5.8"
export SYNOLOGY_USER="manager"
export SYNOLOGY_PASSWORD="..."

# Audit: show Bound / Released / ORPHAN status for every LUN
./lun-manager audit

# Preview what cleanup would delete
./lun-manager cleanup --dry-run

# Delete orphans (with 4 parallel workers)
./lun-manager cleanup --workers 4
```

## Prerequisites

- Go 1.22+ (for building `lun-manager`).
- SSH access to the Synology NAS (user in `administrators` group).
- `kubectl` configured with cluster access (for K8s PV cross-referencing).

## Typical Recovery Workflow

```
1. Audit:    lun-manager audit
2. Preview:  lun-manager cleanup --dry-run
3. Clean:    lun-manager cleanup [--workers N]
4. Verify:   lun-manager audit
```

## How It Works

- **Name mapping**: The CSI driver creates LUNs named `k8s-csi-pvc-<UUID>` and
  targets named with IQN `iqn.2000-01.com.synology:*.pvc-<UUID>`. The tools
  match them by stripping the `k8s-csi-` prefix from the LUN name to recover
  the Kubernetes PV name.
- **SSH + CLI**: The binary SSHes to the NAS and runs
  `/usr/syno/bin/synoiscsitool` with `sudo`. This is the internal CLI tool that
  the DSM UI calls — more reliable than the HTTP API for bulk operations.
- **Config files**: The NAS stores iSCSI config in:
  - `/usr/syno/etc/iscsi_target.conf` — target definitions
  - `/usr/syno/etc/iscsi_lun.conf` — LUN definitions
  - `/usr/syno/etc/iscsi_mapping.conf` — LUN-to-target mappings
