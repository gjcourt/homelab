# cnpgscope

Read-only **CloudNativePG fleet health at a glance**. Sibling to `netscope`
(eBPF network), `thermalscope` (thermal/RAPL) and `mqttscope` (MQTT broker) —
but where those are Prometheus exporters, cnpgscope is an operator-facing CLI:
it enumerates every CNPG `Cluster` across all namespaces and prints a scannable
fleet summary with an **OK / WARN / CRITICAL** verdict per cluster.

## Why

An Immich CNPG cluster had an outage: a **stale (inactive) replication slot
pinned WAL**, which filled a 10Gi replica PVC, crashlooped a replica **and**
blocked the operator's reconcile, creeping the primary to 81% full. That class
of failure is invisible in `kubectl get cluster` (which happily reports "Cluster
in healthy state" the whole way down). cnpgscope surfaces the leading indicators
— inactive slots retaining WAL, `pg_wal` as a large fraction of the volume,
per-instance PVC fill — across the **whole** database fleet, before any single
cluster tips over.

## What it reports, per cluster

| Signal | Detail |
| --- | --- |
| **Instances** | desired vs ready, which pod is primary, CrashLoop / NotReady |
| **Replication slots** | active vs **INACTIVE** slots and **WAL retained per slot** (inactive slots pinning WAL are the #1 risk) |
| **WAL** | `pg_wal` size vs the instance PVC size (fill fraction) |
| **PVC usage** | % full per instance PVC (WARN >75%, CRIT >85%) |
| **Replication lag** | streaming replicas' bytes behind the primary |
| **Backups** | continuous-archiving health + last successful backup age |
| **Verdict** | OK / WARN / CRITICAL — the max severity across all signals |

### Verdict thresholds

| Signal | WARN | CRIT |
| --- | --- | --- |
| PVC used | ≥75% | ≥85% |
| pg_wal fraction of volume | ≥50% (supporting signal, never CRIT on its own) | — |
| Inactive slot retained WAL | ≥512Mi | ≥2Gi, or `wal_status=lost` |
| Streaming replica lag | ≥64Mi | ≥512Mi |
| Instances ready | `ready < desired`, primary still up | primary not ready / 0 ready |
| Last successful backup age | ≥26h | ≥49h |
| Continuous archiving | `ContinuousArchiving=False` | — |

`pg_wal` fraction is deliberately WARN-only: a high steady-state WAL fraction is
normal on a small volume, so on its own it never cries CRITICAL — the hard
signals are PVC fill and inactive slots pinning WAL. It exists to flag "WAL
dominates the volume, limited headroom" so a genuinely tight volume is visible.

## Usage

```console
$ ./cnpgscope.py                 # whole fleet, colored table + non-OK detail
$ ./cnpgscope.py -n immich-prod  # one namespace
$ ./cnpgscope.py --cluster immich-db-prod-cnpg-v3
$ ./cnpgscope.py --no-exec       # CRD-only, no pod exec (fast / low-privilege)
$ ./cnpgscope.py -o json         # machine-readable
$ ./cnpgscope.py --context foo   # target a specific kube-context
```

Exit code is the **worst verdict** found — `0` OK, `1` WARN, `2` CRITICAL — so
it drops straight into cron / CI gating. `--exit-zero` forces `0`.

Example:

```
cnpgscope — CloudNativePG fleet health @ 2026-07-10T17:39  (13 clusters)

CLUSTER                              INST  PVC  WAL  SLOTS   LAG  BACKUP  VERDICT
immich-prod/immich-db-prod-cnpg-v3   3/3   76%  65%  1!6.4G  0B   arch    CRIT
overture-prod/overture-db-...-v1     0/3   ?    ?    -       -    arch    CRIT
golinks-prod/golinks-db-...-v1       3/3   65%  61%  2a      0B   arch    WARN
...
Fleet: 2 CRITICAL, 3 WARN, 8 OK

CRIT  immich-prod/immich-db-prod-cnpg-v3  (primary immich-db-prod-cnpg-v3-5)
    * inactive slot _cnpg_immich_db_prod_cnpg_v3_6 pinning 6.4G of WAL (wal_status=extended)
    * immich-db-prod-cnpg-v3-5 PVC 76% full
    * immich-db-prod-cnpg-v3-5 pg_wal is 65% of the volume (6.4G) — limited headroom
```

The `SLOTS` column reads `<n>a` for n active slots, or `<n>!<bytes>` when there
are inactive slots (with the largest retained WAL) — the smoking gun.

## How it talks to the cluster

All **read-only**. cnpgscope never mutates anything.

1. `kubectl get clusters.postgresql.cnpg.io -A -o json` — desired/ready, primary,
   conditions (`ContinuousArchiving`, `LastBackupSucceeded`),
   `lastSuccessfulBackup`, storage size.
2. `kubectl get pods -A -l cnpg.io/cluster -o json` — per-instance phase,
   container readiness, role.
3. `kubectl exec <pod> -c postgres -- df / du` — per-instance PVC fill and
   `pg_wal` size. Fanned out in parallel; `--no-exec` skips it entirely.
4. `kubectl exec <primary> -c postgres -- psql` — `pg_replication_slots` and
   `pg_stat_replication` for retained WAL per slot and streaming lag.

Only the caller's kube-context and RBAC are used; there is no in-cluster
component. The `--no-exec` pass needs only `get cluster` / `get pods`; the full
pass additionally needs `pods/exec` in the CNPG namespaces.

## Requirements

- Python 3.9+ (standard library only — no pip install).
- `kubectl` on `PATH` with a working context.
- The CNPG plugin is **not** required (cnpgscope reads the CRD + execs psql
  directly), though `kubectl cnpg status <cluster>` is the natural drill-down.

## Container / scheduled use

`Dockerfile` bakes `cnpgscope.py` onto a Python + kubectl base so it can run as
a `CronJob` (e.g. a daily fleet sweep that alerts on a non-zero exit) with a
ServiceAccount granted read-only `clusters`/`pods` get+list and `pods/exec`.
Built + pushed to `ghcr.io/gjcourt/cnpgscope` by
`.github/workflows/build-cnpgscope.yml` on changes under `images/cnpgscope/`.
No in-cluster deployment ships in this change — the primary use is the local CLI.
