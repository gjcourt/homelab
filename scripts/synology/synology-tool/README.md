# synology-tool

CLI tool for managing Synology NAS iSCSI storage in a Kubernetes homelab.
Cross-references NAS LUNs, iSCSI targets, and Kubernetes PVs to detect
orphans, audit health, and automate cleanup.

## Build

```bash
cd scripts/synology/synology-tool
go build -o synology-tool .
```

## Prerequisites

| Requirement | Used by |
|---|---|
| `kubectl` on `$PATH`, configured for the cluster | all commands |
| SSH access to Synology NAS | `inspect`, `copy`, `cleanup-luns` |
| DSM HTTPS API access (port 5001) | all commands |
| `sshpass` installed locally | `copy` only |

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SYNOLOGY_HOST` | yes | — | NAS hostname or IP |
| `SYNOLOGY_USER` | no | `admin` | User for SSH and DSM API |
| `SYNOLOGY_PASSWORD` | yes | — | Password for SSH and DSM API |
| `SYNOLOGY_PORT` | no | `22` | SSH port |
| `SYNOLOGY_API_PORT` | no | `5001` | DSM HTTPS API port |

## Commands

### `audit`

Cross-reference NAS LUNs and iSCSI targets with Kubernetes PVs. Reports
LUN binding status (Bound / Released / Orphaned) and target health
(total count vs. 128 limit, orphaned targets, disabled targets).

```bash
synology-tool audit
```

### `inspect <PV>`

Show detailed information about a single LUN: NAS metadata, iSCSI target
state, Kubernetes PV binding, backing file listing, and btrfs filesystem
health check (via loop device on NAS).

Accepts either a PV name (`pvc-xxx`) or full LUN name (`k8s-csi-pvc-xxx`).

```bash
synology-tool inspect pvc-abc12345-6789-...
```

### `copy <PV>`

Copy a LUN's backing file from the NAS to the local machine via SCP.
Requires `sshpass` to be installed.

```bash
synology-tool copy pvc-abc12345 --output /tmp/backup.img
```

### `cleanup-luns`

Delete orphaned LUNs (LUNs that have no matching Kubernetes PV).
Each deletion unmaps the LUN from its target, deletes the LUN, and
deletes the associated target via the `synoiscsiwebapi` CLI over SSH.

```bash
# Preview what would be deleted
synology-tool cleanup-luns --dry-run

# Delete with 4 concurrent workers
synology-tool cleanup-luns --workers 4
```

### `cleanup-targets`

Delete orphaned iSCSI targets (targets whose name does not match any
existing LUN). Uses the DSM HTTPS API directly. This is critical for
preventing the Synology 128-target limit from blocking new volume
provisioning.

```bash
# Preview
synology-tool cleanup-targets --dry-run

# Delete all orphaned targets
synology-tool cleanup-targets
```

### `enable-targets`

Enable all disabled iSCSI targets. The `synoiscsiwebapi` CLI can
silently disable targets during LUN mapping operations; this command
re-enables them via the DSM API.

```bash
# Preview
synology-tool enable-targets --dry-run

# Enable all disabled targets
synology-tool enable-targets
```

## Architecture

The tool uses two communication channels to the NAS:

- **DSM HTTPS API** (port 5001): Used for listing LUNs/targets, deleting
  targets, and enabling targets. Provides structured JSON responses and
  avoids config-file parsing.

- **SSH**: Used for operations that require filesystem access (`inspect`,
  `copy`) or the `synoiscsiwebapi` CLI (`cleanup-luns`). Commands run via
  `sudo` with the password piped to stdin.

### Key Implementation Details

- **Target deletion**: The DSM API requires `target_id` to be JSON-quoted
  in the URL (`target_id=%223%22`), not passed as a bare integer. Passing
  the bare integer causes error code 18990710.

- **128 target limit**: Synology NAS has a hard limit of 128 iSCSI targets.
  Orphaned targets (created by the CSI driver but never cleaned up) can
  consume all slots and block new volume provisioning. The `audit` command
  warns when approaching this limit.

- **LUN-to-target matching**: The Synology CSI driver creates both a LUN
  and a target with the same name (`k8s-csi-<pv-name>`). Cross-referencing
  is done by name.
