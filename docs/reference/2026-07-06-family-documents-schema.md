---
status: Stable
last_modified: 2026-07-06
summary: "Sub-folder schema for family/documents on hestia — the hybrid (category-first-weighted) taxonomy that the machine-recovery document consolidation sorts into. Grounded in a live scan of the existing family/documents tree plus the Dropbox/Toshiba/gauss/hackintosh archives. Companion to 2026-07-06-hestia-data-organization.md, which defines the top-level buckets; this defines the sub-folders within family/documents."
---

# family/documents — sub-folder schema

Companion to [`docs/plans/2026-07-06-hestia-data-organization.md`](../plans/2026-07-06-hestia-data-organization.md).
That plan defines the three top-level buckets (`family/` · `media/` · `archive/`) and
says documents live in `family/documents`. **This doc defines how `family/documents`
is organized internally**, so the document-consolidation pass (merging docs off the
Toshiba, gauss, Dropbox, hackintosh, and Windows PC) has a defined landing place.

## Model: hybrid, category-weighted

Category-first for shared/household documents (the bulk), with a light per-person tail
for genuinely personal docs. This matches the reality: most documents are joint
household paperwork (finance is a whole bucket — George runs a micro family office);
only a minority are person-specific. Grounded in a 2026-07-06 scan — the existing
`family/documents` was *already* category-first (`real-estate/YYYY`, `finance`,
`receipts`, `estate-plan`, `auto/<vehicle>`, `manuals/<numbered>`); this formalizes and
extends it.

## The schema

```
family/documents/
├── finance/                         [largest]
│   ├── taxes/<year>/                returns + W-2/1099/supporting  (absorbs Dropbox "Tax Returns" 2009–2015)
│   ├── statements/<institution>/    Fidelity, Coinbase, banks
│   ├── retirement/                  TIRA/IRA/401k  (George-TIRA, Mara-TIRA)
│   ├── equity-comp/                 83(b), RSUs, comp statements
│   └── insurance/                   life, umbrella (non-auto)
│
├── real-estate/
│   ├── owned/<property>/<year>/      properties you own — e.g. 3545-washington/{2017,2022,2024}
│   └── prospects/<property>/         browsed / comps, never bought — 1923-pierce, 2955-sacramento, 835-dry-creek
│
├── auto/<vehicle>/                  lexus-nx-450h, mazda-cx-5
│   ├── <year>/
│   └── insurance/
│
├── medical/<year>/                  year-first (matches existing data); person/provider subfolders optional; unclear docs → _review/
│
├── receipts/<year>/<type>/          2024/, "01 Medical", …
│
├── manuals/<NN.NN Category>/        keep the numbered scheme: 01.00 Appliances … 01.07 Bicycles
│                                    (+ 01.08 Motorcycle for the XJ550 service manual)
│
├── career/                          per-person
│   ├── george/                      resumes, offers, comp letters
│   └── mara/
│
├── legal/                           estate-plan, Trust, wills, IDs/passports, contracts
│
├── reference/                       misc guides not tied to a manual
│
├── george/                          personal tail — anything genuinely personal, no shared category
└── mara/                            (kept light by design)
```

## Design decisions (recommended answers baked in above)

1. **real-estate → property-first, split owned vs prospects** (`owned/<property>/<year>`
   and `prospects/<property>/`). Rationale: `3545-washington` is *owned* and its docs
   span 2017/2022/2024 — property-first unifies its history; but most 2022 entries
   (`1923-pierce`, `2955-sacramento`, `3107-warm-springs`, `835-dry-creek`) are one-off
   house-hunting comps that shouldn't get deep owned-style folders. The owned/prospects
   split keeps both clean.
2. **`legal/` holds estate-plan + Trust + IDs/contracts**, pulled out of `finance/`.
   Rationale: estate/legal instruments are referenced independently of tax-year finance.
3. **`career/` is its own category** (per-person), not folded into `george/`/`mara/`.
   Rationale: keeps resumes/offers/comp discoverable together across both people.
4. **Dropped `correspondence/`** — the scan showed the people-named Dropbox folders
   (Terri, Howard, Ofer, Andreas) are not letters: they're event photos, music, and app
   source code (see routing below). No real correspondence corpus exists.
5. **medical stays year-first** (`medical/<year>/`), not person-first. Rationale: the
   existing data is year-organized and doesn't encode which person each doc belongs to
   ("patricia ross" is a provider, not a person). Person/provider subfolders are
   optional; docs with an unclear owner go to `_review/` rather than forcing a guess.

## Non-document routing (so `family/documents` stays clean)

The consolidation must **not** dump non-documents into `family/documents`. From the scan,
these Dropbox/machine folders route elsewhere:

| Source folder(s) | What it actually is | Home |
|---|---|---|
| Camera Uploads, Nikon Transfer, Photos, 251 27th Avenue, Dresser and Nightstand, Public, XJ550 photos | your photos | → Immich (`family/images/photos/<person>/YYYY/MM`) |
| **Terri** (ex-partner's photos), and any ambiguous personal photo set | needs a human keep/trash call | → **`_review/` hold — NOT auto-imported**; owner reviews, then keeps-elsewhere or trashes |

> **Review-hold rule:** the consolidation must **not** auto-import photo sets whose
> ownership or keep/trash status is unclear (an ex-partner's photos, someone else's
> event dump, unsorted junk). These land in a `family/images/_review/` hold for a human
> decision, never straight into a person's timeline. Only clearly-yours photos
> auto-import.
| Howard, classical albums (Gilels/Moravec), music/ | music/audio | → `media/music` (or `family/audio` for own recordings, e.g. the 2006 piano tracks) |
| books | ebooks / audiobooks | → `family/literature` |
| projects, projects_archive, config, disqus, XML, Ofer-George, Andreas | source code / dev config | → `family/projects` (or `archive/` if dead) |
| Memorang, bullet_point_gaming | past **ventures** — marketing/design/business assets | → `family/projects/ventures/<name>` |
| XJ550 service manual PDF | motorcycle manual | → `manuals/01.08 Motorcycle` |

## Conventions

- **Years are `YYYY`** folders; **properties/vehicles/people are lowercase-kebab**
  (`3545-washington`, `lexus-nx-450h`).
- **Manuals keep the numbered index** (`NN.NN Category`) — it sorts predictably and is
  already established.
- Ownership follows the plan's per-person model where a person subfolder exists
  (`medical/george`, `career/mara`): owned by that person's uid, world-readable.
- The consolidation dedupes on **sha256** before landing anything (same doc from
  multiple machines lands once), per the parent plan's §6. **Collision rule:** if a
  candidate's sha256 already exists at the destination → skip (identical). If a file of
  the **same name but different content** exists → land it in `_review/` for a human to
  reconcile; never silently overwrite.

## Basis

Grounded in a 2026-07-06 live scan of `family/documents` plus the `dropbox-cloud`,
`gauss`, `oldmac-unibody`, `winpc-5800x`, and hackintosh (`Poseidon`) archives on hestia.
