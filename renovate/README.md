# Shared Renovate preset

`default.json` is the shared Renovate configuration for the `gjcourt/*` repos.
Consumer repos extend it instead of each carrying their own drifting copy.

## Consuming it

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>gjcourt/homelab//renovate/default"]
}
```

## Why `.json` and not `.json5`

**The implicit `default` lookup resolves `.json` only.** In Renovate's
`fetchPreset` (`lib/config/presets/util.ts`), a preset whose filename is exactly
`default` is fetched as `<path>/default.json`, with a single deprecated fallback
to `<path>/renovate.json`. `default.json5` is never attempted. So
`github>gjcourt/homelab//renovate/default` requires the file to be `default.json`.

A `.json5` file would work, but only if every consumer spelled the extension out —
`github>gjcourt/homelab//renovate/default.json5` — because the explicit-filename
branch does accept `.json5`. That trades a clean, uniform `extends` line across ten
repos for comment support in one file. Not worth it.

The cost is that JSON has no comments, which is why every rule carries a
`description` field and the reasoning lives in this file.

## The rules, and the failures that produced them

### `postUpdateOptions: ["gomodTidy"]`

Renovate rewrites `go.mod` and **adds** the new `go.sum` hashes, but never
prunes the superseded ones. Any repo with a `go mod tidy && git diff --exit-code`
check therefore fails on every single Go dependency PR.

Worse, the failure is not fixable in place: pushing a tidy commit onto a
`renovate/*` branch is discarded when Renovate rebuilds that branch, so each
failure had to be closed and replaced with a hand-built PR. That happened twice
before this preset existed. `gomodTidy` runs `go mod tidy` as a post-update step
so the PR arrives clean.

Tidying is opt-in and `config:recommended` does not enable it. It needs no extra
configuration on the official `renovate/renovate` image, which is Containerbase-
based and defaults to `binarySource=install`.

### Grouping GitHub Actions

One PR per cycle for action `uses:` bumps instead of one per action.

### Pulling language runtimes back out of that group

`actions/setup-python`'s `python-version` is a `uses-with` dependency, and
Renovate classifies `3.12` -> `3.14` as **minor** — Python's `3.x` *is* the minor
field. Without this rule a two-release interpreter jump ships inside a routine
non-major grouped PR and gets none of the scrutiny a major would.

Interpreter and runtime bumps are breaking in practice regardless of what semver
says. `groupName: null` pulls them out of the inherited group and
`additionalBranchPrefix` gives them a standalone, reviewable branch.

**Rule order matters.** Package rules apply in order and the last match wins, so
the runtime rule must stay *after* the grouping rule. `groupName: null` as a
group-removal mechanism is maintainer-endorsed but not documented as a named
feature — do not reorder these two rules casually.

## Merge semantics

`packageRules` is a mergeable array: a consumer repo's own rules are **appended
to** the preset's, not substituted for them. Repository config outranks a
resolved preset, so a repo can still override any scalar it sets explicitly.

This matters for `homelab` itself, whose `renovate.json` carries an `automerge`
rule for `digest`/`pin`/`patch`/`minor` that survives adoption of this preset.

`postUpdateOptions` mergeability is not conclusively documented; if a consumer
needs to *remove* an option rather than add one, verify the behaviour rather
than assuming.
