"""Unit tests for check.py: one or more per rule in rules.yaml and the plan.

Each test builds a real git repo (base on master, change on a branch) and runs
check_range on it, the same entry point CI and the pre-push hook use.
"""

import os

import pytest

import check
from conftest import rule_names, run

DEPLOY = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  template:
    spec:
      containers:
        - name: app
          image: app:1
"""

KUSTOMIZATION = """\
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: app-stage
resources:
  - ../../base/app
"""


def violations(repo, rules, change, base=None):
    return run(repo, rules, base, change)


def assert_ok(vs):
    assert vs == [], "\n".join(map(str, vs))


def assert_rule(vs, rule):
    assert rule in rule_names(vs), f"expected a {rule} violation, got: " + "; ".join(map(str, vs))


# --------------------------------------------------------------------------
# Paths


@pytest.mark.parametrize(
    "path",
    [
        "apps/base/app/deployment.yaml",
        "apps/staging/app/kustomization.yaml",
        "docs/operations/apps/app.md",
        "docs/plans/2026-11-01-x-plan.md",
    ],
)
def test_allowed_paths(repo, rules, path):
    content = KUSTOMIZATION if path.endswith("kustomization.yaml") else "hello\n"
    assert_ok(violations(repo, rules, {path: content}))


@pytest.mark.parametrize(
    "path",
    [
        "infra/x",
        "infra/controllers/thing/helmrelease.yaml",
        "clusters/melodic-muse/apps-staging.yaml",
        ".github/workflows/x.yaml",
        ".github/CODEOWNERS",
        "scripts/github-rulesets.sh",
        "scripts/bench-gate/rules.yaml",
        "scripts/bench-cloud/merge-audit.sh",
        "images/bench-cloud/managed-settings.json",
        "hosts/hestia/x.md",
        "AGENTS.md",
        "renovate.json",
        "Makefile",
    ],
)
def test_paths_outside_allow_list_are_denied(repo, rules, path):
    assert_rule(violations(repo, rules, {path: "hello\n"}), "path")


@pytest.mark.parametrize(
    "path",
    [
        "apps/production/agent-desk/config.yaml",
        "apps/staging/agent-desk/kustomization.yaml",
        "apps/base/agent-desk/deployment.yaml",
        "apps/production/bench-cloud/job.yaml",
        "apps/base/bench-cloud/README.md",
        "apps/production/app/secret-db.yaml",
        "apps/production/truenas-iscsi-monitor/secret.yaml",
        "apps/staging/app/secrets.yml",
        "apps/base/app/creds.sops.yaml",
        "apps/base/app/networkpolicy.yaml",
        "apps/base/app/networkpolicy-egress.yaml",
        "apps/base/app/NetworkPolicy.yaml",
    ],
)
def test_denied_paths_win_over_allow(repo, rules, path):
    assert_rule(violations(repo, rules, {path: "data: 1\n"}), "path")


def test_rename_checks_the_old_name(repo, rules):
    vs = violations(repo, rules, {"infra/x.md": None, "apps/base/app/x.md": "same\n"}, base={"infra/x.md": "same\n"})
    assert any(v.path == "infra/x.md" and v.rule == "path" for v in vs), vs


def test_rename_checks_the_new_name(repo, rules):
    vs = violations(repo, rules, {"docs/x.md": None, "infra/x.md": "same\n"}, base={"docs/x.md": "same\n"})
    assert any(v.path == "infra/x.md" for v in vs), vs


def test_rename_inside_allowed_paths_passes(repo, rules):
    assert_ok(violations(repo, rules, {"docs/a.md": None, "docs/b.md": "same\n"}, base={"docs/a.md": "same\n"}))


def test_deleting_a_denied_path_is_denied(repo, rules):
    assert_rule(violations(repo, rules, {"infra/x": None}, base={"infra/x": "x\n"}), "path")


# --------------------------------------------------------------------------
# SOPS


SOPS_FILE = "data:\n  password: ENC[AES256_GCM,data:xx]\nsops:\n  version: 3.9.0\n"


def test_sops_key_in_new_file(repo, rules):
    assert_rule(violations(repo, rules, {"apps/base/app/creds.yaml": SOPS_FILE}), "sops")


def test_sops_key_in_old_file(repo, rules):
    vs = violations(repo, rules, {"apps/base/app/creds.yaml": "data: {}\n"}, base={"apps/base/app/creds.yaml": SOPS_FILE})
    assert_rule(vs, "sops")


def test_sops_in_docs_is_denied_too(repo, rules):
    assert_rule(violations(repo, rules, {"docs/x.yaml": SOPS_FILE}), "sops")


def test_sops_dotenv(repo, rules):
    assert_rule(violations(repo, rules, {"apps/base/app/app.env": "A=ENC[x]\nsops_version=3.9.0\n"}), "sops")


def test_nested_sops_word_is_not_top_level(repo, rules):
    assert_ok(violations(repo, rules, {"docs/x.md": "Use `sops:` blocks:\n  sops: is nested here\n"}))


# --------------------------------------------------------------------------
# Network policies


CNP = """\
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: x
"""


def test_inline_network_policy_in_another_file(repo, rules):
    vs = violations(repo, rules, {"apps/staging/immich/cronjob-bootstrap.yaml": DEPLOY + "---\n" + CNP})
    assert_rule(vs, "kind")


def test_editing_an_unrelated_line_of_a_file_with_a_policy(repo, rules):
    path = "apps/staging/immich/cronjob-bootstrap.yaml"
    vs = violations(repo, rules, {path: DEPLOY.replace("app:1", "app:2") + "---\n" + CNP}, base={path: DEPLOY + "---\n" + CNP})
    assert_rule(vs, "kind")


def test_k8s_network_policy_kind(repo, rules):
    np = "apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata:\n  name: x\n"
    assert_rule(violations(repo, rules, {"apps/base/app/policy.yaml": np}), "kind")


def test_kustomize_patch_targeting_network_policy(repo, rules):
    k = KUSTOMIZATION + "patches:\n  - target:\n      kind: CiliumNetworkPolicy\n      name: x\n    patch: |-\n      - op: remove\n        path: /spec/egress/0\n"
    assert_rule(violations(repo, rules, {"apps/staging/app/kustomization.yaml": k}), "kind")


def test_dropping_networkpolicy_from_resources(repo, rules):
    path = "apps/base/app/kustomization.yaml"
    base = "resources:\n  - deployment.yaml\n  - networkpolicy.yaml\n"
    vs = violations(repo, rules, {path: "resources:\n  - deployment.yaml\n"}, base={path: base})
    assert_rule(vs, "networkpolicy")


def test_adding_networkpolicy_to_resources(repo, rules):
    path = "apps/base/app/kustomization.yaml"
    vs = violations(repo, rules, {path: "resources:\n  - deployment.yaml\n  - networkpolicy.yaml\n"}, base={path: "resources:\n  - deployment.yaml\n"})
    assert_rule(vs, "networkpolicy")


# --------------------------------------------------------------------------
# Kinds: as a document, a patch target and a List item


def all_denied_kinds(rules):
    return rules["clusterScopedKinds"] + rules["deniedKinds"] + ["ClusterRole", "ClusterRoleBinding", "ClusterIssuer", "ClusterSecretStore"]


@pytest.fixture(params=["document", "patch-target", "patch-target-flow", "list-item", "json-style"])
def kind_form(request):
    return request.param


def kind_file(form, kind):
    if form == "document":
        return "apps/base/app/x.yaml", f"apiVersion: v1\nkind: {kind}\nmetadata:\n  name: x\n"
    if form == "patch-target":
        return "apps/staging/app/kustomization.yaml", KUSTOMIZATION + f"patches:\n  - target:\n      kind: {kind}\n      name: x\n    patch: |-\n      - op: add\n        path: /metadata/labels/a\n        value: b\n"
    if form == "patch-target-flow":
        return "apps/staging/app/kustomization.yaml", KUSTOMIZATION + f"patches:\n  - target: {{kind: {kind}, name: x}}\n    path: patch.yaml\n"
    if form == "list-item":
        return "apps/base/app/list.yaml", f"apiVersion: v1\nkind: List\nitems:\n  - apiVersion: v1\n    kind: {kind}\n    metadata:\n      name: x\n"
    return "apps/base/app/x.json", f'{{"apiVersion": "v1", "kind": "{kind}", "metadata": {{"name": "x"}}}}\n'


def test_every_denied_kind_in_every_form(repo, rules, kind_form):
    # One repo per form; each kind is checked through check_changes directly
    # on the same file layout to keep the suite fast.
    for kind in all_denied_kinds(rules):
        path, text = kind_file(kind_form, kind)
        c = check.Change(path=path, new_text=text, added=list(enumerate(text.splitlines(), 1)))
        vs = check.check_changes(rules, [c])
        assert "kind" in rule_names(vs), f"{kind} as {kind_form}: {vs}"


@pytest.mark.parametrize("kind", ["Namespace", "ClusterRoleBinding", "RoleBinding", "ValidatingWebhookConfiguration", "PersistentVolume"])
def test_denied_kinds_end_to_end(repo, rules, kind):
    path, text = kind_file("document", kind)
    assert_rule(violations(repo, rules, {path: text}), "kind")


def test_patch_target_cluster_role_end_to_end(repo, rules):
    path, text = kind_file("patch-target", "ClusterRole")
    assert_rule(violations(repo, rules, {path: text}), "kind")


@pytest.mark.parametrize("kind", ["Deployment", "Service", "ConfigMap", "PersistentVolumeClaim", "HTTPRoute", "Kustomization", "CronJob", "StatefulSet"])
def test_namespaced_kinds_pass(repo, rules, kind):
    path, text = kind_file("document", kind)
    assert_ok(violations(repo, rules, {path: text.replace("apiVersion: v1", "apiVersion: apps/v1")}))


# --------------------------------------------------------------------------
# API groups: as apiVersion and as a patch target group


GROUP_SAMPLES = [
    "rbac.authorization.k8s.io",
    "admissionregistration.k8s.io",
    "apiextensions.k8s.io",
    "apiregistration.k8s.io",
    "storage.k8s.io",
    "scheduling.k8s.io",
    "node.k8s.io",
    "certificates.k8s.io",
    "cilium.io",
    "isovalent.cilium.io",
    "helm.toolkit.fluxcd.io",
    "kustomize.toolkit.fluxcd.io",
    "source.toolkit.fluxcd.io",
    "notification.toolkit.fluxcd.io",
]


@pytest.mark.parametrize("group", GROUP_SAMPLES)
def test_denied_group_as_api_version(repo, rules, group):
    text = f"apiVersion: {group}/v1\nkind: Thing\nmetadata:\n  name: x\n"
    assert_rule(violations(repo, rules, {"apps/base/app/x.yaml": text}), "api-group")


@pytest.mark.parametrize("group", GROUP_SAMPLES)
def test_denied_group_as_patch_target(rules, group):
    text = KUSTOMIZATION + f"patches:\n  - target:\n      group: {group}\n      kind: Thing\n    path: p.yaml\n"
    c = check.Change(path="apps/staging/app/kustomization.yaml", new_text=text, added=list(enumerate(text.splitlines(), 1)))
    assert "api-group" in rule_names(check.check_changes(rules, [c]))


def test_rules_yaml_lists_every_sampled_group(rules):
    # Guard against the samples drifting from the rules file.
    for g in GROUP_SAMPLES:
        assert check.group_denied(rules, g), g


def test_flux_helmrelease_and_gitrepository(repo, rules):
    hr = "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\nmetadata:\n  name: x\n"
    gr = "apiVersion: source.toolkit.fluxcd.io/v1\nkind: GitRepository\nmetadata:\n  name: x\n"
    assert_rule(violations(repo, rules, {"apps/base/app/hr.yaml": hr, "apps/base/app/gr.yaml": gr}), "api-group")


@pytest.mark.parametrize("group", ["apps", "batch", "gateway.networking.k8s.io", "postgresql.cnpg.io", "monitoring.coreos.com"])
def test_ordinary_groups_pass(repo, rules, group):
    text = f"apiVersion: {group}/v1\nkind: Thing\nmetadata:\n  name: x\n"
    assert_ok(violations(repo, rules, {"apps/base/app/x.yaml": text}))


# --------------------------------------------------------------------------
# System namespaces


@pytest.mark.parametrize("ns", ["flux-system", "kube-system", "kube-public", "kube-node-lease"])
def test_system_namespace_in_metadata(repo, rules, ns):
    text = DEPLOY.replace("  name: app\n", f"  name: app\n  namespace: {ns}\n", 1)
    assert_rule(violations(repo, rules, {"apps/base/app/deployment.yaml": text}), "namespace")


def test_kustomization_namespace_transformer(repo, rules):
    assert_rule(violations(repo, rules, {"apps/staging/app/kustomization.yaml": KUSTOMIZATION.replace("app-stage", "kube-system")}), "namespace")


def test_json_patch_setting_namespace(repo, rules):
    k = KUSTOMIZATION + "patches:\n  - target:\n      kind: Deployment\n    patch: |-\n      - op: replace\n        path: /metadata/namespace\n        value: flux-system\n"
    assert_rule(violations(repo, rules, {"apps/staging/app/kustomization.yaml": k}), "namespace")


def test_existing_system_namespace_mention_still_denies_an_edit(repo, rules):
    path = "apps/base/app/x.yaml"
    base = "# talks to kube-system DNS\nkey: 1\n"
    assert_rule(violations(repo, rules, {path: base.replace("1", "2")}, base={path: base}), "namespace")


def test_system_namespace_in_docs_is_fine(repo, rules):
    assert_ok(violations(repo, rules, {"docs/operations/x.md": "kubectl -n kube-system get pods\nflux-system\n"}))


# --------------------------------------------------------------------------
# kustomization.yaml: remote content, helmCharts, references out of the paths


@pytest.mark.parametrize("key", ["resources", "components", "bases"])
@pytest.mark.parametrize(
    "ref",
    [
        "https://raw.githubusercontent.com/x/y/main/all.yaml",
        "github.com/x/y//deploy?ref=v1",
        "git@github.com:x/y.git",
        "ssh://git@host/x.git",
        "../../base/app?ref=main",
    ],
)
def test_remote_reference_added(repo, rules, key, ref):
    k = f"apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n{key}:\n  - ../../base/app\n  - {ref}\n"
    assert_rule(violations(repo, rules, {"apps/staging/app/kustomization.yaml": k}), "kustomize")


def test_existing_remote_reference_is_not_re_flagged(repo, rules):
    path = "apps/staging/app/kustomization.yaml"
    base = KUSTOMIZATION + "  - https://example.com/x.yaml\n"
    new = base + "images:\n  - name: app\n    newTag: '2'\n"
    assert_ok(violations(repo, rules, {path: new}, base={path: base}))


def test_urls_in_patch_values_are_fine(repo, rules):
    # apps/staging/openwebui/kustomization.yaml has URLs as env values.
    k = KUSTOMIZATION + "patches:\n  - target:\n      kind: Deployment\n    patch: |-\n      - op: replace\n        path: /spec/template/spec/containers/0/env/0/value\n        value: http://llmux.llmux.svc.cluster.local:8080/v1\n"
    assert_ok(violations(repo, rules, {"apps/staging/app/kustomization.yaml": k}))


@pytest.mark.parametrize("key", ["helmCharts", "helmChartInflationGenerator"])
def test_helm_charts_key(repo, rules, key):
    k = KUSTOMIZATION + f"{key}:\n  - name: x\n    repo: https://charts.example.com\n"
    assert_rule(violations(repo, rules, {"apps/staging/app/kustomization.yaml": k}), "kustomize")


@pytest.mark.parametrize(
    "ref",
    ["../../../infra/controllers/thing", "../../../clusters/melodic-muse", "../../base/bench-cloud", "../../production/agent-desk", "../../../scripts"],
)
def test_reference_resolving_outside_the_paths(repo, rules, ref):
    k = KUSTOMIZATION + f"  - {ref}\n"
    assert_rule(violations(repo, rules, {"apps/staging/app/kustomization.yaml": k}), "kustomize")


def test_patch_and_generator_files_resolving_outside(repo, rules):
    k = KUSTOMIZATION + "patches:\n  - path: ../../../infra/p.yaml\nconfigMapGenerator:\n  - name: c\n    files:\n      - key=../../../clusters/x\n"
    vs = violations(repo, rules, {"apps/staging/app/kustomization.yaml": k})
    assert len([v for v in vs if v.rule == "kustomize"]) == 2, vs


def test_plain_kustomization_edit_passes(repo, rules):
    path = "apps/staging/app/kustomization.yaml"
    assert_ok(violations(repo, rules, {path: KUSTOMIZATION + "images:\n  - name: app\n    newTag: '2'\n"}, base={path: KUSTOMIZATION}))


def test_unparsable_kustomization_fails_closed(repo, rules):
    assert_rule(violations(repo, rules, {"apps/staging/app/kustomization.yaml": "resources: [\n  - a\n"}), "kustomize")


def test_non_mapping_kustomization_fails_closed(repo, rules):
    assert_rule(violations(repo, rules, {"apps/staging/app/kustomization.yaml": "- a\n- b\n"}), "kustomize")


# --------------------------------------------------------------------------
# Privileged-pod lines

POD = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  template:
    spec:
      containers:
        - name: app
          image: app:1
          securityContext:
            runAsNonRoot: true
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: data
"""

POD_PRIV = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  template:
    spec:
      hostNetwork: false
      containers:
        - name: app
          image: app:1
          securityContext:
            capabilities:
              add:
                - NET_ADMIN
      volumes:
        - name: dev
          hostPath:
            path: /dev/net
"""

DEP = "apps/staging/app/deployment-patch.yaml"


@pytest.mark.parametrize(
    "old,new",
    [
        ("            runAsNonRoot: true\n", "            privileged: true\n"),
        ("            runAsNonRoot: true\n", '            "privileged": true\n'),
        ("            runAsNonRoot: true\n", "            privileged: True\n"),
        ("    spec:\n      containers:", "    spec:\n      hostNetwork: true\n      containers:"),
        ("    spec:\n      containers:", "    spec:\n      hostPID: true\n      containers:"),
        ("    spec:\n      containers:", "    spec:\n      hostIPC: true\n      containers:"),
        ("          persistentVolumeClaim:\n            claimName: data\n", "          hostPath:\n            path: /\n"),
        ("            runAsNonRoot: true\n", "            capabilities:\n              add:\n                - SYS_ADMIN\n"),
        ("            runAsNonRoot: true\n", "            capabilities:\n              add: [SYS_ADMIN]\n"),
        ("            runAsNonRoot: true\n", "            capabilities:\n              add:\n              - SYS_ADMIN\n"),
    ],
    ids=["privileged", "privileged-json", "privileged-True", "hostNetwork", "hostPID", "hostIPC", "hostPath", "cap-add-block", "cap-add-inline", "cap-add-same-indent"],
)
def test_privileged_block_forms(repo, rules, old, new):
    assert old in POD
    vs = violations(repo, rules, {DEP: POD.replace(old, new, 1)}, base={DEP: POD})
    assert_rule(vs, "privileged")


@pytest.mark.parametrize(
    "line",
    [
        "          securityContext: {privileged: true}",
        "          capabilities: {add: [NET_ADMIN]}",
        '          "capabilities": {"add": ["NET_ADMIN"]}',
        "      - {name: dev, hostPath: {path: /}}",
    ],
    ids=["flow-privileged", "flow-cap-add", "json-cap-add", "flow-hostPath"],
)
def test_privileged_flow_style(repo, rules, line):
    new = POD.replace("          image: app:1\n", "          image: app:1\n" + line + "\n", 1)
    assert_rule(violations(repo, rules, {DEP: new}, base={DEP: POD}), "privileged")


@pytest.mark.parametrize(
    "path",
    [
        "/spec/template/spec/hostNetwork",
        "/spec/template/spec/hostPID",
        "/spec/template/spec/hostIPC",
        "/spec/template/spec/containers/0/securityContext/privileged",
        "/spec/template/spec/volumes/-/hostPath",
        "/spec/template/spec/containers/0/securityContext/capabilities",
        "/spec/template/spec/containers/0/securityContext/capabilities/add/-",
    ],
)
def test_privileged_json_patch(repo, rules, path):
    k = KUSTOMIZATION + f"patches:\n  - target:\n      kind: Deployment\n    patch: |-\n      - op: add\n        path: {path}\n        value: true\n"
    assert_rule(violations(repo, rules, {"apps/staging/app/kustomization.yaml": k}), "privileged")


def test_json_patch_value_with_hostpath_object(repo, rules):
    k = KUSTOMIZATION + "patches:\n  - target:\n      kind: Deployment\n    patch: |-\n      - op: add\n        path: /spec/template/spec/volumes/-\n        value: {name: root, hostPath: {path: /}}\n"
    assert_rule(violations(repo, rules, {"apps/staging/app/kustomization.yaml": k}), "privileged")


def test_nested_path_under_existing_hostpath(repo, rules):
    new = POD_PRIV.replace("            path: /dev/net\n", "            path: /dev/net\n            type: Directory\n")
    vs = violations(repo, rules, {DEP: new}, base={DEP: POD_PRIV})
    assert_rule(vs, "privileged")


def test_changed_path_under_existing_hostpath(repo, rules):
    vs = violations(repo, rules, {DEP: POD_PRIV.replace("path: /dev/net", "path: /")}, base={DEP: POD_PRIV})
    assert_rule(vs, "privileged")


def test_new_capability_under_existing_add(repo, rules):
    new = POD_PRIV.replace("                - NET_ADMIN\n", "                - NET_ADMIN\n                - SYS_ADMIN\n")
    vs = violations(repo, rules, {DEP: new}, base={DEP: POD_PRIV})
    assert_rule(vs, "privileged")


def test_unrelated_line_in_a_privileged_file_passes(repo, rules):
    vs = violations(repo, rules, {DEP: POD_PRIV.replace("app:1", "app:2")}, base={DEP: POD_PRIV})
    assert_ok(vs)


def test_removing_a_hostpath_volume_passes(repo, rules):
    new = POD_PRIV.replace("        - name: dev\n          hostPath:\n            path: /dev/net\n", "        - name: tmp\n          emptyDir: {}\n")
    assert_ok(violations(repo, rules, {DEP: new}, base={DEP: POD_PRIV}))


def test_removing_a_capability_passes(repo, rules):
    new = POD_PRIV.replace("            capabilities:\n              add:\n                - NET_ADMIN\n", "            runAsNonRoot: true\n")
    assert_ok(violations(repo, rules, {DEP: new}, base={DEP: POD_PRIV}))


@pytest.mark.parametrize("drop", ["            capabilities:\n              drop: [ALL]\n", "            capabilities:\n              drop:\n                - ALL\n"])
def test_capabilities_drop_passes(repo, rules, drop):
    new = POD.replace("            runAsNonRoot: true\n", "            runAsNonRoot: true\n" + drop)
    assert_ok(violations(repo, rules, {DEP: new}, base={DEP: POD}))


@pytest.mark.parametrize("line", ["            privileged: false", "      hostNetwork: false", "            allowPrivilegeEscalation: false"])
def test_false_values_pass(repo, rules, line):
    new = POD.replace("            runAsNonRoot: true\n", "            runAsNonRoot: true\n" + line + "\n") if line.startswith("            ") else POD.replace("    spec:\n      containers:", "    spec:\n" + line + "\n      containers:")
    assert_ok(violations(repo, rules, {DEP: new}, base={DEP: POD}))


def test_json_patch_op_add_is_not_a_capability(repo, rules):
    k = KUSTOMIZATION + "patches:\n  - target:\n      kind: Deployment\n    patch: |-\n      - op: add\n        path: /spec/template/metadata/labels/x\n        value: y\n"
    assert_ok(violations(repo, rules, {"apps/staging/app/kustomization.yaml": k}))


def test_privileged_text_in_docs_is_fine(repo, rules):
    assert_ok(violations(repo, rules, {"docs/x.md": "set `privileged: true` and `hostPath:` here\n"}))


# --------------------------------------------------------------------------
# Fail closed


def test_binary_file(repo, rules):
    assert_rule(violations(repo, rules, {"apps/base/app/blob.bin": b"\x00\x01\x02binary"}), "unreadable")


def test_symlink(repo, rules):
    repo.base({"README.md": "x\n"})
    repo.git("checkout", "-q", "-B", "pr", "master")
    os.makedirs(os.path.join(repo.path, "apps/base/app"))
    os.symlink("../../../infra/controllers/x.yaml", os.path.join(repo.path, "apps/base/app/x.yaml"))
    repo.commit("symlink")
    assert_rule(repo.check(rules), "unreadable")


def test_non_utf8_text(repo, rules):
    assert_rule(violations(repo, rules, {"apps/base/app/x.txt": b"caf\xe9\n"}), "unreadable")


def test_too_many_files(rules):
    changes = [check.Change(path=f"docs/{i}.md", new_text="x\n") for i in range(rules["maxFiles"] + 1)]
    assert rule_names(check.check_changes(rules, changes)) == ["size"]


def test_bad_ref_exits_2(repo, rules, capsys):
    repo.base({"README.md": "x\n"})
    assert check.main(["--repo", repo.path, "--base", "master", "--head", "nope"]) == 2


def test_cli_exit_codes(repo, rules, capsys):
    repo.base({"README.md": "x\n"})
    repo.change({"docs/a.md": "x\n"})
    assert check.main(["--repo", repo.path, "--base", "master", "--head", "pr"]) == 0
    repo.change({"infra/a": "x\n"})
    assert check.main(["--repo", repo.path, "--base", "master", "--head", "pr"]) == 1
    assert "infra/a" in capsys.readouterr().out


def test_diff_is_against_the_merge_base(repo, rules):
    # master moving on (here: into infra/) must not count against the PR.
    repo.base({"README.md": "x\n"})
    repo.change({"docs/a.md": "x\n"})
    repo.git("checkout", "-q", "master")
    repo.write({"infra/later.yaml": "x\n"})
    repo.commit("master moves")
    assert_ok(repo.check(rules))


# --------------------------------------------------------------------------
# Helpers


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("apps/**", "apps/a/b/c.yaml", True),
        ("apps/**", "appsx/a", False),
        ("apps/*/agent-desk/**", "apps/production/agent-desk/config.yaml", True),
        ("apps/*/agent-desk/**", "apps/production/x/agent-desk/config.yaml", False),
        ("**/secret*.yaml", "secret.yaml", True),
        ("**/secret*.yaml", "apps/a/secret-x.yaml", True),
        ("**/secret*.yaml", "apps/a/mysecret.yaml", False),
    ],
)
def test_glob(pattern, path, expected):
    assert check.glob_match(pattern, path) is expected


def test_yaml_ancestry_sequence_at_key_indent():
    anc = check.yaml_ancestry("capabilities:\n  add:\n  - X\n  drop:\n  - Y\n")
    assert anc[3][1] == ["capabilities", "add"]
    assert anc[5][1] == ["capabilities", "drop"]


def test_rules_file_loads(rules):
    assert rules["allow"] == ["apps/**", "docs/**"]


def test_glob_dir_itself_matches_trailing_doublestar():
    assert check.glob_match("apps/*/bench-cloud/**", "apps/base/bench-cloud")
    assert not check.glob_match("apps/*/bench-cloud/**", "apps/base/bench-cloudy")
