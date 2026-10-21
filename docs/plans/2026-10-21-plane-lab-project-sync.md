---
status: in-progress
last_modified: 2026-10-21
summary: "Track lab briefs and repo plans in production Plane: a project per lab category and per repo, a one-time import, then an in-cluster plane-sync CronJob. Briefs: Plane owns status, synced back by PR. Plans (incl. open plan PRs): the repo owns status, Plane mirrors"
---

# Lab projects and repo plans in Plane: import and sync

## Goal

George, 2026-10-20: "I'd like to start tracking all these projects in plane". The lab repo
(`gjcourt/lab`) holds 140 project briefs, one markdown file each, with `status:` in frontmatter and a
per-category `projects.md` index. This plan puts every brief in production Plane
(`plane.burntbytes.com`, #1624) and keeps the two in step. Repo plans (`docs/plans/*`, including
plans still in an open PR) are tracked there too, so a plan waiting for review can't be lost.

## Decisions (George, 2026-10-20)

- **Structure:** one Plane project per lab category (7), plus one per active repo. Homelab and
  Domestique come first.
- **Source of truth:** Plane owns status. The lab repo is synced back, so the briefs stay accurate on
  GitHub.
- **Cancelled** becomes a fourth lab status (George, 2026-10-21: "Add cancelled"). A lab PR adds it to
  the invariants (`ALLOWED_STATUS`) and the README Status line, before the import.
- **README counts** are updated by the sync PR along with the briefs and `projects.md` rows (George:
  "Yes").
- **Repo plans are in the first version** (George, 2026-10-21, after asking how a homelab-only plan
  such as Music Assistant, #1574, fits). A plan is a Plane item in its repo's project, including
  plans still in an open PR. See [Repo plans](#repo-plans).
- **Sync PRs merge themselves** once lab CI passes (George: "Merge themselves"). The bot enables
  GitHub auto-merge on its own PR. This is a normal merge after required checks, not a bypass: branch
  protection stays as it is, and the exception covers only this bot's status-sync PRs in `gjcourt/lab`.

## Who owns what

| Field                                                                | Owner     | Direction           |
| -------------------------------------------------------------------- | --------- | ------------------- |
| Existence of a project (new brief)                                   | lab repo  | repo → Plane        |
| Title, difficulty, time commitment, `depends_on`, link to the brief | lab repo  | repo → Plane        |
| Status                                                               | **Plane** | Plane → repo, by PR |
| Comments, assignees, cycles, priority, sub-items                     | Plane     | Plane only          |

A brief is never created from Plane, because a brief is a written document. An item created
directly in Plane stays Plane-only, and the sync reports it rather than writing a file.

## Mapping

- **Projects:** one per category folder. The identifier comes from the folder prefix and name, e.g.
  `HOME` for `03-homelab-automation`, `AUD` for `01-audio-midi`. The project description links to the
  category's `projects.md`.
- **Work items:** one per brief.
  - **Matching:** `external_source = "lab"` and `external_id` = the brief number (e.g. `03-043`).
    The sync matches on these, never on titles, so renames are safe.
  - **Name:** `03-043 · IP Addressing Cleanup…`, matching the index.
  - **Description:** the first paragraph of `## Description`, plus a link to the file on GitHub. The
    brief stays the long-form document.
  - **Labels:** difficulty (`Easy` / `Medium` / `Hard`), plus `blocked-by:<dep>` for each
    `depends_on` entry.
- **Status:**

| Lab `status:`         | Plane state                     | Back to lab                       |
| --------------------- | ------------------------------- | --------------------------------- |
| Not Started           | Backlog                         | Backlog or Todo → Not Started     |
| In Progress           | In Progress                     | In Progress → In Progress         |
| Done                  | Done                            | Done → Done                       |
| Cancelled             | Cancelled                       | Cancelled → Cancelled             |

## Repo plans

Three levels of work, each with one home:

```text
Lab brief (why / what)     lab repo        03-043-ip-addressing-cleanup.md
  └─ Plan (how)            a repo's docs/plans/   2026-10-12-music-assistant.md
       └─ Tickets (steps)  Plane only      created by the 03-042 planner
            └─ PRs         GitHub          linked to their ticket
```

A plan needs no lab brief. Ops work such as Music Assistant is a plan in the Homelab project. A brief
is written only when the work belongs in the catalog: learning, a build, or something spanning
repos.

**Scope:** every repo with a `docs/plans/` directory. Today that is `gjcourt/homelab`; other repos
join when they add one. A repo's Plane project exists even when it has no plans yet (Domestique).

**Ownership is the reverse of briefs.** A plan's status changes because PRs merge, so the repo owns
it and Plane only mirrors it:

| Field                                     | Owner    | Direction    |
| ----------------------------------------- | -------- | ------------ |
| Existence, title (`# H1`), summary, links | repo     | repo → Plane |
| Status (frontmatter, or the open PR)      | **repo** | repo → Plane |
| Comments, tickets under it, assignees     | Plane    | Plane only   |

A state change made in Plane on a plan item is reverted on the next sync and reported, so the repo
stays the single source.

**Mapping:**

- **Matching:** `external_source = "plan:<repo>"`, `external_id` = the plan's filename.
- **Description:** the frontmatter `summary`, a link to the file on `master` (or to the PR while it
  is open), and the PR number.
- **Status:**

| Plan state                                   | Plane state                             |
| -------------------------------------------- | --------------------------------------- |
| In an open PR, not on `master` yet           | **Proposed** (a custom Backlog-group state) |
| `planned`                                    | Todo                                    |
| `in-progress`                                | In Progress                             |
| `complete`                                   | Done                                    |
| `superseded`                                 | Cancelled, linked to `superseded_by`    |
| `abandoned`, or its PR closed without merge  | Cancelled                               |

**Open plan PRs:** the sync lists open PRs in each repo and treats any new file under `docs/plans/`
in them as a Proposed plan. That is how #1574 (Music Assistant, open since 2026-10-12) would appear
today. When the PR merges, the item moves to the plan's frontmatter state.

**"Implements" links:** a plan may declare the brief it serves with an optional frontmatter key,
`lab: 03-043` (or a list). `plans-index` ignores unknown keys, so this needs no tooling change. The
sync adds a Plane relation between the plan item and the brief item, so a brief shows which plans
carry it out.

## API findings (staging, 2026-10-21)

Step 3, measured against staging Plane 1.4.2 (`plane.stage.burntbytes.com`, workspace
`my-test-workspacce`) in two throwaway projects, `SYNCT` and `SYNCU`, deleted afterwards. All paths
are under `/api/v1/workspaces/<slug>/`, authenticated with the `X-API-Key` header.

| Area | Finding | Design consequence |
| --- | --- | --- |
| Work items | `POST/GET/PATCH projects/<id>/work-items/`; create takes `name`, `description_html`, `state`, `labels` (ids), `external_id`, `external_source`. PATCH of `state` and `name` works. | As planned. |
| Dedupe on create | A second create with the same `external_id` + `external_source` returns **409** with the existing item's `id`. | The importer can create blindly and fall back to the returned id, with no prior lookup. |
| Dedupe scope | `external_id` uniqueness is **per project**: the same pair was accepted in a second project. | Matching is always (project, `external_source`, `external_id`). A brief that moves category would duplicate; the sync detects that by scanning all projects and reports it. |
| Lookup | `GET work-items/?external_id=X&external_source=Y` returns **the single item** (200), or **404** when it doesn't exist, not a list. | The client handles the single-object and 404 shape. |
| States | `GET/POST projects/<id>/states/`. New projects get Backlog, Todo, In Progress, Done and Cancelled. A custom `Proposed` state in the `backlog` group was created (200). | As planned. |
| Labels | `POST projects/<id>/labels/` returns 201. A duplicate name returns **409** with the existing label's `id`. | Same create-or-reuse pattern as work items. |
| Relations | `GET/POST work-items/<id>/relations/` with `{"relation_type", "issues": [...]}`. Types: `blocking`, `blocked_by`, `duplicate`, `relates_to`, `start_after`, `start_before`, `finish_after`, `finish_before`. There is **no "implements"** type. Relations work **across projects** and show on both items. Re-posting the same relation is idempotent (201, still one relation). | Plan → brief uses **`relates_to`**. The item description carries the direction ("Implements 03-043"). |
| Links and comments | `POST work-items/<id>/links/` (`url`, `title`) and `POST work-items/<id>/comments/` (`comment_html`) work. | PR links on plan items; conflict reports as comments. |
| Pagination | Cursor paging: `per_page` plus `cursor` (`next_cursor`, `next_page_results`); `fields=` trims the payload. | The client always walks cursors at `per_page=100` with `fields`. |
| Rate limit | **60 requests per minute per API key**. A burst got 429 (`RATE_LIMIT_EXCEEDED`) at about the 60th request in the window, with `Retry-After` and `X-RateLimit-Remaining` headers. | The importer paces itself at 1 request/s and honours `Retry-After`. A first import of about 140 briefs and 70 plans, plus labels and relations, is roughly 450 requests, or about 8 minutes. The sync and the agents must **not share a key**; plane-sync uses its own bot token. |
| Projects | `POST projects/` takes an `identifier` of at most **12 chars**, letters and digits only (no `-`). Project **names** reject special characters too: `&` and `-` both returned 400. `POST projects/<id>/members/` with `{"member", "role"}` adds a member (201). | Names are sanitized (`Audio & MIDI` → `Audio and MIDI`, `Cross-Disciplinary` → `Cross Disciplinary`). Identifiers like `AUDIO`, `WOOD`, `HOMEAUTO`, `FIN`, `PIANO`, `COFFEE`, `CROSS`, `HOMELAB`, `DOMESTIQUE`. The bot creates the projects, so the importer adds George (role 20) to each one it creates. |

## How it runs

1. **One-time import** (`plane-sync import --dry-run`, then for real).
   - Creates the 7 projects and their states.
   - Creates 140 work items with labels.
   - Prints a summary: projects, items and labels created, and anything skipped.
   - It is safe to re-run: existing items are matched by `external_id` and updated, never duplicated.
2. **Ongoing sync:** a CronJob in a new `plane-sync` namespace runs `plane-sync reconcile` every 30
   minutes.
   - **repo → Plane:** new briefs become items, and changed titles or labels are updated. Plans on
     `master` and in open PRs are created or updated, with their status mirrored.
   - **Plane → repo:** status differences are gathered into a single branch,
     `chore/plane-status-sync`, which updates both the brief's frontmatter and its `projects.md` row.
     It also updates the README Status counts. One PR is opened or updated, titled
     "chore(plane-sync): status updates", with GitHub auto-merge enabled so it merges once lab CI
     passes. The job never pushes to `main` and never merges past a failing or pending check.
   - **Report:** a summary of Plane-only items, items whose brief was deleted, and any conflicts.

**Conflict rule:** if a brief's `status:` changed in git since the last sync AND the Plane state also
changed, the job writes neither. It flags the conflict on the Plane item (a comment) and in the PR
body. Otherwise Plane wins.

## Build

- **Code:** `images/plane-sync/`, a small Python tool, following the repo's `images/*` pattern.
  - It uses Plane's REST API through the `work-items` paths, since the old `issues` paths are
    deprecated.
  - It edits YAML frontmatter while preserving key order and quoting, so the lab repo's prettier and
    invariant checks stay clean.
  - It is built to ghcr by the existing image workflow.
- **Manifests:** `apps/base/plane-sync/` plus a production overlay containing:
  - the CronJob, with `concurrencyPolicy: Forbid`;
  - a NetworkPolicy allowing egress only to DNS, `plane-prod`'s api on 8000, and GitHub over 443;
  - a matching ingress rule in `plane-prod`'s policy.
- **Secrets** (`.yaml.example` only; SOPS is operator-only):
  - a Plane API token for a dedicated `plane-sync` bot user, a workspace member, not George's own
    token;
  - a GitHub fine-grained token or App installation scoped to `gjcourt/lab` with contents and pull
    requests write, plus read-only contents and pull requests on each repo with plans (today
    `gjcourt/homelab`). The branch protection on `main` stays as it is. The repo's "Allow auto-merge"
    setting must be on (George's OK, or he flips it).
- **No staging deploy of the CronJob.** Staging Plane is a test instance, and a second sync target
  would open duplicate PRs against the same lab repo. Instead, the importer and reconciler are tested
  against staging Plane from a workstation run, with `--dry-run` for the git side.

## Steps

1. **This plan PR.** Review the decisions and the mapping.
2. **Lab PR: Cancelled status.** Add `Cancelled` to `scripts/check_invariants.py`'s allowed set and the
   README Status line format, so the import and sync can write it.
3. **Verify the API against staging:**
   - the work-items create, list and patch schemas;
   - the `external_id` / `external_source` filters;
   - the states endpoint;
   - labels;
   - the API rate limit for 140 creates.

   Record the findings in this plan, because the third-party docs disagree on details.
4. **Tool PR:** `images/plane-sync/`, with unit tests for frontmatter round-trips (byte-identical when
   nothing changes) and for the status mapping.
5. **Dry-run the import against staging** (briefs and homelab plans, open plan PRs included), then do a real import into staging and have George look
   at it in the UI.
6. **Production:**
   - production Plane activated (#1624's secrets);
   - bot user and tokens created;
   - real import, then the CronJob PR.
7. **First sync round-trip:**
   - George moves one item in Plane;
   - the PR appears with the frontmatter and `projects.md` change;
   - lab CI passes;
   - the PR merges itself.

## Exit criteria

- [ ] All 140 briefs exist in production Plane across 7 projects, with labels and links, and a
      re-run of the import creates nothing new.
- [ ] A new brief merged to the lab repo appears in Plane within one sync interval.
- [ ] A state change in Plane produces one status-sync PR that passes lab CI and merges itself,
      touching only `status:` lines, `projects.md` rows and the README Status counts.
- [ ] Every homelab plan on `master` is in the Homelab project with its mirrored state, and an open
      plan PR (e.g. #1574) shows as Proposed, then moves to the frontmatter state after merge.
- [ ] A plan with `lab: <NN-NNN>` is related to that brief's item.
- [ ] A state change made in Plane on a plan item is reverted and reported.
- [ ] A simulated conflict is flagged and nothing is overwritten.
- [ ] The sync's NetworkPolicy allows only DNS, Plane's api and GitHub.

## Open questions

None open. George answered all three on 2026-10-21 (see Decisions).

## Later (not in this plan)

- **Plan steps as tickets:** the 03-042 planner turns a merged plan's steps into tickets under the
  plan item. This plan only creates the plan item that the planner hangs them on.
- **Exit criteria as sub-items** or checklists, if George wants per-criterion tracking.

## Risks

- **Formatting churn.** A frontmatter rewrite that reorders keys or changes quoting would make every
  sync PR noisy and could fail prettier. The round-trip test in step 3 is the guard.
- **Rate limits.** 140 creates in one burst may hit Plane's API key limit. Step 2 measures it, and
  the importer paces itself.
- **Two sources of edits.** If George edits a brief's status by hand, the conflict rule catches it.
