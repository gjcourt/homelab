---
status: in-progress
last_modified: 2026-11-04
summary: "AccurateRip-check every CD at import time, record results in the rip ledger, archive EAC logs, and generate a bad-disc tally from the ledger"
---

# AccurateRip at import time — plan

**Scope:** `hosts/winpc-5600x/media-pipeline/`, `hosts/winpc-5600x/rip-tools/eac/`, and the private
`media-library` repo (tally).

## Problem

On 2026-11-04 a library-wide AccurateRip scan of the 280 disc folders on hestia found 7 discs where
1-4 tracks matched nothing while the other tracks matched at confidence 5-200. Those are read errors
from scratched discs. All 7 had already gone back to SFPL, so fixing them now means borrowing them
again. The scan had to be run by hand, from a Mac, after the fact, and EAC had kept no logs.

## Decisions (George, 2026-11-04: "do 1-4")

1. **Verify on import.** `ar-verify.ps1` checks each disc before staging, in dry runs too.
   Results go to the rip ledger as `AR_CHECK` rows, the system of record.
2. **Flag, don't block.** A `BAD_TRACKS` disc still imports; the run ends with a re-rip block
   while the disc is still in hand.
3. **Tally in version control.** `media-library` gets a bad-disc tally generated from the ledger
   (plus `unrippable-discs.tsv` for video), refreshed by PR. Daedalus does not commit on its own:
   every change goes through a PR. Seeded with the 2026-11-04 scan results.
4. **EAC logs.** `AutoSaveStatus` and `CreateEnglishLogFile` on; the import archives logs to
   hestia by content hash.

## Not doing

- **Offset correction.** Checks run at shift 0 only. EAC already applies the drive offset (#1800),
  and a shift search found only confidence-1 coincidences.
- **Blocking imports on a failed check.** A flawed track beats a missing album, and
  accuraterip.com being down must never stop a load.

## Status

- [x] `ar-verify.ps1`, cross-checked against the Python checker on 15 discs (identical per-track
      confidences) and on a deliberately altered track (`BAD_TRACKS`, neighbours intact).
- [x] Wired into `import-music.ps1`; dry run against `C:\Rips` with a scratch ledger.
- [x] EAC registry set on daedalus and in `EAC.reg`.
- [ ] Confirm where EAC writes the log, after the next real rip.
- [ ] `media-library` tally + generator, seeded from the scan.
