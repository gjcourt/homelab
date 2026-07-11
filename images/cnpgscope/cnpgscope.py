#!/usr/bin/env python3
"""cnpgscope — CloudNativePG fleet health at a glance.

A read-only CLI in the same "scope" spirit as netscope (eBPF network),
thermalscope (thermal/RAPL) and mqttscope (MQTT broker). Where those are
Prometheus exporters, cnpgscope is an operator-facing diagnostic: it enumerates
every CloudNativePG `Cluster` across all namespaces and prints a scannable
fleet summary with an OK / WARN / CRITICAL verdict per cluster.

It exists because of a real Immich outage: stale (inactive) replication slots
pinned WAL, filled a 10Gi replica PVC, crashlooped a replica AND blocked the
operator's reconcile, creeping the primary to 81% full. The signals that would
have caught it early — inactive slots retaining WAL, pg_wal as a large fraction
of the volume, per-instance PVC fill — are exactly what cnpgscope surfaces
across the whole database fleet before any single cluster tips over.

What it reports, per cluster:
  * Instances       desired vs ready, which pod is primary, CrashLoop/NotReady.
  * Replication      active vs INACTIVE slots and WAL retained per slot
    slots            (inactive slots pinning WAL are the #1 risk).
  * WAL              pg_wal size vs the instance PVC size (fill fraction).
  * PVC usage        %% full per instance PVC (WARN >75%%, CRIT >85%%).
  * Replication lag  streaming vs lagging replicas (bytes behind primary).
  * Backups          continuous-archiving health + last successful backup age.
  * Verdict          OK / WARN / CRITICAL so the fleet is scannable at a glance.

Data sources (all READ-ONLY — cnpgscope NEVER mutates anything):
  * `kubectl get clusters.postgresql.cnpg.io -A -o json` — desired/ready,
    primary, conditions (ContinuousArchiving, LastBackupSucceeded),
    lastSuccessfulBackup, storage size.
  * `kubectl get pods -A -l cnpg.io/cluster -o json` — per-instance phase +
    container readiness + role.
  * `kubectl exec <pod> -c postgres -- df / du` — per-instance PVC fill and
    pg_wal size (skip with --no-exec for a fast, exec-free pass).
  * `kubectl exec <primary> -c postgres -- psql` — pg_replication_slots and
    pg_stat_replication (retained WAL per slot, streaming lag).

Usage:
  cnpgscope.py                      # whole fleet, colored table + detail
  cnpgscope.py -n immich-prod       # one namespace
  cnpgscope.py --cluster immich-db-prod-cnpg-v3
  cnpgscope.py --no-exec            # CRD-only, no pod exec (fast / low-priv)
  cnpgscope.py -o json              # machine-readable
  cnpgscope.py --context <ctx>      # target a specific kube-context

Exit code is the worst verdict found: 0 OK, 1 WARN or UNKNOWN, 2 CRITICAL (handy
for cron / CI gating — an unprobeable fleet fails the gate too). --exit-zero
forces 0.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Thresholds (all overridable via --* flags). Chosen from the Immich outage:
# a 10Gi PVC at 76% with pg_wal already 68% of the volume, pinned by a 6.5G
# inactive slot, was minutes from the failure that took the cluster down.
# ---------------------------------------------------------------------------
PVC_WARN = 75.0        # % of instance PVC used
PVC_CRIT = 85.0
# pg_wal as % of the instance PVC size. A supporting (WARN-only) signal: a high
# steady-state WAL fraction is normal on a small volume, so on its own it never
# escalates to CRIT — the hard signals are PVC fill and inactive slots pinning
# WAL. It flags "WAL dominates the volume, limited headroom" so a genuinely
# tight volume (like the outage) is visible before the slot/PVC signals trip.
WAL_FRAC_WARN = 50.0
SLOT_WARN_BYTES = 512 * 1024**2       # inactive slot retaining >512Mi of WAL
SLOT_CRIT_BYTES = 2 * 1024**3         # ...or >2Gi (or wal_status lost) => CRIT
LAG_WARN_BYTES = 64 * 1024**2         # streaming replica >64Mi behind
LAG_CRIT_BYTES = 512 * 1024**2        # ...or >512Mi => CRIT
BACKUP_WARN_AGE = 26 * 3600           # last successful backup older than ~1d
BACKUP_CRIT_AGE = 49 * 3600           # ...or ~2d
EXEC_TIMEOUT = 20                     # seconds per kubectl exec
EXEC_WORKERS = 12                     # parallel exec fan-out

PGDATA = "/var/lib/postgresql/data"
PGWAL = f"{PGDATA}/pgdata/pg_wal"

OK, WARN, CRIT, UNKNOWN = "OK", "WARN", "CRIT", "UNKNOWN"
_SEV_RANK = {OK: 0, UNKNOWN: 1, WARN: 2, CRIT: 3}


def worst(*sevs: str) -> str:
    """Return the most severe verdict among the arguments."""
    return max((s for s in sevs if s), key=lambda s: _SEV_RANK.get(s, 0), default=OK)


# ---------------------------------------------------------------------------
# Color
# ---------------------------------------------------------------------------
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"


_USE_COLOR = True


def _c(text: str, *codes: str) -> str:
    if not _USE_COLOR or not codes:
        return text
    return "".join(codes) + text + C.RESET


def sev_color(sev: str) -> str:
    return {OK: C.GREEN, WARN: C.YELLOW, CRIT: C.RED, UNKNOWN: C.DIM}.get(sev, "")


def paint_sev(sev: str) -> str:
    return _c(sev, C.BOLD, sev_color(sev))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def human_bytes(n: float | None) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "K", "M", "G", "T", "P"):
        if abs(n) < 1024 or unit == "P":
            if unit == "B":
                return f"{int(n)}{unit}"
            return f"{n:.1f}{unit}".replace(".0", "")
        n /= 1024
    return f"{n:.1f}P"


def human_age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    mins = seconds // 60
    if mins < 90:
        return f"{mins}m"
    hours = mins // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


# ---------------------------------------------------------------------------
# kubectl plumbing
# ---------------------------------------------------------------------------
class Kubectl:
    def __init__(self, binary: str, context: str | None):
        self.base = [binary]
        if context:
            self.base += ["--context", context]

    def json(self, *args: str) -> dict:
        out = self._run(list(args), timeout=60)
        return json.loads(out) if out else {}

    def exec(self, ns: str, pod: str, script: str) -> tuple[bool, str]:
        """Run a read-only shell snippet in the pod's postgres container."""
        args = ["exec", "-n", ns, pod, "-c", "postgres", "--",
                "bash", "-c", script]
        try:
            out = self._run(args, timeout=EXEC_TIMEOUT)
            return True, out
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False, ""

    def psql(self, ns: str, pod: str, sql: str) -> tuple[bool, str]:
        args = ["exec", "-n", ns, pod, "-c", "postgres", "--",
                "psql", "-qtAF", "|", "-c", sql]
        try:
            out = self._run(args, timeout=EXEC_TIMEOUT)
            return True, out
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False, ""

    def _run(self, args: list[str], timeout: int) -> str:
        proc = subprocess.run(
            self.base + args,
            capture_output=True, text=True, timeout=timeout, check=True,
        )
        return proc.stdout


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Slot:
    name: str
    active: bool
    retained_bytes: int | None
    wal_status: str = ""


@dataclass
class Instance:
    name: str
    role: str = "?"          # primary | replica | ?
    phase: str = "?"         # Running | Pending | ...
    ready: bool = False
    reason: str = ""         # CrashLoopBackOff, etc.
    pvc_size_bytes: int | None = None
    pvc_used_bytes: int | None = None
    pvc_pct: float | None = None
    wal_bytes: int | None = None

    @property
    def wal_frac(self) -> float | None:
        if self.wal_bytes is None or not self.pvc_size_bytes:
            return None
        return 100.0 * self.wal_bytes / self.pvc_size_bytes


@dataclass
class ReplicaLag:
    app: str
    state: str
    lag_bytes: int | None


@dataclass
class Cluster:
    namespace: str
    name: str
    desired: int = 0
    ready: int = 0
    primary: str = ""
    phase: str = ""
    storage_size: str = ""
    continuous_archiving: str | None = None   # condition status: True/False/None
    last_backup_succeeded: str | None = None
    last_backup_age: float | None = None
    backup_ts_bad: bool = False   # lastSuccessfulBackup present but unparseable
    backup_configured: bool = False
    instances: list[Instance] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    lags: list[ReplicaLag] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)   # (severity, text)
    exec_ok: bool = True

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"

    @property
    def primary_instance(self) -> Instance | None:
        for inst in self.instances:
            if inst.name == self.primary:
                return inst
        return None


# ---------------------------------------------------------------------------
# Discovery (CRD + pods) — cheap, no exec
# ---------------------------------------------------------------------------
def discover(kc: Kubectl, ns: str | None, name: str | None) -> list[Cluster]:
    if ns:
        cl = kc.json("get", "clusters.postgresql.cnpg.io", "-n", ns, "-o", "json")
    else:
        cl = kc.json("get", "clusters.postgresql.cnpg.io", "-A", "-o", "json")
    items = cl.get("items", [])

    pods = kc.json("get", "pods", "-A", "-l", "cnpg.io/cluster", "-o", "json")
    pods_by_cluster: dict[tuple[str, str], list[dict]] = {}
    for p in pods.get("items", []):
        meta = p.get("metadata", {})
        labels = meta.get("labels", {})
        ckey = (meta.get("namespace", ""), labels.get("cnpg.io/cluster", ""))
        pods_by_cluster.setdefault(ckey, []).append(p)

    now = time.time()
    clusters: list[Cluster] = []
    for it in items:
        meta = it.get("metadata", {})
        spec = it.get("spec", {})
        status = it.get("status", {})
        cname = meta.get("name", "")
        cns = meta.get("namespace", "")
        if name and cname != name:
            continue

        conds = {c.get("type"): c.get("status") for c in status.get("conditions", [])}
        backup = spec.get("backup") or {}
        c = Cluster(
            namespace=cns,
            name=cname,
            desired=int(spec.get("instances", 0) or 0),
            ready=int(status.get("readyInstances", 0) or 0),
            primary=status.get("currentPrimary", "") or "",
            phase=status.get("phase", "") or "",
            storage_size=spec.get("storage", {}).get("size", "") or "",
            continuous_archiving=conds.get("ContinuousArchiving"),
            last_backup_succeeded=conds.get("LastBackupSucceeded"),
            backup_configured=bool(backup),
        )
        lb = status.get("lastSuccessfulBackup")
        if lb:
            ts = _parse_ts(lb)
            if ts is not None:
                c.last_backup_age = now - ts
            else:
                # Present-but-unparseable timestamp: DON'T render age~0 ("fresh"),
                # which would mask a genuinely stale backup. Surface it instead.
                c.backup_ts_bad = True

        # Instances from pods (authoritative for phase/readiness/role).
        for p in pods_by_cluster.get((cns, cname), []):
            pmeta = p.get("metadata", {})
            pstat = p.get("status", {})
            labels = pmeta.get("labels", {})
            role = labels.get("cnpg.io/instanceRole") or labels.get("role") or "?"
            cstatuses = pstat.get("containerStatuses", []) or []
            ready = all(cs.get("ready") for cs in cstatuses) and bool(cstatuses)
            reason = ""
            for cs in cstatuses:
                waiting = (cs.get("state", {}) or {}).get("waiting")
                if waiting and waiting.get("reason"):
                    reason = waiting["reason"]
                    break
            c.instances.append(Instance(
                name=pmeta.get("name", ""),
                role=role,
                phase=pstat.get("phase", "?"),
                ready=ready,
                reason=reason,
            ))
        c.instances.sort(key=lambda i: i.name)
        clusters.append(c)

    clusters.sort(key=lambda c: c.key)
    return clusters


def _parse_ts(ts: str) -> float | None:
    # CNPG timestamps: "2026-07-09T05:23:01Z" or with offset.
    # Returns None on an unrecognized format — the caller must treat a missing
    # age as UNKNOWN, never as "just backed up" (which would hide a stale backup).
    import datetime as _dt
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = _dt.datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Enrichment (exec) — per-instance df/du + per-primary psql
# ---------------------------------------------------------------------------
_DF_DU = (
    f"df -B1 --output=size,used,pcent {PGDATA} | tail -1; "
    f"du -sb {PGWAL} 2>/dev/null | cut -f1"
)

_SLOTS_SQL = (
    "SELECT slot_name, active, "
    "pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint, "
    "coalesce(wal_status,'') "
    "FROM pg_replication_slots ORDER BY active, slot_name"
)
_LAG_SQL = (
    "SELECT application_name, state, "
    "pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint "
    "FROM pg_stat_replication ORDER BY application_name"
)


def enrich(kc: Kubectl, clusters: list[Cluster]) -> None:
    inst_jobs: list[tuple[Cluster, Instance]] = []
    for c in clusters:
        for inst in c.instances:
            if inst.phase == "Running":
                inst_jobs.append((c, inst))

    with concurrent.futures.ThreadPoolExecutor(max_workers=EXEC_WORKERS) as ex:
        list(ex.map(lambda j: _enrich_instance_safe(kc, *j), inst_jobs))
        prim_jobs = [c for c in clusters if c.primary_instance
                     and c.primary_instance.phase == "Running"]
        list(ex.map(lambda c: _enrich_primary_safe(kc, c), prim_jobs))


def _enrich_instance_safe(kc: Kubectl, c: Cluster, inst: Instance) -> None:
    # A worker exception (unexpected output shape, kube-client edge case) must
    # NOT abort the whole fleet scan via ex.map re-raising — degrade this one
    # instance to a failed probe and keep going.
    try:
        _enrich_instance(kc, c, inst)
    except Exception as exc:  # noqa: BLE001 — intentional: never abort the fleet
        c.exec_ok = False
        print(f"warn: probe failed for {c.key}/{inst.name}: {exc}", file=sys.stderr)


def _enrich_primary_safe(kc: Kubectl, c: Cluster) -> None:
    try:
        _enrich_primary(kc, c)
    except Exception as exc:  # noqa: BLE001 — intentional: never abort the fleet
        c.exec_ok = False
        print(f"warn: primary probe failed for {c.key}: {exc}", file=sys.stderr)


def _enrich_instance(kc: Kubectl, c: Cluster, inst: Instance) -> None:
    ok, out = kc.exec(c.namespace, inst.name, _DF_DU)
    if not ok:
        c.exec_ok = False
        return
    lines = [ln for ln in out.splitlines() if ln.strip()]
    parts = lines[0].split() if lines else []
    if len(parts) >= 3:
        try:
            inst.pvc_size_bytes = int(parts[0])
            inst.pvc_used_bytes = int(parts[1])
            inst.pvc_pct = float(parts[2].rstrip("%"))
        except ValueError:
            pass
    if len(lines) >= 2:
        try:
            inst.wal_bytes = int(lines[1].strip())
        except ValueError:
            pass
    if inst.pvc_pct is None:
        # The exec SUCCEEDED but produced no usable df output — an unexpected pod
        # layout (PGDATA elsewhere) or empty result. Surface it as a failed probe
        # so the cluster degrades to UNKNOWN with a note, rather than silently
        # rendering "?" and reading as healthy.
        c.exec_ok = False


def _enrich_primary(kc: Kubectl, c: Cluster) -> None:
    prim = c.primary_instance
    if prim is None:
        return
    ok, out = kc.psql(c.namespace, prim.name, _SLOTS_SQL)
    if ok:
        for ln in out.splitlines():
            if not ln.strip():
                continue
            f = ln.split("|")
            if len(f) < 4:
                continue
            c.slots.append(Slot(
                name=f[0],
                active=(f[1] == "t"),
                retained_bytes=_int_or_none(f[2]),
                wal_status=f[3],
            ))
    else:
        c.exec_ok = False
    ok, out = kc.psql(c.namespace, prim.name, _LAG_SQL)
    if ok:
        for ln in out.splitlines():
            if not ln.strip():
                continue
            f = ln.split("|")
            if len(f) < 3:
                continue
            c.lags.append(ReplicaLag(app=f[0], state=f[1], lag_bytes=_int_or_none(f[2])))


def _int_or_none(s: str) -> int | None:
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
def evaluate(c: Cluster) -> str:
    sev = OK

    # Instances: primary present + ready == desired.
    if c.desired and c.ready < c.desired:
        short = c.desired - c.ready
        prim = c.primary_instance
        if c.ready == 0 or (prim is not None and not prim.ready):
            sev = worst(sev, CRIT)
            c.notes.append((CRIT, f"{c.ready}/{c.desired} instances ready — primary not ready"))
        else:
            sev = worst(sev, WARN)
            c.notes.append((WARN, f"{c.ready}/{c.desired} instances ready ({short} missing)"))
    for inst in c.instances:
        if inst.reason and "CrashLoop" in inst.reason:
            sev = worst(sev, CRIT)
            c.notes.append((CRIT, f"{inst.name}: {inst.reason}"))
        elif inst.phase == "Running" and not inst.ready:
            sev = worst(sev, WARN)
            c.notes.append((WARN, f"{inst.name}: Running but NotReady"))
        elif inst.phase not in ("Running", "?"):
            sev = worst(sev, WARN)
            c.notes.append((WARN, f"{inst.name}: {inst.phase}"
                                  + (f" ({inst.reason})" if inst.reason else "")))

    # Inactive replication slots pinning WAL — the smoking gun.
    for s in c.slots:
        if s.active:
            continue
        rb = s.retained_bytes or 0
        if (s.wal_status == "lost") or rb >= SLOT_CRIT_BYTES:
            sev = worst(sev, CRIT)
            c.notes.append((CRIT, f"inactive slot {s.name} pinning "
                                  f"{human_bytes(rb)} of WAL"
                                  + (f" (wal_status={s.wal_status})" if s.wal_status else "")))
        elif rb >= SLOT_WARN_BYTES:
            sev = worst(sev, WARN)
            c.notes.append((WARN, f"inactive slot {s.name} pinning {human_bytes(rb)} of WAL"))

    # PVC fill + WAL fraction, per instance.
    for inst in c.instances:
        if inst.pvc_pct is not None:
            if inst.pvc_pct >= PVC_CRIT:
                sev = worst(sev, CRIT)
                c.notes.append((CRIT, f"{inst.name} PVC {inst.pvc_pct:.0f}% full"))
            elif inst.pvc_pct >= PVC_WARN:
                sev = worst(sev, WARN)
                c.notes.append((WARN, f"{inst.name} PVC {inst.pvc_pct:.0f}% full"))
        # WAL fraction is a WARN-only supporting signal (see threshold comment):
        # it never escalates the verdict to CRIT on its own.
        wf = inst.wal_frac
        if wf is not None and wf >= WAL_FRAC_WARN:
            sev = worst(sev, WARN)
            c.notes.append((WARN, f"{inst.name} pg_wal is {wf:.0f}% of the volume "
                                  f"({human_bytes(inst.wal_bytes)}) — limited headroom"))

    # Replication lag on streaming replicas.
    for lag in c.lags:
        lb = lag.lag_bytes or 0
        if lb >= LAG_CRIT_BYTES:
            sev = worst(sev, CRIT)
            c.notes.append((CRIT, f"replica {lag.app} {human_bytes(lb)} behind primary"))
        elif lb >= LAG_WARN_BYTES:
            sev = worst(sev, WARN)
            c.notes.append((WARN, f"replica {lag.app} {human_bytes(lb)} behind primary"))

    # Backups / continuous archiving.
    if c.continuous_archiving == "False":
        sev = worst(sev, WARN)
        c.notes.append((WARN, "continuous archiving unhealthy (ContinuousArchiving=False)"))
    if c.last_backup_succeeded == "False":
        sev = worst(sev, WARN)
        c.notes.append((WARN, "last backup failed (LastBackupSucceeded=False)"))
    if c.backup_configured and c.last_backup_age is not None:
        if c.last_backup_age >= BACKUP_CRIT_AGE:
            sev = worst(sev, CRIT)
            c.notes.append((CRIT, f"last successful backup {human_age(c.last_backup_age)} ago"))
        elif c.last_backup_age >= BACKUP_WARN_AGE:
            sev = worst(sev, WARN)
            c.notes.append((WARN, f"last successful backup {human_age(c.last_backup_age)} ago"))

    if c.backup_configured and c.backup_ts_bad:
        sev = worst(sev, WARN)
        c.notes.append((WARN, "last-backup timestamp unparseable — backup age unknown"))

    # A failed probe is ALWAYS surfaced (raised to at least UNKNOWN, note added),
    # regardless of the verdict from the signals that DID land. Previously this
    # only fired when the verdict was still OK, so a WARN/CRIT cluster could hide
    # that its PVC/WAL/slot data was never actually collected.
    if not c.exec_ok:
        sev = worst(sev, UNKNOWN)
        c.notes.append((UNKNOWN, "some instance probes failed (exec) — data partial"))

    return sev


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _slots_cell(c: Cluster) -> str:
    if not c.slots:
        return _c("-", C.DIM)
    inactive = [s for s in c.slots if not s.active]
    active = len(c.slots) - len(inactive)
    if not inactive:
        return f"{active}a"
    pinned = max((s.retained_bytes or 0) for s in inactive)
    code = C.RED if pinned >= SLOT_CRIT_BYTES else C.YELLOW
    return _c(f"{len(inactive)}!{human_bytes(pinned)}", code)


def _pvc_cell(c: Cluster) -> str:
    pcts = [i.pvc_pct for i in c.instances if i.pvc_pct is not None]
    if not pcts:
        return _c("?", C.DIM)
    p = max(pcts)
    code = C.RED if p >= PVC_CRIT else (C.YELLOW if p >= PVC_WARN else "")
    return _c(f"{p:.0f}%", code) if code else f"{p:.0f}%"


def _wal_cell(c: Cluster) -> str:
    fracs = [i.wal_frac for i in c.instances if i.wal_frac is not None]
    if not fracs:
        return _c("?", C.DIM)
    p = max(fracs)
    code = C.YELLOW if p >= WAL_FRAC_WARN else ""
    return _c(f"{p:.0f}%", code) if code else f"{p:.0f}%"


def _lag_cell(c: Cluster) -> str:
    if not c.lags:
        return _c("-", C.DIM)
    m = max((lag.lag_bytes or 0) for lag in c.lags)
    code = C.RED if m >= LAG_CRIT_BYTES else (C.YELLOW if m >= LAG_WARN_BYTES else "")
    return _c(human_bytes(m), code) if code else human_bytes(m)


def _backup_cell(c: Cluster) -> str:
    if c.last_backup_age is not None:
        age = human_age(c.last_backup_age)
        code = (C.RED if c.last_backup_age >= BACKUP_CRIT_AGE
                else C.YELLOW if c.last_backup_age >= BACKUP_WARN_AGE else "")
        return _c(age, code) if code else age
    if c.continuous_archiving == "True":
        return _c("arch", C.GREEN)
    if c.continuous_archiving == "False":
        return _c("arch!", C.RED)
    return _c("none", C.DIM)


def _inst_cell(c: Cluster) -> str:
    txt = f"{c.ready}/{c.desired}"
    if c.desired and c.ready < c.desired:
        return _c(txt, C.RED if c.ready == 0 else C.YELLOW)
    return txt


def _visible_len(s: str) -> int:
    out, i = 0, 0
    while i < len(s):
        if s[i] == "\033":
            j = s.find("m", i)
            i = len(s) if j == -1 else j + 1
        else:
            out += 1
            i += 1
    return out


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _visible_len(s))


def render_table(clusters: list[Cluster], verdicts: dict[str, str]) -> str:
    headers = ["CLUSTER", "INST", "PVC", "WAL", "SLOTS", "LAG", "BACKUP", "VERDICT"]
    rows = []
    for c in clusters:
        rows.append([
            c.key,
            _inst_cell(c),
            _pvc_cell(c),
            _wal_cell(c),
            _slots_cell(c),
            _lag_cell(c),
            _backup_cell(c),
            paint_sev(verdicts[c.key]),
        ])
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], _visible_len(cell))

    lines = ["  ".join(_pad(_c(h, C.BOLD), widths[i]) for i, h in enumerate(headers))]
    for r in rows:
        lines.append("  ".join(_pad(cell, widths[i]) for i, cell in enumerate(r)))
    return "\n".join(lines)


def render(clusters: list[Cluster], verdicts: dict[str, str], details: bool) -> str:
    counts = {OK: 0, WARN: 0, CRIT: 0, UNKNOWN: 0}
    for v in verdicts.values():
        counts[v] = counts.get(v, 0) + 1

    out = []
    header = _c("cnpgscope", C.BOLD, C.CYAN)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    out.append(f"{header} — CloudNativePG fleet health @ {stamp}  "
               f"({len(clusters)} clusters)")
    out.append("")
    out.append(render_table(clusters, verdicts))
    out.append("")

    summary = (f"Fleet: {_c(str(counts[CRIT]) + ' CRITICAL', C.BOLD, C.RED)}, "
               f"{_c(str(counts[WARN]) + ' WARN', C.BOLD, C.YELLOW)}, "
               f"{_c(str(counts[OK]) + ' OK', C.BOLD, C.GREEN)}")
    if counts[UNKNOWN]:
        summary += f", {_c(str(counts[UNKNOWN]) + ' UNKNOWN', C.DIM)}"
    out.append(summary)

    # Detail: why each non-OK cluster is flagged (all clusters with --details).
    targets = clusters if details else [c for c in clusters if verdicts[c.key] != OK]
    if targets:
        out.append("")
        for c in targets:
            out.append(f"{paint_sev(verdicts[c.key])}  {_c(c.key, C.BOLD)}"
                       f"  (primary {c.primary or '-'})")
            for note_sev, text in c.notes:
                bullet = _c("*", sev_color(note_sev))
                out.append(f"    {bullet} {text}")
    return "\n".join(out)


def to_dict(c: Cluster, verdict: str) -> dict:
    return {
        "namespace": c.namespace,
        "name": c.name,
        "verdict": verdict,
        "desired": c.desired,
        "ready": c.ready,
        "primary": c.primary,
        "phase": c.phase,
        "storageSize": c.storage_size,
        "continuousArchiving": c.continuous_archiving,
        "lastBackupSucceeded": c.last_backup_succeeded,
        "lastBackupAgeSeconds": c.last_backup_age,
        "instances": [
            {
                "name": i.name, "role": i.role, "phase": i.phase, "ready": i.ready,
                "reason": i.reason, "pvcPct": i.pvc_pct,
                "pvcSizeBytes": i.pvc_size_bytes, "walBytes": i.wal_bytes,
                "walFracPct": i.wal_frac,
            }
            for i in c.instances
        ],
        "slots": [
            {"name": s.name, "active": s.active,
             "retainedBytes": s.retained_bytes, "walStatus": s.wal_status}
            for s in c.slots
        ],
        "replicationLag": [
            {"app": lag.app, "state": lag.state, "lagBytes": lag.lag_bytes}
            for lag in c.lags
        ],
        "notes": [{"severity": s, "text": t} for s, t in c.notes],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    global _USE_COLOR, PVC_WARN, PVC_CRIT
    ap = argparse.ArgumentParser(
        prog="cnpgscope",
        description="Read-only CloudNativePG fleet health at a glance.",
    )
    ap.add_argument("-n", "--namespace", help="limit to one namespace")
    ap.add_argument("--cluster", help="limit to one cluster by name")
    ap.add_argument("--context", help="kube-context to target")
    ap.add_argument("--kubectl", default=os.environ.get("KUBECTL", "kubectl"),
                    help="kubectl binary (default: kubectl)")
    ap.add_argument("--no-exec", action="store_true",
                    help="skip pod exec (CRD-only, fast, low-privilege)")
    ap.add_argument("-o", "--output", choices=["text", "json"], default="text")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--details", action="store_true",
                    help="always show per-cluster detail (default: non-OK only)")
    ap.add_argument("--pvc-warn", type=float, default=PVC_WARN)
    ap.add_argument("--pvc-crit", type=float, default=PVC_CRIT)
    ap.add_argument("--exit-zero", action="store_true",
                    help="always exit 0 (default: exit worst verdict)")
    args = ap.parse_args(argv)

    _USE_COLOR = (not args.no_color and sys.stdout.isatty()
                  and os.environ.get("NO_COLOR") is None)
    PVC_WARN, PVC_CRIT = args.pvc_warn, args.pvc_crit

    kc = Kubectl(args.kubectl, args.context)
    try:
        clusters = discover(kc, args.namespace, args.cluster)
    except FileNotFoundError:
        print(f"error: kubectl binary not found: {args.kubectl}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"error: kubectl failed: {exc.stderr or exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError:
        print("error: could not parse kubectl JSON output", file=sys.stderr)
        return 2

    if not clusters:
        print("No CloudNativePG clusters found.", file=sys.stderr)
        return 0

    if not args.no_exec:
        enrich(kc, clusters)

    verdicts = {c.key: evaluate(c) for c in clusters}

    if args.output == "json":
        payload = {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "clusterCount": len(clusters),
            "clusters": [to_dict(c, verdicts[c.key]) for c in clusters],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render(clusters, verdicts, args.details))

    if args.exit_zero:
        return 0
    overall = worst(*verdicts.values())
    # UNKNOWN → 1 (not 0): a fleet we couldn't actually probe must NOT pass a CI
    # or cron gate as if it were healthy. --exit-zero is the explicit opt-out.
    return {OK: 0, UNKNOWN: 1, WARN: 1, CRIT: 2}.get(overall, 0)


if __name__ == "__main__":
    sys.exit(main())
