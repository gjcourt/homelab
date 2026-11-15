#!/usr/bin/env python3
"""update-verifier: check that the cluster recovered after a UniFi device reboot.

Plan: docs/plans/2026-11-14-switch-update-observability-plan.md, phase 3.

Every INTERVAL_SECONDS the loop looks at the watched devices (the Rack Switch
and the gateway). When one has rebooted (uptime under MAX_UPTIME_SECONDS) and
that boot has no result yet, it waits until uptime reaches MIN_UPTIME_SECONDS,
runs the checks below through the Prometheus and Loki HTTP APIs, and records
one result per boot:

    homelab_update_verification{device, boot, from_version, to_version,
                                result, failed_checks}

The value is the boot's start time in unix seconds (not 1), so the
UpdateVerificationMissing deadman can match "a result for THIS boot" with a
numeric tolerance; see prometheus-rule.yaml for why exact label matching on
`boot` does not work.

Silence must never mean "could not tell". A query that errors, an API that is
unreachable, or Prometheus/Loki with no recent data is a FAIL with the reason
named in failed_checks. On 2026-11-13 the Rack Switch update blinded
Prometheus itself for ~18h (no samples 03:13-20:55), so a failure is also
POSTed straight to Alertmanager, and the reboot is detected from unpoller's
own /metrics when Prometheus is stale.

Standard library only: this runs from a ConfigMap on a stock python image.
"""

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- configuration -----------------------------------------------------------


def _env(name, default):
    return os.environ.get(name, default)


CONFIG = {
    "prom_url": _env("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring.svc:9090"),
    "loki_url": _env("LOKI_URL", "http://loki.monitoring.svc:3100"),
    "unpoller_url": _env("UNPOLLER_URL", "http://unpoller.monitoring.svc:9130/metrics"),
    "alertmanager_urls": [u for u in _env("ALERTMANAGER_URLS", "").split(",") if u],
    # Labels Prometheus stamps on every alert it sends (its external labels).
    # Adding the same ones to the direct POST gives both paths the same label
    # set, so Alertmanager deduplicates them into one alert and one email.
    "alert_extra_labels": json.loads(_env("ALERT_EXTRA_LABELS", "{}")),
    "devices": [d for d in _env("DEVICES", "Rack Switch|Home").split("|") if d],
    "interval": int(_env("INTERVAL_SECONDS", "300")),
    "min_uptime": int(_env("MIN_UPTIME_SECONDS", "900")),
    "max_uptime": int(_env("MAX_UPTIME_SECONDS", "2700")),
    "retain": int(_env("RETAIN_SECONDS", "10800")),
    "prom_stale": int(_env("PROMETHEUS_STALE_SECONDS", "180")),
    "loki_fresh_window": int(_env("LOKI_FRESH_WINDOW_SECONDS", "900")),
    "iscsi_grace": int(_env("ISCSI_GRACE_SECONDS", "600")),
    "port": int(_env("LISTEN_PORT", "9106")),
    "timeout": float(_env("HTTP_TIMEOUT_SECONDS", "20")),
}

# Unpoller's uptime jitters by up to ~5 min against the sample time (measured
# 2026-11-14: one boot's start ranged over 300 s), so "the same boot" is a
# start time within this many seconds, never an exact label match.
BOOT_TOLERANCE = 600

ISCSI_LOGQL = '{job="talos-kernel"} |~ "(?i)(iscsid|iscsi).*(error|failed|timeout|lost)"'
KERNEL_LOGQL = '{job="talos-kernel"}'

# Order is the order failed_checks lists them in.
CHECKS = (
    "prometheus_stale",
    "pvc_writable",
    "pvc_writeprobe_stale",
    "pvc_not_writable_alert",
    "nodes_ready",
    "etcd",
    "flux",
    "loki_error",
    "loki_stale",
    "iscsi_log_errors",
)


class ApiError(Exception):
    """A query failed: HTTP error, unreachable, timeout, or bad response."""


# --- HTTP clients --------------------------------------------------------------


def _http_get_json(url, timeout):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ApiError(f"GET {url.split('?')[0]}: {e}") from e
    if body.get("status") != "success":
        raise ApiError(f"GET {url.split('?')[0]}: {body.get('error', body)}")
    return body["data"]


class Api:
    """Thin wrapper over the HTTP APIs. Tests replace it with a fake."""

    def __init__(self, cfg):
        self.cfg = cfg

    def prom(self, expr, at=None):
        """Instant query. Returns [(labels, float_value)]."""
        p = {"query": expr}
        if at is not None:
            p["time"] = f"{at:.3f}"
        data = _http_get_json(f"{self.cfg['prom_url']}/api/v1/query?{urllib.parse.urlencode(p)}", self.cfg["timeout"])
        if data.get("resultType") == "scalar":
            return [({}, float(data["result"][1]))]
        return [(r["metric"], float(r["value"][1])) for r in data["result"]]

    def loki(self, expr, at):
        """Instant metric query. Returns [(labels, float_value)]."""
        p = {"query": expr, "time": str(int(at * 1e9))}
        data = _http_get_json(f"{self.cfg['loki_url']}/loki/api/v1/query?{urllib.parse.urlencode(p)}", self.cfg["timeout"])
        return [(r["metric"], float(r["value"][1])) for r in data["result"]]

    def unpoller_text(self):
        try:
            with urllib.request.urlopen(self.cfg["unpoller_url"], timeout=self.cfg["timeout"]) as r:
                return r.read().decode()
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(f"GET {self.cfg['unpoller_url']}: {e}") from e

    def post_alerts(self, url, alerts):
        req = urllib.request.Request(
            f"{url.rstrip('/')}/api/v2/alerts",
            data=json.dumps(alerts).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg["timeout"]) as r:
                r.read()
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(f"POST {url}: {e}") from e


# --- pure helpers --------------------------------------------------------------


def boot_id(start):
    """A boot's start time, rounded to the minute (unix seconds)."""
    return int(round(start / 60.0)) * 60


def same_boot(a, b):
    return abs(int(a) - int(b)) <= BOOT_TOLERANCE


def promql_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse_prom_text(text, metric):
    """Return [(labels, value)] for one metric from Prometheus text format."""
    out = []
    for line in text.splitlines():
        if not line.startswith(metric + "{") and not line.startswith(metric + " "):
            continue
        if "{" in line:
            lbl, _, rest = line[len(metric) + 1:].rpartition("}")
            labels = {k: v.replace('\\"', '"').replace("\\\\", "\\") for k, v in _LABEL_RE.findall(lbl)}
        else:
            labels, rest = {}, line[len(metric):]
        try:
            out.append((labels, float(rest.split()[0])))
        except (IndexError, ValueError):
            continue
    return out


class Device:
    def __init__(self, name, uptime, start, version, source):
        self.name = name
        self.uptime = uptime
        self.start = start  # unrounded boot start, unix seconds
        self.boot = boot_id(start)
        self.version = version
        self.source = source  # "prometheus" or "unpoller"


class Result:
    def __init__(self, device, boot, from_version, to_version, failed, verified_at):
        self.device = device
        self.boot = int(boot)
        self.from_version = from_version
        self.to_version = to_version
        self.failed = list(failed)
        self.verified_at = verified_at

    @property
    def result(self):
        return "fail" if self.failed else "pass"

    @property
    def failed_checks(self):
        return ",".join(self.failed)

    def labels(self):
        return {
            "device": self.device,
            "boot": str(self.boot),
            "from_version": self.from_version,
            "to_version": self.to_version,
            "result": self.result,
            "failed_checks": self.failed_checks,
        }


# --- Prometheus freshness ------------------------------------------------------


def prometheus_fresh(api, now, cfg):
    """True only if Prometheus answers AND holds a sample newer than prom_stale.

    An empty answer is stale: `max(timestamp(up))` only sees samples inside
    the 5m lookback, so a Prometheus that ingested nothing returns nothing.
    That is exactly what it returned at 2026-11-13 03:25.
    """
    try:
        r = api.prom("time() - max(timestamp(up))", at=now)
    except ApiError:
        return False
    return bool(r) and r[0][1] <= cfg["prom_stale"]


# --- device discovery ----------------------------------------------------------


def devices_from_prometheus(api, now, cfg):
    sel = "{name=~" + promql_str("|".join(re.escape(d) for d in cfg["devices"])) + "}"
    starts = api.prom(f"timestamp(unpoller_device_uptime_seconds{sel}) - unpoller_device_uptime_seconds{sel}", at=now)
    uptimes = {lb.get("name"): v for lb, v in api.prom(f"unpoller_device_uptime_seconds{sel}", at=now)}
    infos = {}
    for lb, _ in api.prom(f"unpoller_device_info{sel}", at=now):
        infos[lb.get("name")] = lb.get("version", "unknown")
    out = {}
    for lb, start in starts:
        name = lb.get("name")
        if name in uptimes:
            out[name] = Device(name, uptimes[name], start, infos.get(name, "unknown"), "prometheus")
    return list(out.values())


def devices_from_unpoller(api, now, cfg):
    text = api.unpoller_text()
    uptimes = {lb.get("name"): v for lb, v in parse_prom_text(text, "unpoller_device_uptime_seconds")}
    versions = {lb.get("name"): lb.get("version", "unknown") for lb, _ in parse_prom_text(text, "unpoller_device_info")}
    return [
        Device(name, uptimes[name], now - uptimes[name], versions.get(name, "unknown"), "unpoller")
        for name in cfg["devices"]
        if name in uptimes
    ]


def version_before(api, device, cfg):
    """Firmware the device ran just before this boot, from Prometheus history."""
    sel = "{name=" + promql_str(device.name) + "}"
    # The version whose series has the latest sample before the boot. More
    # than one series per device is normal (unpoller restarts, a WAN IP
    # change on the gateway), so pick by sample time, not by series.
    expr = f"max by (version) (max_over_time(timestamp(unpoller_device_info{sel})[1h:1m]))"
    try:
        rows = api.prom(expr, at=device.start - 120)
    except ApiError:
        return None
    if not rows:
        return None
    return max(rows, key=lambda r: r[1])[0].get("version")


# --- checks --------------------------------------------------------------------


def _count(rows):
    return int(rows[0][1]) if rows else 0


def run_checks(api, boot, now, prom_ok, cfg):
    """Return the names of failed checks, in CHECKS order. Empty = pass."""
    failed = set()
    if not prom_ok:
        # Every Prometheus-backed check would be judged on missing or frozen
        # data, which proves nothing either way; prometheus_stale says so.
        failed.add("prometheus_stale")
    else:
        def check(name, fn):
            try:
                if not fn():
                    failed.add(name)
            except ApiError:
                failed.add(name)

        # Every probed PVC writable, and at least one probed at all.
        check("pvc_writable", lambda: (
            _count(api.prom("count(homelab_pvc_writable)", at=now)) > 0
            and _count(api.prom("count(homelab_pvc_writable != 1)", at=now)) == 0
        ))
        # The sweep must have finished after the reboot, or the result above
        # describes the volumes before the switch went down.
        check("pvc_writeprobe_stale", lambda: (
            lambda rows: bool(rows) and rows[0][1] >= boot
        )(api.prom("max(homelab_pvc_writeprobe_last_run_seconds)", at=now)))
        check("pvc_not_writable_alert", lambda: (
            _count(api.prom('count(ALERTS{alertname="PvcNotWritable"})', at=now)) == 0
        ))
        check("nodes_ready", lambda: (
            lambda total, ready: total > 0 and ready == total
        )(
            _count(api.prom("count(kube_node_info)", at=now)),
            _count(api.prom('count(kube_node_status_condition{condition="Ready",status="true"} == 1)', at=now)),
        ))
        check("etcd", lambda: (
            _count(api.prom("count(etcd_server_has_leader == 1)", at=now)) == 3
            and _count(api.prom("sum(etcd_server_is_leader)", at=now)) == 1
        ))
        check("flux", lambda: _flux_ok(api, boot, now))

    # Loki is checked whatever Prometheus says: it is a separate store.
    try:
        fresh = _count(api.loki(f"sum(count_over_time({KERNEL_LOGQL}[{cfg['loki_fresh_window']}s]))", at=now))
        if fresh == 0:
            # No kernel lines at all means the iSCSI check below would pass
            # by seeing nothing, so it is a failure in its own right.
            failed.add("loki_stale")
        else:
            window = int(now - (boot + cfg["iscsi_grace"]))
            if window > 0:
                errors = _count(api.loki(f"sum(count_over_time({ISCSI_LOGQL}[{window}s]))", at=now))
                if errors > 0:
                    failed.add("iscsi_log_errors")
    except ApiError:
        failed.add("loki_error")

    return [c for c in CHECKS if c in failed]


def _flux_keys(rows):
    return {(lb.get("customresource_kind"), lb.get("exported_namespace"), lb.get("name")) for lb, _ in rows}


def _flux_ok(api, boot, now):
    sel = 'customresource_kind=~"Kustomization|HelmRelease"'
    if _count(api.prom(f"count(gotk_resource_info{{{sel}}})", at=now)) == 0:
        return False  # no data is not "all ready"
    bad_now = _flux_keys(api.prom(f'gotk_resource_info{{{sel},ready!="True"}}', at=now))
    if not bad_now:
        return True
    # Only failures that are new since the boot count.
    bad_before = _flux_keys(api.prom(f'last_over_time(gotk_resource_info{{{sel},ready!="True"}}[10m])', at=boot - 60))
    return not (bad_now - bad_before)


# --- state and the loop --------------------------------------------------------


class State:
    def __init__(self):
        self.results = {}  # (device, boot) -> Result
        self.last_seen = {}  # device -> (boot, version), for from_version without Prometheus
        self.last_loop = 0.0
        self.prom_fresh = 0

    def find(self, device, boot):
        for (d, b), r in self.results.items():
            if d == device and same_boot(b, boot):
                return r
        return None


def read_back(api, state, now, cfg):
    """Load results already in Prometheus, so a restart does not re-run a boot."""
    rows = api.prom(f"max_over_time(homelab_update_verification[{cfg['retain']}s])", at=now)
    for lb, value in rows:
        device = lb.get("device")
        try:
            boot = int(lb.get("boot") or value)
        except ValueError:
            continue
        if device and state.find(device, boot) is None:
            failed = [c for c in (lb.get("failed_checks") or "").split(",") if c]
            if lb.get("result") == "fail" and not failed:
                failed = ["unknown"]
            state.results[(device, boot)] = Result(
                device, boot, lb.get("from_version", "unknown"), lb.get("to_version", "unknown"), failed, now
            )


def step(api, state, now, cfg, log=print):
    """One loop iteration. Returns the Results recorded in this iteration."""
    state.last_loop = now
    prom_ok = prometheus_fresh(api, now, cfg)
    state.prom_fresh = 1 if prom_ok else 0

    devices = []
    if prom_ok:
        try:
            read_back(api, state, now, cfg)
            devices = devices_from_prometheus(api, now, cfg)
        except ApiError as e:
            log(json.dumps({"msg": "prometheus query failed", "error": str(e)}))
            prom_ok = False
            state.prom_fresh = 0
    if not prom_ok:
        try:
            devices = devices_from_unpoller(api, now, cfg)
        except ApiError as e:
            # Neither source can see the devices: nothing to verify against.
            # The Prometheus-side deadman cannot help here either; this log
            # line and the Alertmanager-independent healthchecks.io deadman
            # are what is left.
            log(json.dumps({"msg": "cannot read device uptime", "error": str(e)}))
            devices = []

    recorded = []
    for dev in devices:
        prev = state.last_seen.get(dev.name)
        if prev is None or not same_boot(prev[0], dev.boot):
            state.last_seen[dev.name] = (dev.boot, dev.version)
        if dev.uptime >= cfg["max_uptime"] or dev.uptime < cfg["min_uptime"]:
            continue
        if state.find(dev.name, dev.boot) is not None:
            continue
        from_version = version_before(api, dev, cfg) if prom_ok else None
        if from_version is None:
            from_version = prev[1] if prev is not None and not same_boot(prev[0], dev.boot) else "unknown"
        failed = run_checks(api, dev.boot, now, prom_ok, cfg)
        res = Result(dev.name, dev.boot, from_version, dev.version, failed, now)
        state.results[(dev.name, dev.boot)] = res
        recorded.append(res)
        log(json.dumps({
            "msg": "update verification",
            **res.labels(),
            "uptime_seconds": int(dev.uptime),
            "device_source": dev.source,
        }))

    # Forget results once their boot is older than the deadman can look back.
    for key in [k for k, r in state.results.items() if now - r.boot > cfg["retain"]]:
        del state.results[key]

    notify_alertmanager(api, state, now, cfg, log)
    return recorded


def alert_payload(res, now, cfg):
    labels = {
        "alertname": "UpdateVerificationFailed",
        "severity": "critical",
        "namespace": "monitoring",
        "device": res.device,
        "boot": str(res.boot),
        "from_version": res.from_version,
        "to_version": res.to_version,
        "failed_checks": res.failed_checks,
        **cfg["alert_extra_labels"],
    }
    return {
        "labels": labels,
        "annotations": annotations(res),
        "startsAt": _rfc3339(res.verified_at),
        # Re-sent every loop while retained, so it resolves on its own only
        # when the verifier stops sending (boot older than RETAIN_SECONDS).
        "endsAt": _rfc3339(now + 3 * cfg["interval"]),
    }


def annotations(res):
    return {
        "summary": f"{res.device} rebooted and the cluster did not verify clean: {res.failed_checks}",
        "description": (
            f"{res.device} booted at {_rfc3339(res.boot)} "
            f"(firmware {res.from_version} -> {res.to_version}). "
            f"update-verifier checks failed: {res.failed_checks}. "
            "prometheus_stale / loki_* mean the check could not be made, which is not a pass. "
            "Runbook: AGENTS.md \"Recovering read-only iSCSI volumes\"."
        ),
    }


def notify_alertmanager(api, state, now, cfg, log=print):
    fails = [alert_payload(r, now, cfg) for r in state.results.values() if r.failed]
    if not fails:
        return
    for url in cfg["alertmanager_urls"]:
        try:
            api.post_alerts(url, fails)
        except ApiError as e:
            log(json.dumps({"msg": "alertmanager post failed", "error": str(e)}))


def _rfc3339(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# --- metrics -------------------------------------------------------------------


def _esc(v):
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render_metrics(state):
    lines = [
        "# HELP homelab_update_verification One update-verifier result per device boot. "
        "The value is the boot start time in unix seconds.",
        "# TYPE homelab_update_verification gauge",
    ]
    for r in sorted(state.results.values(), key=lambda r: (r.device, r.boot)):
        lb = ",".join(f'{k}="{_esc(v)}"' for k, v in r.labels().items())
        lines.append(f"homelab_update_verification{{{lb}}} {r.boot}")
    lines += [
        "# HELP homelab_update_verifier_last_loop_seconds Unix time the verifier loop last ran.",
        "# TYPE homelab_update_verifier_last_loop_seconds gauge",
        f"homelab_update_verifier_last_loop_seconds {int(state.last_loop)}",
        "# HELP homelab_update_verifier_prometheus_fresh 1 if Prometheus had recent data on the last loop.",
        "# TYPE homelab_update_verifier_prometheus_fresh gauge",
        f"homelab_update_verifier_prometheus_fresh {state.prom_fresh}",
    ]
    return "\n".join(lines) + "\n"


def serve(state, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_response(404)
                self.end_headers()
                return
            body = render_metrics(state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main(argv):
    cfg = CONFIG
    api = Api(cfg)
    state = State()
    if "--once" in argv:
        # Replay/diagnostic mode: `--once [--at <unix>]` runs one iteration and
        # prints the metrics, without posting to Alertmanager.
        at = float(argv[argv.index("--at") + 1]) if "--at" in argv else time.time()
        cfg = dict(cfg, alertmanager_urls=[])
        if "--at" in argv:
            # unpoller only has the present; never mix it into a replay.
            cfg["unpoller_url"] = "http://unpoller-not-used-in-replay.invalid/metrics"
        step(Api(cfg), state, at, cfg)
        sys.stdout.write(render_metrics(state))
        return 0
    serve(state, cfg["port"])
    print(json.dumps({"msg": "update-verifier started", "devices": cfg["devices"], "interval": cfg["interval"]}), flush=True)
    while True:
        try:
            step(api, state, time.time(), cfg, log=lambda s: print(s, flush=True))
        except Exception as e:  # keep the loop alive; the deadman covers a wedged verifier
            print(json.dumps({"msg": "loop error", "error": repr(e)}), flush=True)
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
