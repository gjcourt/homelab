# plane-sync

Mirrors the lab repo's project briefs and each repo's `docs/plans/` into Plane, and writes Plane's
brief statuses back to the lab repo. Design and decisions:
[`docs/plans/2026-10-21-plane-lab-project-sync.md`](../../docs/plans/2026-10-21-plane-lab-project-sync.md).

| What | Owner of status | Direction |
| --- | --- | --- |
| Lab brief (`gjcourt/lab`, `NN-NNN-*.md`) | **Plane** | repo creates the item; Plane status → lab repo by one auto-merging PR |
| Repo plan (`docs/plans/*.md`, incl. plans only in an open PR) | **repo** | repo → Plane; a Plane-side state change is reverted and reported |

## Commands

```sh
python -m plane_sync import    [--dry-run]                  # create projects/items; Plane takes the repo's statuses
python -m plane_sync reconcile [--dry-run] [--git-dry-run]  # sync both ways under the ownership rules
```

`--dry-run` makes no Plane writes. `--git-dry-run` applies the lab edits to a scratch clone and
prints the diff, with no push and no PR. Configuration is environment-only; see the docstring at the
top of [`plane_sync/cli.py`](plane_sync/cli.py).

## Behaviour worth knowing

- **Matching** is on `external_source` + `external_id`, never titles. Briefs use `lab` + the brief
  number. Plans use `plan:<repo>` + the filename.
- **Repo projects are never created by plane-sync.** Each repo in `REPO_PROJECTS` maps to a Plane
  project a person made, and the bot must be a Member of it. A project the bot can't see, or is
  listed but not a member of (Plane lists public projects to everyone but answers 403 on their
  items), is skipped and reported in `skipped_projects`. Lab category projects are still created.
- **Plans that already have a ticket are left alone.** Before creating a plan item, plane-sync
  looks for a hand-made item (no `external_source`) in the same project that tracks the plan: one
  the plan names (`Ticket: FAX-3` in its text, or its PR's title `docs(FAX-3): ...`), or one whose
  name equals the plan's title, ignoring case, punctuation and a `Plan:` prefix. If found, no
  mirror item is made and the pair is reported in `plans_tracked_by_ticket`. That ticket's state
  stays with whoever works it.
- **Plans with no frontmatter `status:`** are not mirrored (there is no status to mirror) and are
  listed in `plans_without_status`. A repo with no `docs/plans/` syncs nothing.
- **Statuses.** Brief statuses are Not Started → Backlog, In Progress → In Progress, Done → Done,
  and Cancelled → Cancelled. Plane → lab maps on the state *group*, so Todo also reads as Not
  Started. Plan statuses are planned → Todo, in-progress → In Progress, complete → Done, and
  superseded/abandoned → Cancelled. A plan that is only in an open PR is **Proposed**, a custom
  backlog-group state, and goes to Cancelled if the PR closes unmerged.
- **Conflict rule:** if a brief's status changed in git *and* in Plane since the last sync, to
  different values, nothing is written. The item gets one comment and the PR body lists the
  conflict. Otherwise Plane wins.
- **State file:** the last synced brief statuses and plan states live in `STATE_FILE`. The conflict
  rule and the plan-revert detection need it.
- **Lab edits** are line-local: the frontmatter `status:` line, the `projects.md` Status cell
  (re-padded to its width), and the README Status counts (that paragraph is re-wrapped at 100, as
  prettier does). They pass the lab's prettier and `check_invariants.py`; the tests check both.
- **Rate limit:** Plane allows 60 requests/min per API key. The client paces at 1 request/s and
  honours `Retry-After`. A first import is about 300 requests.
- **`relates_to`** links a plan to the briefs named in its `lab:` frontmatter key. Plane has no
  "implements" relation.

## Tests

```sh
pip install -r requirements.txt -r requirements-dev.txt
pytest -q tests                         # unit tests with a fake Plane and GitHub
LAB_PATH=/path/to/lab pytest -q tests   # plus round-trip and invariant tests on a real lab checkout
```
