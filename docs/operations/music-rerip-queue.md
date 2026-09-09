---
status: active
last_modified: 2026-09-02
summary: "CDs that must be re-ripped or re-tagged, derived from a tag-based completeness audit of the whole music library"
---

# Music re-rip / re-tag queue

Derived from a completeness audit of **all 3,328 files / 207 albums** in
`hestia:/mnt/main/family/media/music`, using the `TOTALDISCS` / `TOTALTRACKS`
values embedded in the files themselves (the same check now enforced by
`scripts/organize-music-rips.sh`).

**Audit result: 78 complete · 6 incomplete · 123 unverified (no totals tags).**

## 1. Re-rip required — data is gone

| Album | Missing | Release |
| :--- | :--- | :--- |
| **The Clash — London Calling** | **disc 1** (10 tracks) | barcode `887254469926`, MBID `4ea6268c-b185-45f1-8ffe-21b386cc1425` |

⚠️ **This one is confirmed permanently lost.** Disc 1 exists in no ZFS snapshot
(all ~50 checked back to July 2026), on no host, and the rip box's `C:\rips` is
empty with an empty Recycle Bin and no EAC logs. The 2×CD is owned — disc 1
needs re-ripping from the physical disc. Disc 2's nine tracks are intact; rip
disc 1 only.

Root cause and the fix are documented in `scripts/organize-music-rips.sh`.

## 2. Verify ownership, then re-rip

These declare more discs than are present. Each may be deliberate — a deluxe
edition where only one disc was ever wanted — so **confirm you own the missing
disc before queueing**.

| Album | Declared | Present | MBID |
| :--- | :--- | :--- | :--- |
| Curtis Mayfield — Superfly | 2 discs | disc 1 | `f9956f05-8875-426d-b740-db4ce700bf9c` |
| John Coltrane — A Love Supreme | 2 discs | disc 1 | `e59fe514-030a-44bd-847f-9faa962f27d8` |
| The Beatles — Revolver | 2 discs | **disc 2 — disc 1 missing** | `f68086ff-1861-4b58-93c6-f71891538faf` |
| The Beatles — Revolver (2022 stereo mix) | 5 discs | 1 disc | `85e77c9c-7de1-4fd6-89d5-b3b6cf2bb643` |

The two Revolver entries carry different MBIDs, so they are tagged as separate
releases — but they may physically be one super-deluxe box. Check the shelf
before ripping twice.

## 3. Re-tag only — no re-rip needed

| Album | Issue |
| :--- | :--- |
| Ornette Coleman — The Shape of Jazz to Come | **Disc 2 is present** (15 tracks, XLD-ripped) but was never run through Picard |

`The Shape of Jazz to Come [Disc 2]/` has no `MUSICBRAINZ_ALBUMID`, no
`DISCNUMBER`, no `TOTALDISCS`, and a blank `ARTIST` — so it cannot group with
disc 1 (12 tracks) and reads as two half-albums. Tag it in Picard against the
same release as disc 1. **Do not re-rip this.**

## 4. Re-borrow from the library

| Album | Source |
| :--- | :--- |
| *Finding Beauty* | SFPL, call number `CD JAZZ CALL` |

Ripped after the 2026-08-29 import and never transferred; not on hestia, not on
this Mac, and the rip box is empty. It is a **library** disc, not owned — if it
has been returned it must be requested again. See
`~/src/lab/03-homelab-automation/03-029-sfpl-borrow-and-rip-pipeline.md`
(status: Not Started — holds are placed manually today).

## 5. Standing gap — 123 albums unverifiable

123 of 207 albums carry **no** `TOTALDISCS`/`TOTALTRACKS` tags, so completeness
cannot be proven for them either way. They are not known-bad; they are
un-checkable. Re-tagging them in Picard would bring them under the gate. Until
then, treat "no news" as no evidence rather than as good news — that conflation
is exactly what lost London Calling disc 1.
