# plans-index

Validates the YAML frontmatter of every plan in `docs/plans/` and generates
the status-grouped Document Index in `docs/plans/README.md` between the
`BEGIN PLANS INDEX` / `END PLANS INDEX` markers.

The index is **generated — do not edit it by hand.** Edit the plan's
frontmatter (`status`, `summary`, `blocked_on`, `superseded_by`) and
regenerate.

## Usage

From the repo root:

```bash
make plans-index        # regenerate the index after changing frontmatter
make plans-index-check  # CI gate: fails on schema violations or index drift
```

Or directly:

```bash
cd scripts/plans-index
go run . -write   # regenerate
go run . -check   # validate only
```

## What `-check` enforces

- Filenames match `YYYY-MM-DD-<slug>.md`.
- Frontmatter exists with `status` ∈ planned | in-progress | complete |
  superseded | abandoned, a `YYYY-MM-DD` `last_modified`, and a non-empty
  `summary`.
- The generated index block in `README.md` matches frontmatter reality.

Stdlib only; no dependencies.
