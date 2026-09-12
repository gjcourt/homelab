# GHCR package audit — 2026-09-09

Point-in-time audit of all `ghcr.io/gjcourt/*` container packages: **visibility
against repo visibility**, and **tag-naming compliance** with the date-sha
convention in [AGENTS.md](../../AGENTS.md#conventions).

**34 packages. 24 public, 10 private.** Two visibility mismatches, one in each
direction — see §1 and §1b.

## 1. Visibility — the headline

The question was "which public repos have private images". **Exactly one.**

| Package | Repo | Repo vis | Pkg vis | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **`pingo`** | `gjcourt/Pingo` | **PUBLIC** | **private** | ⚠️ **mismatch — publish this one** |
| `burntbytes` | `gjcourt/burntbytes` | PRIVATE | private | ✅ correct |
| `cadence` | `gjcourt/cadence` | PRIVATE | private | ✅ correct |
| `ladder` | `gjcourt/ladder` | PRIVATE | private | ✅ correct — personal-finance tool, **keep private** |
| `overture` | `gjcourt/tempo-interview` | PRIVATE | private | ✅ correct |
| `overture-bridge` | `gjcourt/tempo-interview` | PRIVATE | private | ✅ correct |
| `tempo-interview/acmeusd` | unlinked | — | private | ✅ correct (take-home) |
| `tempo-interview/bridge` | unlinked | — | private | ✅ correct (take-home) |
| `biometrics` | → renamed to `vitals` | n/a | private | 🗑️ **stale — delete, do not publish** |
| `synology-csi` | **no such repo** | n/a | private | 🗑️ **orphan — delete** |

### `pingo` — the one real fix

`gjcourt/Pingo` is public; the image is not. It is **actively used in-cluster**:

```text
infra/controllers/pingo/cronjob.yaml:26   image: ghcr.io/gjcourt/pingo:2026-06-20
namespace pingo                            carries a ghcr-secret purely because the image is private
```

Publishing it lets the `ghcr-secret` in the `pingo` namespace be dropped.

⚠️ **This cannot be scripted.** The GitHub Packages REST API exposes only
list/get/delete/restore — **there is no visibility endpoint**. GHCR visibility is
changed in the web UI:

```text
https://github.com/users/gjcourt/packages/container/pingo/settings
  -> Danger Zone -> Change visibility -> Public
```

(The local `gh` token also lacks `write:packages`, but that is moot — the endpoint
does not exist.)

### The two deletion candidates

**`biometrics`** — the repo was renamed to `vitals`; `gjcourt/biometrics` now
redirects. The package is a pre-rename leftover:

```text
biometrics   17 versions, last updated 2026-02-21, newest tag "2026-02-20-v3"
vitals       30 versions, last updated 2026-09-09, newest tag "2026-09-09-e499087"
```

**Do not publish it** — it would expose a seven-month-old superseded image.

**`synology-csi`** — unlinked, single tag `v1.2.0`, 23 of 24 versions untagged,
last touched **2025-06-28**, and no `gjcourt/synology-csi` repo exists. Fourteen
months cold.

⚠️ Both deletions are **irreversible** and need an explicit decision. `delete:packages`
is in scope, so they can be actioned once approved.

## 1b. The inverse direction — private repo, PUBLIC image

⚠️ **The original audit only asked "which public repos have private images".
Asking the question the other way found the more serious problem.**

| Package | Repo | Repo vis | Pkg vis | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **`vibrato`** | `gjcourt/vibrato` | **PRIVATE** | **public** | ⚠️ **exposure — decide** |

All other 23 public packages belong to public repos. `vibrato` is the only one.

**Verified genuinely pullable by anyone**, not merely mislabelled:

```text
GET https://ghcr.io/token?scope=repository:gjcourt/vibrato:pull   -> HTTP 200
GET https://ghcr.io/v2/gjcourt/vibrato/manifests/latest (anon)    -> HTTP 200
66 versions, last updated 2026-08-27
```

So the compiled artifact of a private repo is world-downloadable.

**No credentials found in the image config:**

```text
ENV: PATH, NODE_VERSION=22.23.2, YARN_VERSION=1.22.22, NODE_ENV=production
suspicious names: 0
```

⚠️ **That check covers the image *config* only.** Secrets committed *inside* a
layer — a `.env`, a config file with a token — would not appear there. Scanning
layer contents across all 66 versions has **not** been done.

**Assessment: source/IP exposure, not a credential leak.** Two ways to resolve,
and it is a judgement call:

- **Make the package private** to match the repo —
  `https://github.com/users/gjcourt/packages/container/vibrato/settings`
- **Make the repo public**, if openness was the intent (it is an espresso
  controller, so plausibly yes).

⚠️ **Making the package private now does not un-publish what has already been
pulled or cached.** It only stops future anonymous pulls.

**Lesson for future audits: check both directions.** Public-repo/private-image is
an inconvenience; private-repo/public-image is a disclosure.

## 2. Tag-convention compliance

Convention: `YYYY-MM-DD-<sha7>`. `AGENTS.md` also accepts legacy
`YYYY-MM-DD` and `YYYY-MM-DD-N`.

⚠️ **Two things are NOT violations and were filtered out of these results.** Both
were flagged by the automated pass and are false positives:

- **`latest`** — the convention governs *pinnable* tags; `latest` alongside them is
  normal and appears on ~24 packages.
- **`YYYY-MM-DD-N`** — explicitly documented as accepted legacy in `AGENTS.md`
  (`signal-bridge` `-2/-3/-4`, `overture` `-1..-5`).

### Genuine findings

**A. The `*scope` family emits three surplus tags** — `modemscope`, `netscope`,
`thermalscope` share a `build.yml` template that pushes five tags per build:

```yaml
:${{ branch }}          -> "main"        mutable, unpinnable
:${{ sha }}             -> "e2b2d76"     bare sha7, no date prefix
:${{ date }}            -> "2026-07-26"  legacy
:${{ date }}-${{ sha }} -> convention    ✅
:latest                                  mutable
```

**The convention tag is present** — these are extras, not wrong tags. Cost is
registry bloat: `netscope` carries 17 bare-sha tags across 45 versions,
`thermalscope` 7. Worth trimming the template to `date-sha` + `latest`, but this is
hygiene, not breakage.

**B. Full 40-character SHA tags** — `cadence` (7 tags) and `vibrato` (several),
e.g. `692fa1b90b0afccf79ecca8ade025b107144f29c`. Unreadable and non-conforming.

**C. No date-sha tag at all** — `tempo-interview/acmeusd` and
`tempo-interview/bridge` carry only `latest`. Private take-home repos, so low
stakes, but nothing there is pinnable.

**D. `snapcast-hifiberry`** — single legacy tag `2026-05-05`, zero date-sha tags.

### Exemplary

`golinks` (23 conforming tags), `vitals` (18+), `pingo` (14+), `soundbyte` /
`soundbyte-client` (12+ each) all follow the convention cleanly.

## 3. Secondary findings

**Nine namespaces carry a `ghcr-secret` for images that are already public:**

```text
needed:      burntbytes-prod, ladder, overture-prod, pingo
vestigial:   changes-prod, changes-stage, finance-dashboard,
             flashcards-prod, flashcards-stage, golinks-prod,
             golinks-stage, vitals-prod, vitals-stage
```

Harmless (an unused pull secret costs nothing) but dead weight left behind when
those images were made public. `pingo` joins the vestigial list once published.

**`pingo` is pinned three months stale in-cluster.** The cronjob pins
`2026-06-20` — a legacy date-only tag — while `2026-09-05-9929447` exists. Worth a
bump, and worth pinning the date-sha form rather than the mutable-ish date form.

## 4. Method and caveats

Collected via `gh api /user/packages?package_type=container` and
`/user/packages/container/<name>/versions`, cross-referenced against
`gh repo view` for repo visibility. Tag classification was parallelised across two
sub-agents.

⚠️ **The second batch reported approximate counts** ("120+", "many", "multiple")
for `overture`, `overture-bridge`, `vibrato`, `soundbyte`, `soundbyte-client` and
`vitals`. Those specific totals are indicative, not exact. **The visibility audit
in §1 is exact** — it was collected directly, not delegated.

Package names containing `/` require URL-encoding (`tempo-interview%2Facmeusd`).
