# Media Library Build-Out

**Status:** active — the single source of truth for building the household media
libraries (**Jellyfin** for video, **Navidrome** for music) from a mix of
borrowed, owned, and archived sources, deduplicated against what already exists
so nothing is re-acquired.

## Why this doc

Three efforts kept overlapping: the franchise wishlist
([jellyfin-library-picks.md](jellyfin-library-picks.md)), the disc-ripping
pipeline (lab repo project `03-026-rip-owned-4k-uhd-blurays-to-nas-jellyfin`),
and the archive assimilation (life repo `data-archive/assimilation-plan.md`).
This ties them into one tracked plan with an inventory baseline, an acquisition
strategy, and per-title status.

## Golden rule: reconcile before acquiring

Never borrow, buy, or re-rip a title without checking it against **hestia** (the
Jellyfin library + the archive) **and Mighty Joe** (the movie drive). MJ is
mid-recovery and currently unreadable — treat every "missing" below as "not on
hestia," not "confirmed absent." **Re-run the reconciliation once MJ mounts
cleanly before spending money or making a library trip.**

## Pipelines (the "how")

| Source | Pipeline |
|---|---|
| Owned / borrowed **4K UHD** disc | lab `03-026` — LibreDrive-flashed LG drive → MakeMKV remux → Jellyfin |
| Owned / borrowed **standard Blu-ray** | any Blu-ray drive → MakeMKV (no UHD flash needed) → Jellyfin |
| **Archive media** (already digital) | assimilation plan → `family/media/{video,music}` → Jellyfin / Navidrome |
| **CDs** | already ripped → Navidrome (complete) |

Standard Blu-ray is the *easy* path — only 4K UHD needs the flashed drive and
AACS 2.0 handling. TV box sets and every SFPL disc are standard Blu-ray.

---

## Inventory baseline (reconciled 2026-07-08 vs hestia + MJ)

**On hand: 12 / 104 franchise films** (all on hestia). Archive held only home
videos. `tv-shows` dataset empty. **Mighty Joe (~60% recovered) unreadable —
contents UNKNOWN.**

## Track 1 — Movies (6 franchises, 104 films)

| Franchise | Own (hestia) | SFPL Blu-ray | To acquire (pending MJ) |
|---|---|---|---|
| Star Wars | **6/6 ✅** | 6/6 | — |
| Indiana Jones | **5/5 ✅** | 5/5 | — |
| Lord of the Rings | 0/6 | 6/6 (Extended Eds) | 6 |
| James Bond | 0/25 | 24/25 | 25 (24 SFPL + *From Russia with Love* via LINK+) |
| Pixar | 0/28 | 27/28 | 28 (27 SFPL + *Luca* via LINK+) |
| Marvel (MCU) | 1/34 (Iron Man) | 33/34 +1 likely | 33 |
| **Total** | **12/104** | **101/104** | **92 films** |

**Key facts:**
- **SFPL has 101/104 on Blu-ray but ZERO 4K UHD.** 1080p Blu-ray is the ceiling
  there (still lossless audio + high bitrate; LOTR/Hobbit circulate as Extended
  Editions). For a 4K/HDR master you must **buy** the disc — the library can't help.
- 3 to double-check via LINK+ (SFPL regional sharing): *From Russia with Love*,
  *Luca*, *The Marvels*.
- Batch holds for the popular "all copies in use" titles (Toy Story 2/3,
  Octopussy, Casino Royale, Iron Man 3, Civil War, Incredible Hulk).
- **Full per-film table with call numbers + availability:**
  [sfpl-bluray-index.md](sfpl-bluray-index.md).

## Track 2 — TV

| Show | Own? | Best available | Acquire via | Status |
|---|---|---|---|---|
| Sex and the City | **Own (Blu-ray)** | 1080p BR | — | ready to rip |
| Gilmore Girls | **Own (Blu-ray)** | 1080p BR | — | ready to rip |
| Seinfeld | no | DVD (no BR) | SFPL / buy | to acquire |
| Friends | no | Blu-ray remaster | SFPL / buy | to acquire |
| The Wire | no | Blu-ray HD remaster | SFPL / buy | to acquire |
| Planet Earth (I/II) | no | **4K UHD reference** | **buy** (SFPL no 4K) | to acquire |
| Derry Girls | no | DVD / UK import | SFPL / buy | to acquire |
| Luther | no | Blu-ray | SFPL / buy | to acquire |
| Sherlock | no | Blu-ray | SFPL / buy | to acquire |

The two owned box sets rip **now**, independent of MJ. Planet Earth II in 4K is
the reference demo disc — worth buying the UHD for the tuned home-theater setup.

## Track 3 — Music

- **CDs:** all owned CDs are already ripped and in **Navidrome** — done.
- **Archive:** the assimilation dedup found **920 unique tracks / 36 artists**
  (Sublime, Jack Johnson, State Radio…) routing to `family/media/music/`. Task:
  **fold these into Navidrome, deduped against the existing library** — handled
  by assimilation Phase 3, not a separate ripping effort.

### Optional sub-track — music films
SFPL confirmed these concert/music docs on Blu-ray (curation batch): *Stop
Making Sense*, *The Last Waltz*, *Gimme Shelter*, *Buena Vista Social Club*,
*Jazz on a Summer's Day*, *Long Strange Trip*. Fold in if wanted.

---

## Next actions

1. **Rip the owned TV Blu-rays** (Sex and the City, Gilmore Girls) — no
   dependencies; validates the standard-Blu-ray path end to end.
2. **Wait for MJ recovery → re-reconcile** → finalize the movie to-borrow list
   (MJ likely covers a chunk of the 92 "missing").
3. **Batch SFPL holds** for the confirmed gaps: place holds on the in-use
   titles, grab the available ones in one AV Center run.
4. **Buy 4K** only for the few reference titles where HDR matters (Planet Earth II).
5. **Fold the archive's 920 tracks into Navidrome** (assimilation Phase 3).

## Related

- Franchise wishlist + PTP fallback: [jellyfin-library-picks.md](jellyfin-library-picks.md)
- SFPL availability (104 films): [sfpl-bluray-index.md](sfpl-bluray-index.md)
- Rip pipeline: lab `03-026-rip-owned-4k-uhd-blurays-to-nas-jellyfin`
- Media routing: life `data-archive/assimilation-plan.md`
