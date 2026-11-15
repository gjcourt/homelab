"""Offline tests for update-verifier's decision logic, against fake API responses.

Run: python -m pytest -q infra/configs/update-verifier/tests
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import verifier as v  # noqa: E402

# Ground truth: the Rack Switch's 2026-11-13 update. Boot start 03:10:50 PST.
BOOT = 1794568250.8
OLD, NEW = "7.5.15.17146", "7.6.11.17272"

CFG = dict(
    v.CONFIG,
    devices=["Rack Switch", "Home"],
    alertmanager_urls=["http://am-0:9093", "http://am-1:9093"],
    alert_extra_labels={"prometheus": "monitoring/kube-prometheus-stack-prometheus"},
)


class FakeApi:
    """Answers each query from a table of substring -> rows (or an exception).

    Defaults describe a healthy cluster 20 minutes after the switch rebooted.
    Tests override single entries to create each failure.
    """

    def __init__(self, uptime=1200.0, **overrides):
        self.uptime = uptime
        self.posts = []
        self.prom_calls = []
        self.unpoller_error = None
        self.unpoller_uptime = uptime
        self.history = []  # homelab_update_verification read-back rows
        self.table = {
            "time() - max(timestamp(up))": [({}, 0.3)],
            "count(homelab_pvc_writable)": [({}, 91)],
            "count(homelab_pvc_writable != 1)": [],
            "max(homelab_pvc_writeprobe_last_run_seconds)": [({}, BOOT + 1100)],
            'count(ALERTS{alertname="PvcNotWritable"})': [],
            "count(kube_node_info)": [({}, 4)],
            'count(kube_node_status_condition{condition="Ready",status="true"} == 1)': [({}, 4)],
            "count(etcd_server_has_leader == 1)": [({}, 3)],
            "sum(etcd_server_is_leader)": [({}, 1)],
            "count(gotk_resource_info{": [({}, 19)],
            'gotk_resource_info{customresource_kind=~"Kustomization|HelmRelease",ready!="True"}': [],
            FLUX_BEFORE: [],
            "max by (version)": [({"version": OLD}, BOOT - 150)],
        }
        self.loki_table = {
            "fresh": [({}, 5000)],
            "iscsi": [],
        }
        self.table.update(overrides)

    # Prometheus
    def prom(self, expr, at=None):
        self.prom_calls.append(expr)
        if expr.startswith("max_over_time(homelab_update_verification"):
            return self.history
        if expr.startswith("timestamp(unpoller_device_uptime_seconds"):
            return self._ret([({"name": "Rack Switch"}, BOOT)])
        if expr.startswith("unpoller_device_uptime_seconds"):
            return self._ret([({"name": "Rack Switch"}, self.uptime)])
        if expr.startswith("unpoller_device_info"):
            return self._ret([({"name": "Rack Switch", "version": NEW}, 1)])
        if expr in self.table:
            return self._ret(self.table[expr])
        for key in sorted(self.table, key=len, reverse=True):  # most specific first
            if key in expr:
                return self._ret(self.table[key])
        raise AssertionError(f"unexpected query: {expr}")

    def _ret(self, rows):
        if isinstance(rows, Exception):
            raise rows
        return rows

    # Loki
    def loki(self, expr, at):
        key = "iscsi" if "iscsi" in expr else "fresh"
        return self._ret(self.loki_table[key])

    # unpoller
    def unpoller_text(self):
        if self.unpoller_error:
            raise self.unpoller_error
        return (
            '# HELP unpoller_device_uptime_seconds Uptime\n'
            f'unpoller_device_uptime_seconds{{name="Rack Switch",site_name="Default (default)",type="usw"}} {self.unpoller_uptime}\n'
            f'unpoller_device_uptime_seconds{{name="Office Switch",type="usw"}} 999999\n'
            f'unpoller_device_info{{ip="10.42.1.100",name="Rack Switch",type="usw",version="{NEW}"}} 1\n'
        )

    def post_alerts(self, url, alerts):
        self.posts.append((url, alerts))


FLUX_BEFORE = 'last_over_time(gotk_resource_info{customresource_kind=~"Kustomization|HelmRelease",ready!="True"}[10m])'
NOW = BOOT + 1200  # uptime 20 min


def run(api, state=None, now=NOW):
    state = state or v.State()
    logs = []
    recorded = v.step(api, state, now, CFG, log=logs.append)
    return state, recorded, logs


# --- pass ----------------------------------------------------------------------


def test_pass_records_one_silent_result():
    api = FakeApi()
    state, rec, logs = run(api)
    assert len(rec) == 1
    r = rec[0]
    assert r.result == "pass" and r.failed_checks == ""
    assert r.device == "Rack Switch"
    assert r.boot == 1794568260  # 03:10:50.8 rounded to the minute
    assert (r.from_version, r.to_version) == (OLD, NEW)
    assert api.posts == []  # pass is silent: nothing to Alertmanager
    assert any('"result": "pass"' in line for line in logs)  # the structured log line
    m = v.render_metrics(state)
    assert ('homelab_update_verification{device="Rack Switch",boot="1794568260",from_version="7.5.15.17146",'
            'to_version="7.6.11.17272",result="pass",failed_checks=""} 1794568260') in m


def test_result_not_repeated_on_next_loop():
    api = FakeApi()
    state, rec, _ = run(api)
    _, rec2, _ = run(api, state, now=NOW + 300)
    assert len(rec) == 1 and rec2 == []


# --- each failure --------------------------------------------------------------


@pytest.mark.parametrize("override,expected", [
    ({"count(homelab_pvc_writable != 1)": [({}, 3)]}, "pvc_writable"),
    ({"count(homelab_pvc_writable)": []}, "pvc_writable"),  # no probe data is not a pass
    ({"max(homelab_pvc_writeprobe_last_run_seconds)": [({}, BOOT - 30)]}, "pvc_writeprobe_stale"),
    ({"max(homelab_pvc_writeprobe_last_run_seconds)": []}, "pvc_writeprobe_stale"),
    ({'count(ALERTS{alertname="PvcNotWritable"})': [({}, 1)]}, "pvc_not_writable_alert"),
    ({'count(kube_node_status_condition{condition="Ready",status="true"} == 1)': [({}, 3)]}, "nodes_ready"),
    ({"count(kube_node_info)": []}, "nodes_ready"),
    ({"count(etcd_server_has_leader == 1)": [({}, 2)]}, "etcd"),
    ({"sum(etcd_server_is_leader)": [({}, 0)]}, "etcd"),
    ({'gotk_resource_info{customresource_kind=~"Kustomization|HelmRelease",ready!="True"}':
      [({"customresource_kind": "HelmRelease", "exported_namespace": "monitoring", "name": "loki"}, 1)]}, "flux"),
    ({"count(gotk_resource_info{": []}, "flux"),
    ({"count(etcd_server_has_leader == 1)": v.ApiError("boom")}, "etcd"),  # a query error is a fail
])
def test_each_prometheus_check_fails(override, expected):
    api = FakeApi(**override)
    _, rec, _ = run(api)
    assert rec[0].result == "fail"
    assert rec[0].failed == [expected]


def test_flux_failure_present_before_boot_is_not_new():
    bad = [({"customresource_kind": "HelmRelease", "exported_namespace": "plane-stage", "name": "plane"}, 1)]
    api = FakeApi(**{
        'gotk_resource_info{customresource_kind=~"Kustomization|HelmRelease",ready!="True"}': bad,
        FLUX_BEFORE: bad,
    })
    _, rec, _ = run(api)
    assert rec[0].result == "pass"


def test_iscsi_log_errors_fail():
    api = FakeApi()
    api.loki_table["iscsi"] = [({}, 12)]
    _, rec, _ = run(api)
    assert rec[0].failed == ["iscsi_log_errors"]


def test_loki_without_kernel_lines_is_stale_not_pass():
    api = FakeApi()
    api.loki_table["fresh"] = []
    api.loki_table["iscsi"] = AssertionError("must not be asked: no data to judge")
    _, rec, _ = run(api)
    assert rec[0].failed == ["loki_stale"]


def test_loki_unreachable_is_fail():
    api = FakeApi()
    api.loki_table["fresh"] = v.ApiError("connection refused")
    _, rec, _ = run(api)
    assert rec[0].failed == ["loki_error"]


def test_fail_posts_to_every_alertmanager_with_rule_labels():
    api = FakeApi(**{"count(homelab_pvc_writable != 1)": [({}, 3)]})
    _, rec, _ = run(api)
    assert [u for u, _ in api.posts] == CFG["alertmanager_urls"]
    labels = api.posts[0][1][0]["labels"]
    # Same label set the PrometheusRule produces, so Alertmanager dedups the two paths.
    assert labels == {
        "alertname": "UpdateVerificationFailed", "severity": "critical", "namespace": "monitoring",
        "device": "Rack Switch", "boot": "1794568260", "from_version": OLD, "to_version": NEW,
        "failed_checks": "pvc_writable", "prometheus": "monitoring/kube-prometheus-stack-prometheus",
    }


def test_fail_is_reposted_each_loop_while_retained():
    api = FakeApi(**{"count(homelab_pvc_writable != 1)": [({}, 3)]})
    state, _, _ = run(api)
    run(api, state, now=NOW + 300)
    assert len(api.posts) == 4  # 2 Alertmanagers x 2 loops
    run(api, state, now=BOOT + CFG["retain"] + 60)
    assert len(api.posts) == 4  # boot aged out: no more posts, the alert expires


# --- Prometheus blind (2026-11-13) ---------------------------------------------


@pytest.mark.parametrize("fresh", [
    [],                       # empty: no samples in the lookback, what 11-13 03:25 returned
    [({}, 4000.0)],           # frozen TSDB: newest sample over an hour old
    v.ApiError("timeout"),    # unreachable
])
def test_prometheus_stale_fails_and_uses_unpoller(fresh):
    api = FakeApi(**{"time() - max(timestamp(up))": fresh})
    state, rec, _ = run(api)
    assert len(rec) == 1
    r = rec[0]
    assert r.failed == ["prometheus_stale"]
    assert r.to_version == NEW
    assert r.boot == v.boot_id(NOW - 1200)
    # Only the freshness probe went to Prometheus: no read-back, no checks on stale data.
    assert api.prom_calls == ["time() - max(timestamp(up))"]
    assert len(api.posts) == 2  # the email does not depend on Prometheus
    assert v.render_metrics(state).count("homelab_update_verifier_prometheus_fresh 0") == 1


def test_from_version_from_memory_when_prometheus_blind():
    api = FakeApi(uptime=9_000_000)
    state = v.State()
    # Before the reboot the verifier saw the old version (via Prometheus).
    old_info = api.prom
    def prom_old(expr, at=None):
        if expr.startswith("unpoller_device_info"):
            return [({"name": "Rack Switch", "version": OLD}, 1)]
        if expr.startswith("timestamp(unpoller_device_uptime_seconds"):
            return [({"name": "Rack Switch"}, BOOT - 9_000_000)]
        return old_info(expr, at)
    api.prom = prom_old
    v.step(api, state, BOOT - 600, CFG, log=lambda s: None)
    # Then the switch reboots and Prometheus goes blind.
    api2 = FakeApi(**{"time() - max(timestamp(up))": []})
    _, rec, _ = run(api2, state)
    assert (rec[0].from_version, rec[0].to_version) == (OLD, NEW)


def test_nothing_can_see_the_devices_logs_and_records_nothing():
    api = FakeApi(**{"time() - max(timestamp(up))": []})
    api.unpoller_error = v.ApiError("no route")
    _, rec, logs = run(api)
    assert rec == []
    assert any("cannot read device uptime" in line for line in logs)


# --- restart after a pass ------------------------------------------------------


def test_restart_after_pass_reads_back_and_does_not_repeat():
    api = FakeApi()
    first, rec, _ = run(api)
    assert len(rec) == 1
    # New process: empty memory. Prometheus still holds the earlier result,
    # with this loop's boot jittered by 50 s (measured: unpoller's start time
    # wanders by up to 300 s), so read-back must match by tolerance.
    r = rec[0]
    api2 = FakeApi()
    api2.history = [({**r.labels()}, float(r.boot))]
    api2.prom_calls.clear()
    real = api2.prom
    api2.prom = lambda e, at=None: [({"name": "Rack Switch"}, BOOT + 50)] if e.startswith("timestamp(") else real(e, at)
    state, rec2, _ = run(api2, now=NOW + 300)
    assert rec2 == []  # no duplicate result
    # ...and the result is exported again, so the deadman's max_over_time keeps
    # seeing this boot's value and does not page falsely.
    assert 'result="pass"' in v.render_metrics(state)
    assert api2.posts == []


def test_restart_after_fail_keeps_alerting():
    r = v.Result("Rack Switch", 1794568260, OLD, NEW, ["pvc_writable"], NOW)
    api = FakeApi()
    api.history = [(r.labels(), float(r.boot))]
    _, rec, _ = run(api, now=NOW + 300)
    assert rec == []
    assert len(api.posts) == 2
    assert api.posts[0][1][0]["labels"]["failed_checks"] == "pvc_writable"


# --- timing --------------------------------------------------------------------


def test_boot_too_young_waits():
    api = FakeApi(uptime=600)  # 10 min: sessions may still be inside the 600 s window
    state, rec, _ = run(api, now=BOOT + 600)
    assert rec == [] and state.results == {}
    assert not any(c.startswith("count(") for c in api.prom_calls)  # checks did not run


def test_boot_too_old_is_left_to_the_deadman():
    api = FakeApi(uptime=2700)
    state, rec, _ = run(api, now=BOOT + 2700)
    assert rec == [] and state.results == {}


def test_boundaries():
    _, rec, _ = run(FakeApi(uptime=900), now=BOOT + 900)
    assert len(rec) == 1  # exactly 15 min: verify
    _, rec, _ = run(FakeApi(uptime=2699), now=BOOT + 2699)
    assert len(rec) == 1  # just under 45 min: still verify


# --- helpers -------------------------------------------------------------------


def test_parse_prom_text_handles_escapes_and_bare_metrics():
    text = 'm{name="a \\"b\\"",x="1"} 2\nm 3\nmm{name="z"} 9\n'
    assert v.parse_prom_text(text, "m") == [({"name": 'a "b"', "x": "1"}, 2.0), ({}, 3.0)]


def test_boot_id_and_tolerance():
    assert v.boot_id(1794568250.8) == 1794568260
    assert v.same_boot(1794568260, 1794568260 + 600)
    assert not v.same_boot(1794568260, 1794568260 + 601)
