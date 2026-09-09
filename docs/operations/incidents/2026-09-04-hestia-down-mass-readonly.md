---
status: resolved
last_modified: 2026-09-04
summary: "hestia went down; 59 of 63 cluster volumes remounted read-only. Recovered by per-pod forced detach. Corrects four runbook procedures that do not work for this failure."
---

# 2026-09-04 — hestia down, 59/63 volumes read-only

**Largest instance of the recurring iSCSI read-only failure to date** (previous
high: 2026-02-28). Every StorageClass is `democratic-csi` over iSCSI to hestia,
so losing that host took storage out from under most of the cluster.

## Timeline

| | |
| :--- | :--- |
| — | hestia stops responding. BMC (`10.42.2.13`) stays up; ping, `:22`, `:80`, `:443` all dead |
| — | Cluster reports **healthy**: nodes Ready, Flux ready, zero failing pods. `pvc-writeprobe` reports **59 read-only volumes across 30 namespaces**, 26 of them CNPG instances |
| — | Hard reset by operator |
| — | hestia returns. `main` pool **ONLINE, zero data errors**; iSCSI/NFS/SMB all serving |
| — | Per-pod forced detach/reattach, each verified by writing bytes |
| — | **21/21 CNPG instances writable**; all affected Deployments recovered; Flux resumed |

**No data loss.** ZFS came through a hard reset on 6.89 TB intact.

## Diagnosis

The BMC being reachable while the OS was not is the useful discriminator: it runs
on standby power and sits on the same switch port, so its liveness ruled out **both**
a PSU/PDU failure and a switch/VLAN failure, and localised the fault to the OS.

The read-only mounts were confirmed to be the documented `emergency_ro` variant —
the mount advertises `rw` while every write returns `EROFS`:

```text
/dev/sdj on /var/lib/postgresql/data type ext4 (rw,relatime,seclabel,stripe=2048,emergency_ro)
```

## ⚠️ Four runbook procedures that do not work for this failure

All four are now corrected in `AGENTS.md`. Recorded here because each cost real time.

**1. `kubectl cnpg restart` silently no-ops.** It printed `<cluster> restarted`
and the pod was never recycled — `.status.startTime` was still two months old and
the volume remained `emergency_ro`. It is not a recovery path for this failure.

**2. `kubectl cnpg fencing` does not release the volume.** Fencing stops
PostgreSQL *inside* the pod; the pod keeps running and keeps the device attached.
Right tool for a stuck promote, wrong tool here.

**3. "Wait for `volumeattachment` to reach zero" is wrong for self-recreating
workloads.** CNPG instances and Deployment pods are replaced as fast as the old
attachment is released, so the count never reaches zero **even on a fully
successful recovery**. This produced two false "still stuck" readings and one
300-second wait for a condition that could not occur. The check is only valid
inside the scale-to-0 cycle, where nothing recreates the pod.

**4. The scale-to-0 cycle is usually unnecessary.** Plain `kubectl delete pod`
recovered every affected Deployment in ~20–30 s. The runbook's warning applies to
`rollout restart` (creates-before-terminates), not to a delete.

## The signature of a successful recovery

The replacement pod is frequently scheduled onto a **different node** and gets a
brand-new device:

```text
before:  /dev/sdj  node talos-2mz-rfj  ext4 (rw,...,emergency_ro)   → writes EROFS
after:   /dev/sdm  node talos-ykb-uir  ext4 (rw,relatime,...)       → WRITABLE
```

## ⚠️ The probe lags — do not read a stale sweep as a regression

`pvc-writeprobe` runs on `INTERVAL_SECONDS=300`, so a sweep can be **five minutes
older than reality**. Twice during this recovery a stale sweep was read as
volumes having regressed, when the sweep simply predated the fix. Compare the
sweep timestamp against the pod's `.status.startTime` before believing it.

**Ground truth is the write test, not the probe and not pod status:**

```bash
kubectl -n <ns> exec <pod> -c <ctr> -- sh -c 'printf ok > <mount>/.wtest && rm -f <mount>/.wtest'
```

## Found during recovery, NOT caused by it

- **`immich-prod` has no serving database.** Primary `immich-db-prod-cnpg-v3-5`
  is CrashLoopBackOff with **280 restarts over 21 days**, terminating
  `exit=0 / Completed` — a probe killing postgres before startup completes, not
  corruption. Predates this outage by ~3 weeks and explains the `2/3 ready` visible
  before hestia went down. Immich has effectively been down for three weeks.
  **Needs its own diagnosis.**
- **`snapcast-prod`** CrashLoopBackOff, 44 restarts. Possibly the known
  go-librespot auth pattern; unverified.

## Bootstrap gap

BMC credentials for recovering hestia are documented in `family/admin` **on
hestia**. When hestia is down they are unreachable, which is exactly when they are
needed. A copy belongs somewhere that does not depend on the host being up.
