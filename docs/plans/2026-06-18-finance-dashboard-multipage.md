# Plan — finance.burntbytes.com → 4-page personal-finance site

## Context
The deployed dashboard (`finance.burntbytes.com`, the `finance-dashboard` app in `gjcourt/homelab`) currently serves **one** static page: the balance sheet (`report_html.py` → `index.html`, fed by an encrypted `positions.yaml`). George wants it to become a small **4-page static site**:

1. **Balance Sheet** (exists) — net worth / IPS allocation / FI / concentration / holdings.
2. **Cash Flow** — the monthly model from `cashflow.py`, but reading inputs from encrypted YAML instead of hardcoded comp.
3. **Real Estate (STR)** — the dual-structure rental pro-forma (interactive) + a candidate shortlist driven by a Redfin export.
4. **Runway** — an interactive "can I retire now / how much runway" projector with sliders (client-side JS), showing **both** a deterministic projection (conservative/expected/optimistic) **and** a Monte Carlo success-probability.

Requirements from George: every data file is an **encrypted YAML**; pages are **statically served** (no backend — "interactive" = in-browser JS only); **shared stylesheet + easy nav** across all pages.

All scripts live in two places kept in sync: the canonical `~/src/utility/portfolio/` (not a git repo) and the image build context `~/src/homelab/images/finance-dashboard/`.

---

## Architecture

### Shared rendering layer — new `webcommon.py`
A single module imported by every page renderer, providing:
- `page(title, active_nav, body, head_extra="")` → wraps a body in the full HTML doc with the shared `<head>` (links `style.css`), the **nav header** (4 links: Balance Sheet · Cash Flow · Real Estate · Runway; `active_nav` highlights current), and footer.
- Shared helpers moved here: `usd`, `pct`, `bar` (currently duplicated in `portfolio.py`/`report_html.py`).
- A standalone **`style.css`** (dark theme, extracted from `report_html.py`'s inlined CSS) served at `/srv/html/style.css` and linked by all pages — one place to restyle everything.

### Per-page renderers (each reads its encrypted YAML, returns an HTML body, wrapped by `webcommon.page`)
- **`report_html.py`** (balance sheet) — refactor to import `webcommon` and emit `index.html`. Reads `positions.yaml`. Behavior unchanged, just re-themed via shared CSS + nav.
- **`cashflow.py`** — extract the hardcoded `DEFAULTS` dict into **`cashflow.yaml`**; add `render_html()` that builds the monthly table + summary as an HTML body via `webcommon`. Keep the existing CLI text mode.
- **`realestate.py`** (new) — two sections:
  - **Interactive pro-forma**: port `re_taxshield.py`'s `proforma()` math to client-side JS with sliders (price, down %, rate, nightly, occupancy, insurance %, cost-seg %, marginal rate). Live-updates net cash / tax shield / year-1 & steady after-tax. Defaults from **`str.yaml`**.
  - **Candidate shortlist**: a table rendered from **`candidates.yaml`** (address, price, acres, drive-mi, DOM, fit-score), produced by extending `redfin_filter.py` with an **`--emit-yaml`** mode (Redfin CSV → filtered+scored → `candidates.yaml`; reuses existing `hav_miles`, `fit_score`, `deals_view` logic). Clicking a candidate prefills the pro-forma price.
- **`runway.py`** (new) — emits `runway.html`. Bakes **`runway.yaml`** inputs as a JS object; all modeling runs in-browser:
  - Inputs: current investable, current age, end age (default 95), annual spend, pre-retirement annual savings, years-to-retirement (slider = retirement age), real-return mean+vol per scenario, inflation.
  - **Accumulation→decumulation** model (covers "savings rates over time" + "retire now"): balance grows with savings + returns until retirement age, then draws down by spend.
  - **Deterministic**: 3 lines (conservative/expected/optimistic real returns) → balance-over-time, with the depletion age annotated.
  - **Monte Carlo**: N≈2000 paths sampling annual returns ~Normal(mean,vol) → "**X% chance funds last to age 95**" + a percentile fan.
  - Sliders (retire age, annual spend, return, inflation, savings rate) recompute everything live.
  - Charts: **Chart.js** (pinned version, fetched at image-build time → `/srv/html/chart.min.js`, served locally — no CDN, no runtime egress).

### Data layer — one consolidated encrypted secret
Replace `secret-positions.yaml` with a single **`secret-finance-data.yaml`** (Secret `finance-dashboard-data`) holding all data files as keys, mounted at `/data/`:
`positions.yaml`, `cashflow.yaml`, `str.yaml`, `runway.yaml`, `candidates.yaml`. One secret = one mount, one encrypt step. Ship a `.example` template per file. SOPS-encrypted (same age recipient; encryption needs only the public key). The Redfin **CSV stays local** — `redfin_filter.py --emit-yaml` converts it to `candidates.yaml` which is what gets encrypted/committed (keeps everything encrypted-YAML, no raw CSV in the repo).

**Data edits are decoupled from image rebuilds** (data is *mounted*, not baked): updating a number = `sops` the secret + commit + `rollout restart` — no rebuild. To smooth that loop, add a **`scripts/update-finance-data.sh`** helper (or a Make target) that wraps: re-encrypt the chosen data file → commit on a branch → push → and prints the `rollout restart` command. Code/layout changes are the only thing that triggers an image rebuild.

### Image + deployment changes
- **Dockerfile**: `COPY` the new modules (`webcommon.py`, `realestate.py`, `runway.py`) + `report_html.py`/`cashflow.py`, the static assets (`style.css`), and a build-time `RUN` to fetch pinned `chart.min.js`. Deps stay **pyyaml only** (all charts are client-side; no server-side matplotlib).
- **`entrypoint.sh`**: render all four pages to `/srv/html/{index,cashflow,realestate,runway}.html`, copy `style.css`/`chart.min.js`, then `http.server`.
- **Manifests** (`apps/base/finance-dashboard/`): `deployment.yaml` mounts `finance-dashboard-data` at `/data`; `kustomization.yaml` swaps `secret-positions.yaml` → `secret-finance-data.yaml`. **NetworkPolicy unchanged** (DNS-only egress — the interactive JS runs in the user's browser, not the pod). Runbook + README updated for the multi-page layout and the new update flow.

---

## Files
**New:** `images/finance-dashboard/{webcommon.py, realestate.py, runway.py, style.css}`; `apps/base/finance-dashboard/secret-finance-data.yaml.example` (+ encrypted `secret-finance-data.yaml`); `~/src/utility/portfolio/{cashflow.yaml, str.yaml, runway.yaml}.example` data templates.
**Modify:** `images/finance-dashboard/{report_html.py, cashflow.py, Dockerfile, entrypoint.sh}`; `~/src/utility/portfolio/{report_html.py, cashflow.py, redfin_filter.py}` (canonical, add `--emit-yaml`); `apps/base/finance-dashboard/{deployment.yaml, kustomization.yaml}`; `docs/operations/apps/finance-dashboard.md`; `images/finance-dashboard/README.md`.
**Reuse:** `re_taxshield.py::proforma` (port to JS), `redfin_filter.py::{hav_miles, fit_score, col}`, `portfolio.py::value_position`.

## Verification
- **Local**: run each renderer against the `.example` YAMLs → open the 4 HTML files; click nav links; drag the runway + STR sliders (deterministic lines move, Monte Carlo % updates, STR after-tax recomputes). `docker build` + `docker run -v <sample-data>:/data` → curl/browse all 4 routes.
- **Deploy** (established flow): `redfin_filter.py --emit-yaml` from a Redfin export → fill the data YAMLs → encrypt into `secret-finance-data.yaml` → branch + PR → babysit → `/ship` (CI-green gate, normal merge) → `rollout restart deploy/finance-dashboard` → verify `https://finance.burntbytes.com/{,cashflow.html,realestate.html,runway.html}` render with the shared nav/style and the interactive charts work.

## Notes
- Per George's plan-workflow convention, on execution the first step also writes this plan to `~/src/homelab/docs/plans/` as the durable artifact (this internal file is just the plan-mode mechanism).
- Single cohesive feature → one branch/PR, babysat before merge; the encrypted secret is the CI gate as usual.
