# SFPL availability for the 6 Jellyfin franchises (104 films)

_Checked 2026-07-08 against the SFPL BiblioCommons catalog (`sfpl.bibliocommons.com`). Source list: `docs/operations/apps/jellyfin-library-picks.md`._

## TL;DR — how much to trust the status column

- **Verification is real, not guessed.** SFPL's BiblioCommons catalog *is* scrapable through its `v2/search` endpoint — I drove a live title query for every one of the 104 films and read back the actual format labels, call numbers, and availability the catalog returned. Where a row says "Blu-ray," the catalog showed a `BLU ...` record.
- **SFPL has NO 4K UHD Blu-ray.** A format-code search for 4K/UHD returns zero results system-wide; their physical video is DVD + Blu-ray only. So the best disc you can check out for any of these is a **1080p Blu-ray** (still a lossless-audio, high-bitrate rip — well worth ripping over a stream). Every "format" cell below is Blu-ray-or-lower by definition.
- **Result: 101 of 104 confirmed on Blu-ray**, 2 are **DVD-only** (no Blu-ray found), and 1 is **Blu-ray-likely** (aggregate showed Blu-ray discs but I couldn't isolate the film's own record). Zero films were entirely absent.
- **Caveats on the status column:** (1) Availability ("Available" / "All copies in use") is a point-in-time snapshot from today and floats constantly — treat it as indicative, not a reservation. (2) The catalog is a JS single-page app; there are no stable per-item permalinks I could capture, so the "link" column is a **title search URL** that lands you on the film's record, not a deep link to the exact bib. (3) The 2 DVD-only calls mean *no Blu-ray surfaced in the film's search* — a Blu-ray could exist under an odd edition record, but I could not confirm one.

## Summary by franchise

| Franchise | On Blu-ray at SFPL | Notes |
|---|---|---|
| Star Wars | 6 / 6 | all confirmed |
| Indiana Jones | 5 / 5 | all confirmed |
| Lord of the Rings | 6 / 6 | all confirmed (several Extended Edition Blu-rays) |
| James Bond | 24 / 25 | *From Russia with Love* DVD-only |
| Pixar | 27 / 28 | *Luca* DVD-only |
| Marvel (MCU) | 33 / 34 confirmed + 1 likely | *The Marvels* Blu-ray-likely |
| **Total** | **101 / 104 confirmed Blu-ray** | +1 likely, 2 DVD-only, 0 not-found, 0 in 4K |

Format legend: **BR** = Blu-ray confirmed · **DVD-only** = only a DVD record found · **BR?** = Blu-ray likely but not isolated. No 4K UHD exists at SFPL.

---

## Star Wars — 6/6 on Blu-ray

| Film | Year | SFPL format | Availability (2026-07-08) | Find it |
|---|---|---|---|---|
| Episode I: The Phantom Menace | 1999 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Star%20Wars%20Phantom%20Menace) |
| Episode II: Attack of the Clones | 2002 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Star%20Wars%20Attack%20of%20the%20Clones) |
| Episode III: Revenge of the Sith | 2005 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Star%20Wars%20Revenge%20of%20the%20Sith) |
| Episode IV: A New Hope | 1977 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Star%20Wars%20A%20New%20Hope) |
| Episode V: The Empire Strikes Back | 1980 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Empire%20Strikes%20Back) |
| Episode VI: Return of the Jedi | 1983 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Return%20of%20the%20Jedi) |

## Indiana Jones — 5/5 on Blu-ray

| Film | Year | SFPL format | Availability | Find it |
|---|---|---|---|---|
| Raiders of the Lost Ark | 1981 | BR | 2 BR copies | [search](https://sfpl.bibliocommons.com/v2/search?query=Raiders%20of%20the%20Lost%20Ark) |
| Temple of Doom | 1984 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Indiana%20Jones%20Temple%20of%20Doom) |
| The Last Crusade | 1989 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Indiana%20Jones%20Last%20Crusade) |
| Kingdom of the Crystal Skull | 2008 | BR | Available (special ed. HD) | [search](https://sfpl.bibliocommons.com/v2/search?query=Indiana%20Jones%20Kingdom%20Crystal%20Skull) |
| Dial of Destiny | 2023 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Indiana%20Jones%20Dial%20of%20Destiny) |

## Lord of the Rings — 6/6 on Blu-ray

| Film | Year | SFPL format | Availability | Find it |
|---|---|---|---|---|
| The Fellowship of the Ring | 2001 | BR | 3 BR copies | [search](https://sfpl.bibliocommons.com/v2/search?query=Lord%20of%20the%20Rings%20Fellowship) |
| The Two Towers | 2002 | BR | Extended Ed. + HD, Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Lord%20of%20the%20Rings%20Two%20Towers) |
| The Return of the King | 2003 | BR | 2 BR copies | [search](https://sfpl.bibliocommons.com/v2/search?query=Lord%20of%20the%20Rings%20Return%20of%20the%20King) |
| The Hobbit: An Unexpected Journey | 2012 | BR | Extended Ed., Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Hobbit%20An%20Unexpected%20Journey) |
| The Hobbit: The Desolation of Smaug | 2013 | BR | Extended Ed., Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Hobbit%20Desolation%20of%20Smaug) |
| The Hobbit: The Battle of the Five Armies | 2014 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Hobbit%20Battle%20of%20the%20Five%20Armies) |

## James Bond — 24/25 on Blu-ray (1 DVD-only)

| Film | Year | SFPL format | Availability | Find it |
|---|---|---|---|---|
| Dr. No | 1962 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Dr%20No%20James%20Bond) |
| From Russia with Love | 1963 | **DVD-only** | DVD Available; no BR found | [search](https://sfpl.bibliocommons.com/v2/search?query=From%20Russia%20with%20Love%20film) |
| Goldfinger | 1964 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Goldfinger%20Bond) |
| Thunderball | 1965 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Thunderball%20Bond) |
| You Only Live Twice | 1967 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=You%20Only%20Live%20Twice%20Bond) |
| On Her Majesty's Secret Service | 1969 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=On%20Her%20Majestys%20Secret%20Service) |
| Diamonds Are Forever | 1971 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Diamonds%20Are%20Forever%20Bond) |
| Live and Let Die | 1973 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Live%20and%20Let%20Die%20Bond) |
| The Man with the Golden Gun | 1974 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Man%20with%20the%20Golden%20Gun%20Bond) |
| The Spy Who Loved Me | 1977 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Spy%20Who%20Loved%20Me%20Bond) |
| Moonraker | 1979 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Moonraker%20Bond) |
| For Your Eyes Only | 1981 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=For%20Your%20Eyes%20Only%20Bond) |
| Octopussy | 1983 | BR | All copies in use (3) | [search](https://sfpl.bibliocommons.com/v2/search?query=Octopussy%20Bond) |
| A View to a Kill | 1985 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=A%20View%20to%20a%20Kill%20Bond) |
| The Living Daylights | 1987 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=The%20Living%20Daylights) |
| Licence to Kill | 1989 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Licence%20to%20Kill%20Bond) |
| GoldenEye | 1995 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=GoldenEye%20Bond) |
| Tomorrow Never Dies | 1997 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Tomorrow%20Never%20Dies%20Bond) |
| The World Is Not Enough | 1999 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=The%20World%20Is%20Not%20Enough%20Bond) |
| Die Another Day | 2002 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Die%20Another%20Day%20Bond) |
| Casino Royale | 2006 | BR | 3 BR copies, all in use | [search](https://sfpl.bibliocommons.com/v2/search?query=Casino%20Royale%20Bond) |
| Quantum of Solace | 2008 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Quantum%20of%20Solace%20Bond) |
| Skyfall | 2012 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Skyfall%20Bond) |
| Spectre | 2015 | BR | 2 BR copies | [search](https://sfpl.bibliocommons.com/v2/search?query=Spectre%20film) |
| No Time to Die | 2021 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=No%20Time%20to%20Die%20Bond) |

## Pixar — 27/28 on Blu-ray (1 DVD-only)

| Film | Year | SFPL format | Availability | Find it |
|---|---|---|---|---|
| Toy Story | 1995 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Toy%20Story) |
| A Bug's Life | 1998 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=A%20Bugs%20Life%20Pixar) |
| Toy Story 2 | 1999 | BR | All copies in use (holds) | [search](https://sfpl.bibliocommons.com/v2/search?query=Toy%20Story%202) |
| Monsters, Inc. | 2001 | BR | 3 BR copies | [search](https://sfpl.bibliocommons.com/v2/search?query=Monsters%20Inc%20Pixar) |
| Finding Nemo | 2003 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Finding%20Nemo) |
| The Incredibles | 2004 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Incredibles) |
| Cars | 2006 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Cars%20animated%20film) |
| Ratatouille | 2007 | BR | 2 BR copies | [search](https://sfpl.bibliocommons.com/v2/search?query=Ratatouille%20Pixar) |
| WALL-E | 2008 | BR | 2 BR copies (no DVD) | [search](https://sfpl.bibliocommons.com/v2/search?query=WALL-E%20Pixar%20movie) |
| Up | 2009 | BR | Available (Eng + Spanish BR) | [search](https://sfpl.bibliocommons.com/v2/search?query=Up%20Pixar%20film) |
| Toy Story 3 | 2010 | BR | All copies in use (holds) | [search](https://sfpl.bibliocommons.com/v2/search?query=Toy%20Story%203) |
| Cars 2 | 2011 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Cars%202) |
| Brave | 2012 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Brave%20Disney%20Pixar%20film) |
| Monsters University | 2013 | BR | 2 BR copies, Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Monsters%20University%20Pixar) |
| Inside Out | 2015 | BR | 2 BR copies | [search](https://sfpl.bibliocommons.com/v2/search?query=Inside%20Out%20emotions) |
| The Good Dinosaur | 2015 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=The%20Good%20Dinosaur) |
| Finding Dory | 2016 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Finding%20Dory) |
| Cars 3 | 2017 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Cars%203) |
| Coco | 2017 | BR | in collection (DVD Available) | [search](https://sfpl.bibliocommons.com/v2/search?query=Coco%20Pixar) |
| Incredibles 2 | 2018 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Incredibles%202) |
| Toy Story 4 | 2019 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Toy%20Story%204) |
| Onward | 2020 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Onward%20Pixar) |
| Soul | 2020 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Soul%20Pixar) |
| Luca | 2021 | **DVD-only** | DVD Available; no BR found | [search](https://sfpl.bibliocommons.com/v2/search?query=Luca%20sea%20monster%20movie) |
| Turning Red | 2022 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Turning%20Red) |
| Lightyear | 2022 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Lightyear%20movie%20animated) |
| Elemental | 2023 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Elemental%20Pixar) |
| Inside Out 2 | 2024 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Inside%20Out%202) |

## Marvel Cinematic Universe — 33/34 confirmed + 1 likely

| Film | Year | SFPL format | Availability | Find it |
|---|---|---|---|---|
| Iron Man | 2008 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Iron%20Man%20Favreau) |
| The Incredible Hulk | 2008 | BR | All copies in use | [search](https://sfpl.bibliocommons.com/v2/search?query=Incredible%20Hulk%20Edward%20Norton) |
| Iron Man 2 | 2010 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Iron%20Man%202) |
| Thor | 2011 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Thor%20Chris%20Hemsworth) |
| Captain America: The First Avenger | 2011 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Captain%20America%20First%20Avenger) |
| The Avengers | 2012 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Avengers%20Whedon%20Marvel%20film) |
| Iron Man 3 | 2013 | BR | All copies in use | [search](https://sfpl.bibliocommons.com/v2/search?query=Iron%20Man%203) |
| Thor: The Dark World | 2013 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Thor%20The%20Dark%20World) |
| Captain America: The Winter Soldier | 2014 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Captain%20America%20Winter%20Soldier) |
| Guardians of the Galaxy | 2014 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Guardians%20of%20the%20Galaxy) |
| Avengers: Age of Ultron | 2015 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Avengers%20Age%20of%20Ultron) |
| Ant-Man | 2015 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Ant-Man) |
| Captain America: Civil War | 2016 | BR | All copies in use (5) | [search](https://sfpl.bibliocommons.com/v2/search?query=Captain%20America%20Civil%20War) |
| Doctor Strange | 2016 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Doctor%20Strange%20Cumberbatch) |
| Guardians of the Galaxy Vol. 2 | 2017 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Guardians%20of%20the%20Galaxy%20Vol%202) |
| Spider-Man: Homecoming | 2017 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Spider-Man%20Homecoming) |
| Thor: Ragnarok | 2017 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Thor%20Ragnarok) |
| Black Panther | 2018 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Black%20Panther%202018) |
| Avengers: Infinity War | 2018 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Avengers%20Infinity%20War) |
| Ant-Man and the Wasp | 2018 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Ant-Man%20and%20the%20Wasp) |
| Captain Marvel | 2019 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Captain%20Marvel%20Brie%20Larson) |
| Avengers: Endgame | 2019 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Avengers%20Endgame) |
| Spider-Man: Far From Home | 2019 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Spider-Man%20Far%20From%20Home) |
| Black Widow | 2021 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Black%20Widow%20Marvel) |
| Shang-Chi | 2021 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Shang-Chi) |
| Eternals | 2021 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Eternals%20Marvel) |
| Spider-Man: No Way Home | 2021 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Spider-Man%20No%20Way%20Home) |
| Doctor Strange in the Multiverse of Madness | 2022 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Doctor%20Strange%20Multiverse%20of%20Madness) |
| Thor: Love and Thunder | 2022 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Thor%20Love%20and%20Thunder) |
| Black Panther: Wakanda Forever | 2022 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Wakanda%20Forever) |
| Ant-Man and the Wasp: Quantumania | 2023 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Ant-Man%20Quantumania) |
| Guardians of the Galaxy Vol. 3 | 2023 | BR | in collection | [search](https://sfpl.bibliocommons.com/v2/search?query=Guardians%20of%20the%20Galaxy%20Vol%203) |
| The Marvels | 2023 | **BR?** | Blu-ray likely; film record not isolated | [search](https://sfpl.bibliocommons.com/v2/search?query=The%20Marvels%20Captain) |
| Deadpool & Wolverine | 2024 | BR | Available | [search](https://sfpl.bibliocommons.com/v2/search?query=Deadpool%20and%20Wolverine) |

---

## The 3 to double-check in person / via LINK+

1. **From Russia with Love (1963)** — only a DVD record surfaced. If you want it on Blu-ray, request via LINK+ (SFPL's regional resource-sharing network) or check the physical AV Center shelf.
2. **Luca (2021)** — DVD-only in the catalog. Disney's own Luca Blu-ray exists retail; SFPL just may not stock it. LINK+ is the fallback.
3. **The Marvels (2023)** — the aggregate search showed Blu-ray discs present but I couldn't confirm the film's own bib record; very likely stocked given the rest of the MCU is.

## Practical notes for ripping

- Everything here tops out at **1080p Blu-ray** — there is no 4K UHD to be had at SFPL. For a title where you specifically want the 4K/HDR master (e.g. the Dune-style demo discs, or Bond 4K restorations), the library won't provide it; buy/borrow the UHD elsewhere.
- Many franchise Blu-rays circulate as **Extended Editions** (notably all six LOTR/Hobbit) — nice bonus for ripping the longer cuts.
- Availability floats hourly. Batch your holds: place holds on the "all copies in use" ones (Toy Story 2/3, Octopussy, Casino Royale, Iron Man 3, Civil War, Incredible Hulk) and grab the "Available" ones on a single AV Center run.
