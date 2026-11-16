import os

import lint_workflows as lw


def test_statuses_write_outside_bench_gate():
    doc = {"jobs": {"a": {"permissions": {"statuses": "write"}}}}
    assert lw.violations_for("other.yaml", doc) == ["other.yaml: job a has statuses: write (only bench-gate.yaml may)"]


def test_workflow_level_statuses_write():
    assert lw.violations_for("other.yml", {"permissions": {"statuses": "write"}, "jobs": {}})


def test_write_all_is_denied_everywhere():
    assert lw.violations_for("bench-gate.yaml", {"permissions": "write-all"})
    assert lw.violations_for("x.yaml", {"jobs": {"j": {"permissions": "write-all"}}})


def test_bench_gate_may_write_statuses():
    assert lw.violations_for("bench-gate.yaml", {"permissions": {"statuses": "write"}}) == []


def test_statuses_read_is_fine():
    assert lw.violations_for("staging-deploy.yaml", {"jobs": {"s": {"permissions": {"statuses": "read"}}}}) == []


def test_main_on_dir(tmp_path):
    (tmp_path / "ok.yaml").write_text("on: push\njobs:\n  a:\n    permissions:\n      contents: read\n")
    assert lw.main([str(tmp_path)]) == 0
    (tmp_path / "bad.yml").write_text("on: push\njobs:\n  a:\n    permissions:\n      statuses: write\n")
    assert lw.main([str(tmp_path)]) == 1


def test_repo_workflows_pass():
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".github", "workflows")
    assert lw.main([root]) == 0
