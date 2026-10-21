---
status: planned
last_modified: 2026-10-21
summary: "Track the lab repo's project briefs in production Plane: one Plane project per lab category, a one-time import, then an in-cluster plane-sync CronJob. Briefs create items; Plane owns status, synced back to the lab repo by PR"
---

# Lab projects in Plane: import and two-way sync

## Goal

George, 2026-10-20: "I'd like to start tracking all these projects in plane". The lab repo
(`gjcourt/lab`) holds 140 project briefs, one markdown file each, with `status:` in frontmatter and a
per-category `projects.md` index. This plan puts every brief in production Plane
(`plane.burntbytes.com`, #1624) and keeps the two in step.

## Decisions (George, 2026-10-20)

- **Structure:** one Plane project per lab category (7), plus one per active repo. Homelab and
  Domestique come first, and their projects start empty (see Later).
- **Source of truth:** Plane owns status. The lab repo is synced back, so the briefs stay accurate on
  GitHub.

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
| (no lab equivalent)   | Cancelled                       | **open question**, see below      |

## How it runs

1. **One-time import** (`plane-sync import --dry-run`, then for real).
   - Creates the 7 projects and their states.
   - Creates 140 work items with labels.
   - Prints a summary: projects, items and labels created, and anything skipped.
   - It is safe to re-run: existing items are matched by `external_id` and updated, never duplicated.
2. **Ongoing sync:** a CronJob in a new `plane-sync` namespace runs `plane-sync reconcile` every 30
   minutes.
   - **repo → Plane:** new briefs become items, and changed titles or labels are updated.
   - **Plane → repo:** status differences are gathered into a single branch,
     `chore/plane-status-sync`, which updates both the brief's frontmatter and its `projects.md` row.
     One PR is opened or updated, titled "chore(plane-sync): status updates", and George merges it.
     The job never pushes to `main` and never merges.
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
  - a GitHub fine-grained token or App installation scoped to `gjcourt/lab`, with contents and pull
    requests write. The branch protection on `main` stays as it is.
- **No staging deploy of the CronJob.** Staging Plane is a test instance, and a second sync target
  would open duplicate PRs against the same lab repo. Instead, the importer and reconciler are tested
  against staging Plane from a workstation run, with `--dry-run` for the git side.

## Steps

1. **This plan PR.** Review the decisions, the mapping and the open questions.
2. **Verify the API against staging:**
   - the work-items create, list and patch schemas;
   - the `external_id` / `external_source` filters;
   - the states endpoint;
   - labels;
   - the API rate limit for 140 creates.

   Record the findings in this plan, because the third-party docs disagree on details.
3. **Tool PR:** `images/plane-sync/`, with unit tests for frontmatter round-trips (byte-identical when
   nothing changes) and for the status mapping.
4. **Dry-run the import against staging,** then do a real import into staging and have George look
   at it in the UI.
5. **Production:**
   - production Plane activated (#1624's secrets);
   - bot user and tokens created;
   - real import, then the CronJob PR.
6. **First sync round-trip:**
   - George moves one item in Plane;
   - the PR appears with the frontmatter and `projects.md` change;
   - lab CI passes;
   - George merges.

## Exit criteria

- [ ] All 140 briefs exist in production Plane across 7 projects, with labels and links, and a
      re-run of the import creates nothing new.
- [ ] A new brief merged to the lab repo appears in Plane within one sync interval.
- [ ] A state change in Plane produces one status-sync PR that passes lab CI, touching only
      `status:` lines and `projects.md` rows.
- [ ] A simulated conflict is flagged and nothing is overwritten.
- [ ] The sync's NetworkPolicy allows only DNS, Plane's api and GitHub.

## Open questions

1. **Cancelled.** The lab repo allows only Not Started / In Progress / Done. When an item is
   Cancelled in Plane, the options are:
   - add `Cancelled` to the lab invariants and the README counts;
   - map it back to `Not Started` with a `cancelled` label;
   - leave the brief untouched and only report it.
2. **README counts.** The README Status line ("1 done, 15 in progress") isn't checked by the
   invariants today. Should the sync PR update it too? My suggestion: yes, since it is the same edit.
3. **Merge cadence.** Should status-sync PRs wait for George's merge every time, or may they
   auto-merge once lab CI passes? They touch only status lines, so auto-merging is the cheaper option,
   but it changes the "George merges" rule for this one bot, so it is George's call.

## Later (not in this plan)

- **Homelab and Domestique projects:** import `docs/plans/*` as items, with plan `status:` mapped the
  same way. These are the agent desk planner's input (lab 03-042), so they belong with that work.
- **Exit criteria as sub-items** or checklists, if George wants per-criterion tracking.

## Risks

- **Formatting churn.** A frontmatter rewrite that reorders keys or changes quoting would make every
  sync PR noisy and could fail prettier. The round-trip test in step 3 is the guard.
- **Rate limits.** 140 creates in one burst may hit Plane's API key limit. Step 2 measures it, and
  the importer paces itself.
- **Two sources of edits.** If George edits a brief's status by hand, the conflict rule catches it.
