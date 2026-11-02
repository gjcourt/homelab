"""plane-sync CLI.

    plane-sync import    [--dry-run]
    plane-sync reconcile [--dry-run] [--git-dry-run]

Configuration comes from the environment (the CronJob's env and Secrets):

    PLANE_URL           e.g. http://plane-api.plane-prod.svc.cluster.local:8000
    PLANE_WORKSPACE     workspace slug
    PLANE_API_KEY       or PLANE_API_KEY_FILE
    GITHUB_TOKEN        or GITHUB_TOKEN_FILE (lab: contents+PRs write; plan repos: read)
    OWNER_EMAIL         added as admin to every project the bot creates
    STATE_FILE          JSON state (last synced statuses), default ./plane-sync-state.json
    LAB_REPO            owner/repo, default gjcourt/lab
    LAB_PATH            use an existing lab checkout instead of cloning
    REPO_PROJECTS       repo:IDENT:Name[:plans], comma-separated
                        default "homelab:HOMELAB:Homelab:plans,domestique:DOMESTIQUE:Domestique"
                        The projects must already exist with the bot as a Member; plane-sync
                        skips and reports one it can't work in rather than creating it.
    PLAN_PATH_<REPO>    use an existing checkout for that repo's plans, read as checked out;
                        REPO upper-cased with "-" as "_" (PLAN_PATH_PLANE_MOBILE)
    GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL   identity for the status-sync commit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from . import github as ghmod
from .plane import Plane
from .sync import Config, RepoProject, Syncer

SYNC_BRANCH = "chore/plane-status-sync"
PR_TITLE = "chore(plane-sync): status updates"


def secret(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    f = os.environ.get(f"{name}_FILE")
    return Path(f).read_text().strip() if f else None


def plan_path_var(repo: str) -> str:
    return "PLAN_PATH_" + repo.upper().replace("-", "_")


def parse_repo_projects(spec: str, workdir: Path, token: str | None, owner: str,
                        gh=None) -> list[RepoProject]:
    out = []
    for part in filter(None, (s.strip() for s in spec.split(","))):
        bits = part.split(":")
        repo, ident, name = bits[0], bits[1], bits[2]
        plans = len(bits) > 3 and bits[3] == "plans"
        path = None
        branch = "master"
        if plans:
            local = os.environ.get(plan_path_var(repo))
            path = Path(local) if local else ghmod.clone(owner, repo, workdir / repo, token)
            if gh is not None:
                branch = gh.default_branch(owner, repo) or branch
        out.append(RepoProject(repo, ident, name, path, branch))
    return out


def write_back(syncer: Syncer, cfg: Config, gh, token: str | None, workdir: Path, git_dry_run: bool) -> dict:
    """Carry Plane's brief statuses into the lab repo via the status-sync PR."""
    rep = syncer.report
    if not rep.brief_writes:
        return {"pr": None, "files": []}
    owner, repo = cfg.lab_owner, cfg.lab_repo
    if git_dry_run:
        root = cfg.lab_root
        scratch = workdir / "lab-dry"
        ghmod.git("clone", "--quiet", str(root), str(scratch))
        files = syncer.apply_brief_writes(scratch)
        diff = ghmod.git("diff", "--stat", cwd=scratch)
        return {"pr": None, "files": [str(p.relative_to(scratch)) for p in files], "diff": diff}
    existing = gh.pull_for_branch(owner, repo, SYNC_BRANCH)
    root = ghmod.clone(owner, repo, workdir / "lab-write", token)
    if existing:
        ghmod.git("fetch", "--quiet", "origin", SYNC_BRANCH, cwd=root)
        ghmod.git("checkout", "--quiet", "-B", SYNC_BRANCH, f"origin/{SYNC_BRANCH}", cwd=root)
        # Keep the branch current with a normal merge (never a force-push).
        ghmod.git("merge", "--quiet", "--no-edit", "origin/main", cwd=root, check=False)
    else:
        # No open PR, but the branch can still exist: lab doesn't delete head
        # branches on merge, so a merged sync PR leaves its branch behind and a
        # fresh branch from main can't push over it. With no open PR nothing
        # depends on that branch, so delete it before starting again.
        if ghmod.git("ls-remote", "--heads", "origin", SYNC_BRANCH, cwd=root).strip():
            ghmod.git("push", "--quiet", "origin", "--delete", SYNC_BRANCH, cwd=root)
        ghmod.git("checkout", "--quiet", "-b", SYNC_BRANCH, cwd=root)
    files = syncer.apply_brief_writes(root)
    if not ghmod.git("status", "--porcelain", cwd=root).strip():
        return {"pr": existing and existing["number"], "files": []}
    ghmod.git("add", "-A", cwd=root)
    msg = "chore(plane-sync): status updates from Plane\n\n" + "\n".join(
        f"- {p}: {s}" for p, s in sorted(rep.brief_writes.items()))
    ghmod.git("-c", f"user.name={os.environ.get('GIT_AUTHOR_NAME', 'plane-sync')}",
              "-c", f"user.email={os.environ.get('GIT_AUTHOR_EMAIL', 'plane-sync@users.noreply.github.com')}",
              "commit", "--quiet", "-m", msg, cwd=root)
    ghmod.git("push", "--quiet", "origin", SYNC_BRANCH, cwd=root)
    body = ("Status changes made in Plane, written back to the briefs, `projects.md` rows and the "
            "README counts by plane-sync. Auto-merges once lab CI passes.\n\n"
            + "\n".join(f"- `{p}` → {s}" for p, s in sorted(rep.brief_writes.items())))
    if rep.conflicts:
        body += "\n\n**Conflicts (nothing written for these):**\n" + "\n".join(f"- {c}" for c in rep.conflicts)
    if existing:
        gh.update_pull_body(owner, repo, existing["number"], body)
        pr = existing
    else:
        pr = gh.create_pull(owner, repo, SYNC_BRANCH, "main", PR_TITLE, body)
    gh.enable_auto_merge(pr["node_id"])
    return {"pr": pr["number"], "files": [str(p.relative_to(root)) for p in files]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plane-sync")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="create projects and items; Plane takes the repo's statuses")
    imp.add_argument("--dry-run", action="store_true", help="no Plane writes")
    rec = sub.add_parser("reconcile", help="sync both ways under the ownership rules")
    rec.add_argument("--dry-run", action="store_true", help="no Plane writes")
    rec.add_argument("--git-dry-run", action="store_true", help="edit a scratch lab clone; no push/PR")
    args = ap.parse_args(argv)

    plane_key = secret("PLANE_API_KEY")
    if not plane_key:
        print("PLANE_API_KEY (or _FILE) is required", file=sys.stderr)
        return 2
    gh_token = secret("GITHUB_TOKEN")
    lab_full = os.environ.get("LAB_REPO", "gjcourt/lab")
    lab_owner, lab_repo = lab_full.split("/")
    plane = Plane(os.environ["PLANE_URL"], os.environ["PLANE_WORKSPACE"], plane_key,
                  dry_run=args.dry_run, log=lambda m: print(m, file=sys.stderr))
    gh = ghmod.GitHub(gh_token)
    with tempfile.TemporaryDirectory(prefix="plane-sync-") as tmp:
        work = Path(tmp)
        lab_root = Path(os.environ["LAB_PATH"]) if os.environ.get("LAB_PATH") else ghmod.clone(
            lab_owner, lab_repo, work / "lab", gh_token)
        cfg = Config(
            lab_root=lab_root, lab_owner=lab_owner, lab_repo=lab_repo,
            repo_projects=parse_repo_projects(
                os.environ.get("REPO_PROJECTS",
                               "homelab:HOMELAB:Homelab:plans,domestique:DOMESTIQUE:Domestique"),
                work, gh_token, lab_owner, gh),
            owner_email=os.environ.get("OWNER_EMAIL", ""),
            state_file=Path(os.environ.get("STATE_FILE", "plane-sync-state.json")),
        )
        syncer = Syncer(plane, cfg, gh=gh, log=lambda m: print(m, file=sys.stderr))
        report = syncer.run(initial=(args.cmd == "import"))
        result = report.summary()
        if args.cmd == "reconcile" and not args.dry_run:
            result["write_back"] = write_back(syncer, cfg, gh, gh_token, work, args.git_dry_run)
        elif args.cmd == "reconcile":
            result["would_write"] = report.brief_writes
        syncer.save()
        result["plane_requests"] = plane.requests
        print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
