---
status: planned
last_modified: 2026-07-03
summary: "Design study for converging finance.burntbytes.com (server-rendered encrypted-YAML dashboard) and ladder.burntbytes.com (local-first React SPA) into one finance umbrella WITHOUT breaking either data model — recommends shared-nav-now, path-based-host-later, no forced SPA merge"
---

# Finance umbrella: converging the dashboard and the ladder workbench

## Context

Two personal-finance web properties exist, built on **deliberately opposite data models**. The goal is to make them feel like one product (shared nav, identity, auth) *without* dissolving the property that makes each one correct.

### App 1 — `finance.burntbytes.com` (the dashboard)
- **What**: a static 4-page site — Balance Sheet · Cash Flow · Real Estate (STR) · Retirement Runway. See [`2026-06-18-finance-dashboard-multipage.md`](2026-06-18-finance-dashboard-multipage.md).
- **Data model**: **server-side render of operator-mounted encrypted YAML.** The renderers (`report_html.py`, `cashflow.py`, `realestate.py`, `runway.py` + `webcommon.py`) run once at container startup, reading a SOPS-encrypted Secret (`finance-dashboard-data`, mounted read-only at `/data`) and emitting flat HTML to an `emptyDir`. Source: `~/src/homelab/images/finance-dashboard/` (image build context) and the canonical `~/src/utility/portfolio/` (not a git repo). "Interactive" = in-browser JS only (Chart.js vendored at build time). **No backend at request time, no runtime egress** — the pod's CiliumNetworkPolicy allows egress to DNS only.
- **The load-bearing property**: George's *actual* net-worth numbers live encrypted-at-rest in a k8s Secret, are rendered server-side, and are **never under the browser's control** as editable state. The browser receives already-rendered HTML.
- **Deploy**: `apps/base/finance-dashboard/` + `apps/production/finance-dashboard/`, image `ghcr.io/gjcourt/finance-dashboard:latest`. LAN-only via the Cilium gateway (`app-gateway-production`, `*.burntbytes.com` wildcard TLS + wildcard DNS to the gateway LAN IP). **Currently not Authelia-gated** (LAN-only is the perimeter).

### App 2 — `ladder.burntbytes.com` (the workbench)
- **What**: a Vite + React + TS + Tailwind SPA, "privacy-first personal-finance workbench." Module-registry shell (`src/App.tsx`, the `MODULES` array). Live modules: **Bond Ladder** and **Fundamentals / EDGAR 10-K analysis**; planned: **FI / Scenario** and **Asset Location**. Source: `~/src/ladder` (superset branch `feat/fundamentals-edgar`), being packaged as private repo `gjcourt/ladder` → `ghcr.io/gjcourt/ladder`.
- **Data model**: **client-side local-first.** Every user input — amounts, yields, tax rates — lives only in browser `localStorage`. No account, no backend. Export/import is a client-side JSON file the user owns. The stated promise: *your data never leaves your device.*
- **The one server piece**: a **stateless, public-data-only SEC proxy** (`server/edgarProxy.ts`) for the Fundamentals module. It is a dumb passthrough to three public SEC endpoints (ticker→CIK, companyfacts, submissions) that exists only because SEC requires a declarative `User-Agent` (a header browsers forbid) and sends no CORS headers. It sees **only tickers** (public company ids), never user data, and holds an immutable on-disk cache of public filings. This is a documented, narrowly-scoped carve-out — the bond-ladder module deliberately refuses even that.
- **Deploy target**: `ladder.burntbytes.com`, behind **Authelia `one_factor`**. (Being deployed standalone right now; no homelab app dir exists yet — this plan treats "ladder live standalone" as a near-term starting state, not a precondition.)

### The two privacy postures side by side
| | dashboard | ladder |
|---|---|---|
| Sensitive data | George's real net worth / positions / real estate | User-entered model inputs (amounts, yields, tax rates) |
| Where it lives | SOPS-encrypted k8s Secret, server-side | Browser `localStorage`, client-side |
| Who can edit it | operator only (`sops` + commit + rollout) | the browser user |
| Rendered by | server (Python, at startup) | client (React, at runtime) |
| Backend at request time | none | none (except the tickers-only SEC proxy) |
| Egress | DNS only | SEC proxy → `www.sec.gov` / `data.sec.gov` only |

---

## The crux: these two data models must NOT be naively merged

A convergence is only honest if it preserves **both** of these simultaneously:

1. **The dashboard's operator-data model** — real net-worth numbers stay encrypted-at-rest, server-rendered, and out of the browser's editable state.
2. **The ladder's local-first no-transmit guarantee** — user-entered inputs never leave the device, and the *only* egress is a tickers-only public-data proxy.

Two tempting merges break one property each, and must be treated as **anti-goals**:

- **Anti-goal A — "put the balance sheet into ladder."** Porting the dashboard pages into the React SPA means George's real positions become client-side `localStorage`/JS state. That (a) drops the encrypted-at-rest, operator-only edit model, and (b) forces real net-worth data into the very layer ladder promises holds *only* the user's own model inputs. It also can't work without *some* server delivering those numbers to the browser — which is a backend under ladder.
- **Anti-goal B — "put a backend under ladder."** Adding an authenticated data endpoint so ladder can read the real numbers server-side breaks the "no backend, data never leaves the device" promise that is ladder's entire reason for existing. The tickers-only proxy is the *maximum* server surface ladder's charter tolerates; a personal-financial-data endpoint is categorically different.

**Conclusion up front**: the dashboard and ladder are two different *kinds* of app — a server-rendered **read-only view of operator truth** vs. a client-side **what-if modeling sandbox**. They should be unified at the **presentation and navigation layer**, and kept separate at the **data layer**. The recommendation below (a phased path ending at one host, two backends) is built entirely around that line.

---

## Goal & principles

**Goal**: one coherent "finance umbrella" — shared nav, shared visual identity, one mental model, consistent auth — spanning the dashboard and the workbench.

**Principles** (in priority order):
1. **Never co-mingle data classes.** Operator net-worth data and user-entered model inputs never share a store, a process's editable state, or a transmission path. Public market data is a third, freely-movable class.
2. **Preserve encrypted-at-rest for operator truth.** Real numbers stay in the SOPS Secret, rendered server-side. No path puts them into `localStorage` or client JS state.
3. **Preserve local-first for the workbench.** Ladder keeps no backend for user data; egress stays limited to the tickers-only SEC proxy.
4. **Unify at the seams, not the core.** Share the cheap, safe things — header/nav, CSS tokens, host, auth posture. Do not share data stores or rendering engines.
5. **Every step reversible.** Start from today's two-subdomain state; each phase is independently shippable and independently revertible.
6. **Match the existing homelab posture** — LAN-only + gateway wildcard TLS/DNS, Authelia for the gate, GitOps via Flux, SOPS for secrets.

---

## Options analysis

### Option A — Shared-nav, separate apps (two subdomains, common header)
Keep both apps exactly where they are (`finance.burntbytes.com`, `ladder.burntbytes.com`). Add a **shared header/nav fragment** and a **shared CSS token set** so they look and feel like one product. The nav is a small static include (a strip with "Dashboard / Workbench" links + product wordmark) rendered into both: the dashboard bakes it via `webcommon.page()`, ladder renders it in its `App.tsx` shell. Cross-links are absolute (`https://finance…` ↔ `https://ladder…`).

- **Data model merge**: none. Each app keeps its own model untouched. Both privacy properties trivially preserved.
- **Effort**: low. A shared nav partial + a shared `tokens.css` (colors, spacing, type scale) copied/vendored into both build contexts. No infra change.
- **Migration path**: purely additive; ship the nav to each app independently. Fully reversible (delete the partial).
- **Trade-offs**: two hostnames means two Authelia decisions, a full-page navigation (not in-app) when crossing apps, and drift risk between the two copies of the nav/tokens unless a shared source is designated. "One product" is a visual illusion, not a structural fact — acceptable for a personal tool.

### Option B — Path-based single host (`finance.burntbytes.com`, gateway path routing)
Serve both under **one hostname** via Cilium Gateway `HTTPRoute` path matching: `/` (and `/cashflow`, `/realestate`, `/runway`) → the dashboard backend; `/workbench/*` → the ladder backend (SPA), with `/edgar/*` (or `/workbench/api/*`) → the SEC proxy. Two pods, two data models, **one URL and one Authelia decision**.

- **Data model merge**: still none — two backends behind two path prefixes. The browser holds ladder's `localStorage` under the `finance.burntbytes.com` origin; the dashboard still ships pre-rendered HTML from the Python pod. **Critical caveat**: because both now share one **origin**, ladder's `localStorage` and the dashboard's pages are same-origin. The dashboard ships no script that reads `localStorage` and has no user data in JS, so there's no *actual* leak — but the *isolation argument* weakens from "different origins, browser-enforced" to "different paths, same origin, isolation by convention." Worth stating explicitly; a strict per-path CSP restores most of the guarantee.
- **Effort**: medium. Requires ladder to build under a non-root `base` path (`/workbench/`) so its asset URLs and router resolve correctly; an `HTTPRoute` with ordered path rules; the SEC proxy exposed under a path with its egress netpol; Authelia rule on the one host.
- **Migration path**: stand it up as a *third* route while both subdomains still work, cut over, then retire the ladder subdomain. Reversible by removing the path rules.
- **Trade-offs**: SPA-under-subpath friction (base path, deep-link rewrites, 404→index fallback for client routing). Same-origin caveat above. But genuinely "one URL" and one auth surface — the strongest "feels like one product" for the least data-model risk.

### Option C — Full migration into the React SPA (port the 4 Python pages into ladder modules)
Turn the dashboard's four pages into ladder React modules (add them to the `MODULES` registry), retire the Python renderer, serve everything from ladder.

- **This is the option that forces the crux.** The dashboard's numbers come from an **encrypted operator Secret**; a client-side React module can only display them if they arrive in the browser somehow. Three sub-variants, all problematic:
  - **C1 — build-time injection**: bake the numbers into the JS bundle at image build. Then the *image* contains plaintext net worth (leaks via any image pull / registry read), and every data edit forces an image rebuild — losing the current "edit = re-encrypt Secret + rollout, no rebuild" property. **Reject.**
  - **C2 — read-only authenticated data endpoint**: a backend serves the decrypted numbers to the authenticated browser as JSON. This is **Anti-goal B** — a backend for personal financial data under ladder — and it puts real numbers into client JS state (**Anti-goal A**). **Reject** on charter grounds.
  - **C3 — manual import**: the user pastes/imports the numbers into ladder `localStorage` by hand. This *technically* keeps local-first, but now George's real net worth lives in `localStorage` (dropping encrypted-at-rest + operator-only), and it's a manual, drift-prone duplicate of the Secret. Contradicts the whole reason the dashboard is server-rendered. **Reject** for the real-numbers pages.
- **Effort**: highest (port four renderers + their client JS to React/TS, rebuild the data pipeline, resolve the above). Highest risk to both privacy properties.
- **Where a *narrow* slice of C is actually right**: the **interactive, input-driven** pages — Runway and the STR pro-forma — are already "what-if sandboxes" whose inputs are model assumptions, not operator truth. Those belong to ladder's model (they're basically the planned FI/Scenario surface). Porting *those* is coherent. The **Balance Sheet and Cash Flow** (real positions/real comp) are exactly what must stay server-rendered. So "full migration" is wrong, but "migrate the sandbox pages, keep the truth pages server-rendered" is a real insight that the roadmap below uses.

### Option D — Umbrella shell + micro-frontends (thin shell embeds both sub-apps)
A thin shell app owns the chrome (nav, auth, routing) and embeds the dashboard and ladder as sub-apps (iframes, Module Federation, or web components).

- **Data model merge**: none by design — sub-apps stay isolated (iframes give a real security boundary; ladder's `localStorage` stays partitioned per frame origin). Preserves both properties cleanly.
- **Effort**: high, and mostly *accidental* complexity for a two-app personal tool — a shell framework, cross-frame messaging, auth token plumbing, iframe sizing/UX papercuts. Module Federation adds a build-coupling and shared-dependency headache; iframes add postMessage and styling seams.
- **Migration path**: build the shell, embed both, migrate nav into it. Reversible but heavy.
- **Trade-offs**: the "correct big-org" answer, but over-engineered here. Buys isolation we already get for free from two origins (Option A) or can get from CSP + subpath (Option B). Not worth the framework tax at this scale. **Documented for completeness; not recommended.**

### Options summary
| Option | One product feel | Data-model risk | Effort | One auth surface | Reversible |
|---|---|---|---|---|---|
| A — shared nav, 2 subdomains | medium (visual) | none | low | no (2 hosts) | yes |
| B — path-based, 1 host | high | low (same-origin caveat) | medium | yes | yes |
| C — full SPA migration | high | **high** (breaks a property) | high | yes | hard |
| D — micro-frontend shell | high | none | high | yes | yes |

---

## Recommendation

**Adopt A now, evolve to B, and cherry-pick the *sandbox-only* slice of C over time. Never do C for the real-numbers pages; do not build D.**

Concretely: ship a **shared nav + shared design tokens (Option A)** as the immediate, low-risk win that makes the two apps feel like one product this week. Then, when the seams of two-hostnames chafe, collapse to **one host with gateway path routing (Option B)** — `finance.burntbytes.com/` = dashboard, `finance.burntbytes.com/workbench/*` = ladder — giving one URL and one Authelia decision while keeping **two backends and two data models**. Independently, migrate only the **input-driven sandbox pages** (Runway, STR pro-forma) into ladder modules where they naturally belong (the planned FI/Scenario surface), while the **Balance Sheet and Cash Flow stay server-rendered from the encrypted Secret forever**.

**One-paragraph rationale**: The dashboard and ladder are not two versions of one app; they are two *kinds* of app — a server-rendered read-only view of operator truth, and a client-side what-if sandbox — and their opposite data models are each *correct for their purpose*, not an accident to be reconciled away. The only honest convergence unifies the presentation and navigation layer while keeping the data layer split, so real net-worth numbers stay encrypted-at-rest and server-rendered and the workbench stays local-first. A/B do exactly that and are both reversible; C (for the truth pages) and D each pay a large cost to erase a boundary that is actually load-bearing.

---

## Data & privacy architecture (recommended path)

Three data classes, kept strictly separate:

| Class | Examples | Home | At rest | Transmission | Editable by |
|---|---|---|---|---|---|
| **Operator truth** | net worth, positions, real estate, comp | SOPS Secret `finance-dashboard-data`, mounted RO | SOPS/age encrypted in git + k8s Secret | never leaves server as data; browser gets **rendered HTML only** | operator (`sops` + commit + rollout) |
| **Workbench inputs** | ladder amounts, yields, tax rates, scenario assumptions | browser `localStorage` (origin-scoped) | plaintext in the user's browser profile only | **never transmitted**; export = local file | the browser user |
| **Public market data** | Treasury par curve, SEC 10-K filings | ephemeral (fetch/cache); SEC cache on the proxy | n/a (public) | freely fetched; carries no private inputs | n/a |

**Invariant to enforce and test**: no code path moves data from the *operator truth* column into the *workbench inputs* column, or vice-versa. The dashboard pod never serves editable JSON of the real numbers; the ladder bundle never receives them.

**SEC proxy egress isolation** (unchanged from ladder's standalone design, restated for the merged host):
- The proxy is its own deployment/container with its **own CiliumNetworkPolicy**: egress allowed only to DNS + `www.sec.gov`/`data.sec.gov` (443); ingress only from the gateway on its path. It is the *only* pod in the umbrella with any non-DNS egress.
- It carries the SEC-required `User-Agent`, a polite rate limit, and an immutable public-filings cache. It receives **only tickers**.
- The dashboard pod and the ladder static-serving pod keep **DNS-only egress** (dashboard's existing netpol is already this; ladder's HTML/asset server needs no egress at all).

**Same-origin note for Option B**: once both live under `finance.burntbytes.com`, ladder's `localStorage` and the dashboard's pages share an origin. There is no actual data flow between them (the dashboard ships no script that reads `localStorage`, and holds no user data in JS), but to keep the isolation *enforced* rather than *by convention*, apply a **strict per-path CSP** (dashboard: `default-src 'self'`, no `connect-src` beyond self; ladder/workbench: `connect-src` limited to the SEC-proxy path) so neither surface can script the other's data or exfiltrate. This restores most of the browser-enforced boundary that separate origins gave for free.

---

## Auth, DNS & routing

**DNS** (no change to the split-horizon model): everything stays on the `*.burntbytes.com` wildcard that AdGuard rewrites to the production gateway LAN IP, with wildcard TLS terminated at the gateway. **LAN-only** (not tunneled) is the outer perimeter for all of it.
- **Option A**: two existing hostnames, both already covered by the wildcard — no DNS/cert work.
- **Option B**: keep `finance.burntbytes.com`; retire `ladder.burntbytes.com` after cutover (or leave it as a redirect). Still zero per-host DNS/cert changes (wildcard covers it).

**Auth (Authelia via the global Envoy `ext_authz` filter)**: the gateway runs a global `ext_authz` filter with `default_policy: bypass`; apps opt in with a `one_factor` rule (per `docs/architecture/gateway-auth.md`). Recommended posture:
- Gate the **whole umbrella** with `one_factor` — ladder is already slated for it, and the dashboard's real net-worth numbers deserve at least the same gate (today it relies on LAN-only alone; adding `one_factor` is a strict improvement and is nearly free).
  - **Option A**: two rules — `domain: finance.burntbytes.com` and `domain: ladder.burntbytes.com`, both `one_factor`.
  - **Option B**: one rule — `domain: finance.burntbytes.com`, `policy: one_factor` — covers dashboard, workbench, and the SEC-proxy path in a single decision.
- The SEC proxy sits *behind* the same gate (its path is under the protected host), so unauthenticated users can't even reach the ticker passthrough. Good.

**Gateway routing (Option B specifics)**: one set of `HTTPRoute`s on `finance.burntbytes.com` (http→https redirect + https), with **ordered path rules**:
- `PathPrefix: /workbench/api` (or `/edgar`) → `edgar-proxy` service (SEC proxy).
- `PathPrefix: /workbench` → `ladder` service (static SPA; needs SPA fallback so deep links serve `index.html`).
- `PathPrefix: /` → `finance-dashboard` service (default/catch-all last).
Ladder must be built with Vite `base: '/workbench/'` and a router basename so assets and client routes resolve under the subpath. Each backend keeps its own namespace, service, and netpol.

---

## Phased roadmap

Starting state: **ladder live standalone at `ladder.burntbytes.com`; `finance.burntbytes.com` = the Python dashboard.** Each phase is independently shippable and reversible.

**Phase 0 — Land ladder as a first-class homelab app (prereq, in flight).** Add `apps/base/ladder/` (+ production overlay): deployment for the static SPA, a separate deployment for `edgar-proxy` with its scoped egress netpol, service(s), `HTTPRoute` on `ladder.burntbytes.com`, ghcr pull secret, Authelia `one_factor` rule. Mirror the finance-dashboard app's security posture (non-root, RO rootfs, drop caps, `automountServiceAccountToken: false`). *Reversible*: it's a new app dir.

**Phase 1 — Shared identity (Option A), the immediate win.** Define a small shared design-token set (colors/type/spacing) and a shared nav fragment ("Dashboard | Workbench" + wordmark). Bake it into the dashboard via `webcommon.page()` and into ladder's `App.tsx` shell; cross-link absolutely. Designate **one source of truth** for the tokens/nav (e.g., a tiny file vendored into both build contexts) to prevent drift. Add Authelia `one_factor` to the dashboard host to match ladder. *Ship value now; fully reversible.*

**Phase 2 — Collapse to one host (Option B).** Build ladder under `base: '/workbench/'`; add path-routed `HTTPRoute`s on `finance.burntbytes.com` (proxy path, `/workbench`, dashboard catch-all); add strict per-path CSP; move the Authelia rule to the single host. Stand it up alongside the existing subdomains, verify, then cut over and redirect (or retire) `ladder.burntbytes.com`. *Reversible by removing the path rules and re-pointing.*

**Phase 3 — Migrate the sandbox pages only (narrow slice of C).** Fold **Runway** and the **STR pro-forma** into ladder as modules (they are input-driven what-ifs = the planned FI/Scenario surface). **Leave Balance Sheet and Cash Flow server-rendered from the encrypted Secret** — they are operator truth and stay in the Python dashboard. Result: the umbrella cleanly splits into "truth view" (server-rendered, `/`) and "modeling sandbox" (local-first, `/workbench`), which is the honest end-state. *Optional, incremental, per-page reversible.*

**Explicitly out of scope / rejected**: putting real net-worth numbers into `localStorage` or the JS bundle (Anti-goal A); any authenticated personal-data backend under ladder (Anti-goal B); a micro-frontend shell framework (Option D).

---

## Owner decisions (resolved 2026-07-03)

1. **One URL vs. two subdomains → DECIDED: Option A (two subdomains + shared nav) is the durable resting state.** Not collapsing to a single host — the SPA-under-subpath friction isn't worth "one bookmark" for a personal tool. Only concrete follow-up: a shared nav header across the two sites (when desired).
2. **Gate the dashboard → DONE.** `finance.burntbytes.com` given Authelia `one_factor` (PR #1034, merged 2026-07-03), matching the other internal apps. LAN still bypasses; off-LAN requires login.
3. **Runway/STR into ladder → DEFERRED.** Keep all four pages canonical in the Python dashboard for now. Revisit migrating the input-driven Runway/STR pages into ladder only if ladder proves itself as the daily-driver workbench — it's an investment, not a fix.
4. (Minor) **Retire/redirect `ladder.burntbytes.com`** — moot given decision 1 (two subdomains stay); ladder keeps its own host.

**Net:** light touch — two gated subdomains with shared nav; no data-model merge; TS migration of Runway/STR deferred.
