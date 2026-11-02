#!/usr/bin/env python3
"""bench-gate path checker (HOMELAB-85).

Decides whether a change may come from bench-cloud: every changed path must be
inside the allowed paths, and no changed manifest may declare a cluster-scoped
kind, a privilege-granting API group, a system namespace, remote kustomize
content, or (on an added line) a privileged-pod field. The rules live in
rules.yaml next to this file.

    check.py --base origin/master --head <sha>          # human report
    check.py --base origin/master --head <sha> --json   # machine report

Exit 0: allowed. Exit 1: at least one violation. Exit 2: the check itself
could not run (bad ref, unreadable rules). Anything the checker cannot read
(binary files, symlinks, unparsable kustomizations, too many files) is a
violation, never a pass.

The checker only reads git objects (`git diff`, `git show`); it never checks
out or executes anything from the change. CI runs master's copy against a PR
fetched as data.

Plan: docs/plans/2026-11-01-bench-homelab-paths-plan.md
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass, field

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(HERE, "rules.yaml")

KUSTOMIZATION_NAMES = {"kustomization.yaml", "kustomization.yml", "Kustomization"}


# --------------------------------------------------------------------------
# Data


@dataclass
class Change:
    """One changed file. Renames are reported as a delete plus an add."""

    path: str
    old_text: str | None = None  # None: file did not exist before
    new_text: str | None = None  # None: file does not exist after
    added: list[tuple[int, str]] = field(default_factory=list)  # (new lineno, text)
    removed: list[str] = field(default_factory=list)
    unreadable: str | None = None  # reason the file can't be checked


@dataclass(frozen=True)
class Violation:
    path: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: [{self.rule}] {self.detail}"


# --------------------------------------------------------------------------
# Rules and globs


def load_rules(path: str = DEFAULT_RULES) -> dict:
    with open(path, encoding="utf-8") as f:
        rules = yaml.safe_load(f)
    if not isinstance(rules, dict) or not rules.get("allow"):
        raise ValueError(f"{path}: no allow list")
    return rules


def glob_to_regex(pattern: str) -> str:
    """`**` = any number of segments, `*`/`?` = within one segment."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern[i:] == "/**":
            out.append("(?:/.*)?")  # the directory itself, or anything in it
            i += 3
        elif pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "^" + "".join(out) + "$"


def glob_match(pattern: str, path: str, ignore_case: bool = False) -> bool:
    flags = re.IGNORECASE if ignore_case else 0
    return re.match(glob_to_regex(pattern), path, flags) is not None


def any_glob(patterns, path: str, ignore_case: bool = False) -> str | None:
    for p in patterns or []:
        if glob_match(p, path, ignore_case):
            return p
    return None


# --------------------------------------------------------------------------
# Per-rule checks


def path_violations(rules: dict, path: str) -> list[Violation]:
    out = []
    if not any_glob(rules["allow"], path):
        out.append(Violation(path, "path", "outside the allowed paths (" + ", ".join(rules["allow"]) + ")"))
    hit = any_glob(rules.get("deny"), path, ignore_case=True)
    if hit:
        out.append(Violation(path, "path", f"matches denied path {hit}"))
    return out


SOPS_RE = re.compile(r"""^(?:["']?sops["']?\s*:|sops_[a-z_]+=)""", re.MULTILINE)


def sops_violations(path: str, texts) -> list[Violation]:
    for label, text in texts:
        if text and SOPS_RE.search(text):
            return [Violation(path, "sops", f"file {label} the change has a top-level sops key")]
    return []


KIND_RE = re.compile(r"""(?:^|[\s{,\[])["']?kind["']?\s*:\s*["']?([A-Za-z][A-Za-z0-9]*)""")
APIVERSION_RE = re.compile(r"""["']?apiVersion["']?\s*:\s*["']?([A-Za-z0-9.\-]+)/""")
GROUP_RE = re.compile(r"""(?:^|[\s{,\[])["']?group["']?\s*:\s*["']?([A-Za-z0-9.\-]+)""")


def kind_denied(rules: dict, kind: str) -> str | None:
    if kind in (rules.get("clusterScopedKinds") or []):
        return "cluster-scoped kind"
    if kind in (rules.get("deniedKinds") or []):
        return "denied kind"
    for p in rules.get("deniedKindPrefixes") or []:
        if kind.startswith(p):
            return f"kind starting with {p}"
    for s in rules.get("deniedKindSuffixes") or []:
        if kind.endswith(s):
            return f"kind ending with {s}"
    return None


def group_denied(rules: dict, group: str) -> bool:
    g = group.lower()
    if g in [x.lower() for x in rules.get("deniedApiGroups") or []]:
        return True
    return any(g.endswith(s.lower()) for s in rules.get("deniedApiGroupSuffixes") or [])


def manifest_text_violations(rules: dict, path: str, texts) -> list[Violation]:
    """Kind, API group and system-namespace rules, on the text before and after."""
    out: list[Violation] = []
    seen = set()

    def add(rule, detail):
        if (rule, detail) not in seen:
            seen.add((rule, detail))
            out.append(Violation(path, rule, detail))

    for label, text in texts:
        if not text:
            continue
        for line in text.splitlines():
            for kind in KIND_RE.findall(line):
                why = kind_denied(rules, kind)
                if why:
                    add("kind", f"{why} {kind} (file {label} the change)")
            for g in APIVERSION_RE.findall(line) + GROUP_RE.findall(line):
                if group_denied(rules, g):
                    add("api-group", f"API group {g} (file {label} the change)")
        for ns in rules.get("systemNamespaces") or []:
            if ns in text:
                add("namespace", f"mentions {ns} (file {label} the change)")
    return out


def networkpolicy_line_violations(path: str, change: Change) -> list[Violation]:
    for _, line in change.added:
        if "networkpolicy" in line.lower():
            return [Violation(path, "networkpolicy", f"added line mentions a network policy: {line.strip()[:80]}")]
    for line in change.removed:
        if "networkpolicy" in line.lower():
            return [Violation(path, "networkpolicy", f"removed line mentions a network policy: {line.strip()[:80]}")]
    return []


# --- kustomization.yaml ---------------------------------------------------


def kustomize_refs(doc: dict, rules: dict) -> list[str]:
    """Every path or URL a kustomization pulls content from."""
    refs: list[str] = []
    for key in rules.get("kustomizeResourceKeys") or []:
        for item in doc.get(key) or []:
            if isinstance(item, str):
                refs.append(item)
    for item in doc.get("patchesStrategicMerge") or []:
        if isinstance(item, str) and "\n" not in item:
            refs.append(item)
    for key in ("patches", "patchesJson6902"):
        for item in doc.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                refs.append(item["path"])
    for key in ("configMapGenerator", "secretGenerator"):
        for gen in doc.get(key) or []:
            if not isinstance(gen, dict):
                continue
            for f in (gen.get("files") or []) + (gen.get("envs") or []):
                if isinstance(f, str):
                    refs.append(f.split("=", 1)[-1])
            if isinstance(gen.get("env"), str):
                refs.append(gen["env"])
    return refs


def parse_kustomization(text: str | None):
    if not text:
        return {}
    doc = yaml.safe_load(text)
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise ValueError("not a mapping")
    return doc


def kustomization_violations(rules: dict, path: str, change: Change) -> list[Violation]:
    out: list[Violation] = []
    docs = {}
    for label, text in (("before", change.old_text), ("after", change.new_text)):
        try:
            docs[label] = parse_kustomization(text)
        except Exception as e:  # noqa: BLE001 - any parse failure fails closed
            out.append(Violation(path, "kustomize", f"cannot parse the file {label} the change: {e}"))
    if out:
        return out
    for label, doc in docs.items():
        for key in rules.get("deniedKustomizeKeys") or []:
            if key in doc:
                out.append(Violation(path, "kustomize", f"{key} key (file {label} the change)"))
    old_refs = set(kustomize_refs(docs["before"], rules))
    base_dir = posixpath.dirname(path)
    for ref in kustomize_refs(docs["after"], rules):
        if ref in old_refs:
            continue
        if any(m in ref for m in rules.get("remoteMarkers") or []):
            out.append(Violation(path, "kustomize", f"remote reference {ref}"))
            continue
        target = posixpath.normpath(posixpath.join(base_dir, ref))
        bad = [v.detail for v in path_violations(rules, target)]
        if bad:
            out.append(Violation(path, "kustomize", f"new reference {ref} resolves to {target}, which is {bad[0]}"))
    return out


# --- privileged-pod lines -------------------------------------------------

PRIV_TRUE_RE = re.compile(r"""["']?\b(privileged|hostNetwork|hostPID|hostIPC)\b["']?\s*:\s*["']?true\b""", re.IGNORECASE)
HOSTPATH_KEY_RE = re.compile(r"""["']?\bhostPath\b["']?\s*:""")
JSON_PATCH_RE = re.compile(r"""/(privileged|hostNetwork|hostPID|hostIPC|hostPath|capabilities)(?![A-Za-z0-9])""")
CAP_FLOW_ADD_RE = re.compile(r"""["']?\bcapabilities\b["']?\s*:\s*\{.*["']?\badd\b["']?\s*:""")
KEY_RE = re.compile(r"""^((?:-\s+)*)["']?([A-Za-z0-9_$.\-/]+)["']?\s*:(?:\s|$)""")


def yaml_ancestry(text: str) -> dict[int, tuple[str | None, list[str]]]:
    """For each 1-based line: (its own key, the keys it is nested under).

    Indentation only, no YAML parse, so it works on partial or broken files
    and on YAML inside block scalars. A block sequence item at the same
    indentation as a key (`add:` then `- X`) counts as that key's child.
    """
    result = {}
    stack: list[tuple[int, str]] = []  # (key column, key)
    for n, raw in enumerate(text.splitlines(), start=1):
        content = raw.strip()
        if not content or content.startswith("#"):
            result[n] = (None, [k for _, k in stack])
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        is_item = content == "-" or content.startswith("- ")
        while stack and (stack[-1][0] > indent or (stack[-1][0] == indent and not is_item)):
            stack.pop()
        m = KEY_RE.match(content)
        key = m.group(2) if m else None
        result[n] = (key, [k for _, k in stack])
        if key is not None:
            stack.append((indent + len(m.group(1)), key))
    return result


def under_cap_add(ancestors: list[str]) -> bool:
    return any(a == "add" and i > 0 and ancestors[i - 1] == "capabilities" for i, a in enumerate(ancestors))


def privileged_violations(path: str, change: Change) -> list[Violation]:
    if not change.added or change.new_text is None:
        return []
    anc = yaml_ancestry(change.new_text)
    out = []
    for n, line in change.added:
        key, parents = anc.get(n, (None, []))
        why = None
        if PRIV_TRUE_RE.search(line):
            why = "sets " + PRIV_TRUE_RE.search(line).group(1) + ": true"
        elif HOSTPATH_KEY_RE.search(line):
            why = "adds a hostPath"
        elif JSON_PATCH_RE.search(line):
            why = "names /" + JSON_PATCH_RE.search(line).group(1) + " (patch path)"
        elif CAP_FLOW_ADD_RE.search(line):
            why = "adds capabilities (flow style)"
        elif key == "add" and parents and parents[-1] == "capabilities":
            why = "adds a capabilities add: key"
        elif "hostPath" in parents:
            why = "nested under a hostPath"
        elif under_cap_add(parents):
            why = "nested under capabilities.add"
        if why:
            out.append(Violation(path, "privileged", f"line {n} {why}: {line.strip()[:80]}"))
    return out


# --------------------------------------------------------------------------
# Whole change


def check_changes(rules: dict, changes: list[Change]) -> list[Violation]:
    out: list[Violation] = []
    max_files = rules.get("maxFiles", 3000)
    if len(changes) > max_files:
        return [Violation("*", "size", f"{len(changes)} changed files, more than {max_files}")]
    for c in changes:
        out += path_violations(rules, c.path)
        if c.unreadable:
            out.append(Violation(c.path, "unreadable", c.unreadable))
            continue
        texts = (("before", c.old_text), ("after", c.new_text))
        out += sops_violations(c.path, texts)
        if not any_glob(rules.get("manifestPaths"), c.path):
            continue
        out += manifest_text_violations(rules, c.path, texts)
        out += networkpolicy_line_violations(c.path, c)
        if posixpath.basename(c.path) in KUSTOMIZATION_NAMES:
            out += kustomization_violations(rules, c.path, c)
        out += privileged_violations(c.path, c)
    return out


# --------------------------------------------------------------------------
# git


def git(repo: str, *args: str) -> bytes:
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1")
    return subprocess.run(
        ["git", "-C", repo, "-c", "core.quotepath=off", *args],
        check=True,
        capture_output=True,
        env=env,
    ).stdout


def parse_hunks(patch: str) -> tuple[list[tuple[int, str]], list[str]]:
    added, removed = [], []
    new_line = 0
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if not m:
                raise ValueError(f"bad hunk header {line!r}")
            new_line = int(m.group(1))
            in_hunk = True
        elif not in_hunk:
            continue
        elif line.startswith("+"):
            added.append((new_line, line[1:]))
            new_line += 1
        elif line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("\\"):
            pass  # "\ No newline at end of file"
        else:
            new_line += 1
    return added, removed


def collect_changes(repo: str, base: str, head: str) -> list[Change]:
    """Changes from merge-base(base, head) to head, as a PR diff shows them."""
    mb = git(repo, "merge-base", base, head).decode().strip()
    head_sha = git(repo, "rev-parse", "--verify", head + "^{commit}").decode().strip()
    raw = git(repo, "diff", "--raw", "-z", "--no-renames", "--no-ext-diff", mb, head_sha).split(b"\0")
    numstat = git(repo, "diff", "--numstat", "-z", "--no-renames", "--no-ext-diff", mb, head_sha).split(b"\0")
    binary = set()
    for rec in numstat:
        parts = rec.split(b"\t", 2)
        if len(parts) == 3 and parts[0] == b"-" and parts[1] == b"-":
            binary.add(parts[2].decode("utf-8", "replace"))

    changes = []
    i = 0
    while i < len(raw) - 1:
        meta = raw[i].decode()
        if not meta.startswith(":"):
            i += 1
            continue
        path = raw[i + 1].decode("utf-8", "replace")
        i += 2
        old_mode, new_mode, _, _, status = meta[1:].split(" ", 4)
        c = Change(path=path)
        changes.append(c)
        modes = {old_mode, new_mode} - {"000000"}
        if modes - {"100644", "100755"}:
            c.unreadable = f"not a regular file (mode {old_mode} -> {new_mode}); symlinks and submodules are not allowed"
            continue
        if path in binary:
            c.unreadable = "binary file"
            continue
        try:
            if old_mode != "000000":
                c.old_text = git(repo, "show", f"{mb}:{path}").decode("utf-8")
            if new_mode != "000000":
                c.new_text = git(repo, "show", f"{head_sha}:{path}").decode("utf-8")
            patch = git(repo, "diff", "-U0", "--no-renames", "--no-ext-diff", "--no-color", mb, head_sha, "--", path)
            c.added, c.removed = parse_hunks(patch.decode("utf-8"))
        except (subprocess.CalledProcessError, UnicodeDecodeError, ValueError) as e:
            c.unreadable = f"cannot read the diff: {e}"
    return changes


def check_range(repo: str, base: str, head: str, rules: dict | None = None) -> list[Violation]:
    return check_changes(rules or load_rules(), collect_changes(repo, base, head))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", required=True, help="the default branch, e.g. origin/master")
    ap.add_argument("--head", required=True, help="the commit to check")
    ap.add_argument("--rules", default=DEFAULT_RULES)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        violations = check_range(args.repo, args.base, args.head, load_rules(args.rules))
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        msg = e.stderr.decode(errors="replace").strip() if isinstance(e, subprocess.CalledProcessError) else str(e)
        print(f"bench-gate: check could not run: {msg}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([v.__dict__ for v in violations], indent=2))
    elif violations:
        print(f"bench-gate: {len(violations)} violation(s):")
        for v in violations:
            print(f"  {v}")
    else:
        print("bench-gate: all changed paths are allowed")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
