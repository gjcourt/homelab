# lun-manager

Go binary that audits and cleans up orphaned Synology iSCSI LUNs/Targets by
cross-referencing the NAS iSCSI configuration against active Kubernetes
PersistentVolumes.

Replaces the original Python scripts (`audit_luns.py` / `cleanup_orphans.py`)
with a single compiled binary, optional concurrency for deletions, and
concurrent NAS config loading.

## Build

```bash
cd scripts/synology/lun-manager
go build -o lun-manager .
```

Or cross-compile for Linux:

```bash
GOOS=linux GOARCH=amd64 go build -o lun-manager-linux .
```

## Environment Variables

| Variable             | Required | Default  | Description                  |
|----------------------|----------|----------|------------------------------|
| `SYNOLOGY_HOST`      | yes      | —        | NAS hostname or IP           |
| `SYNOLOGY_USER`      | no       | `admin`  | SSH username                 |
| `SYNOLOGY_PASSWORD`  | yes      | —        | SSH / sudo password          |
| `SYNOLOGY_PORT`      | no       | `22`     | SSH port                     |

`kubectl` must be on `$PATH` and configured to talk to the cluster.

## Subcommands

### `audit`

Compares every iSCSI LUN on the NAS against Kubernetes PVs and prints a
table showing each LUN's status (Bound / Released / ORPHAN), size, and the
PVC claim it belongs to.

```
lun-manager audit
```

Both the NAS SSH connection and the `kubectl get pv` call are made
concurrently.  The three NAS config files (`iscsi_lun.conf`,
`iscsi_target.conf`, `iscsi_mapping.conf`) are also read in parallel over the
same SSH client.

### `cleanup`

Deletes orphaned LUNs (and their associated Targets) from the NAS.

```
# Dry-run — preview what would be deleted (default: execute)
lun-manager cleanup --dry-run

# Actually delete
lun-manager cleanup

# Delete with 4 parallel workers
lun-manager cleanup --workers 4
```

Flags:

| Flag         | Default | Description                              |
|--------------|---------|------------------------------------------|
| `--dry-run`  | false   | Preview deletions; do not make changes   |
| `--workers`  | 1       | Number of concurrent deletion goroutines |

Each deletion performs three steps per LUN: unmap → delete LUN → delete
Target.  Workers share a single SSH client (`*ssh.Client` is
concurrency-safe), opening a fresh session per step.

## Example output

### audit

```
LUN Name                                                      Status      Size(GiB)  Claim
----------------------------------------------------------------------------------------------------
k8s-csi-pvc-abc123                                            Bound           10.00  production/immich-db
k8s-csi-pvc-def456                                            ORPHAN           5.00
...
Total LUNs: 64  |  Bound: 64  |  Released: 0  |  Orphaned: 0
```

### cleanup (dry-run)

```
DRY RUN — would delete 3 orphaned LUN(s):
  LUN k8s-csi-pvc-aaa111  UUID xxxxxxxx-...  TID 42
  ...
Re-run without --dry-run to delete.
```
