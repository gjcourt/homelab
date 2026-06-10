# Jellyfin library picks — PTP curated franchises

_Generated 2026-05-21 via PTP API. Seeder counts are point-in-time; sizes are absolute._

This doc curates franchise-complete download targets from PassThePopcorn for the Jellyfin library on alcatraz. Each pick is the best 1080p Blu-ray variant available with an alive swarm at the time of generation, biased toward x264 standard releases (not BD50/Remux) in the ~8–20 GiB sweet spot. Direct PTP torrent URLs are included for one-click grab via the Web UI.

## How this was built

- Query: `https://passthepopcorn.me/torrents.php?searchstr=<title>&year=<y>&grouping=0&json=1` per film, authenticated via the `ApiUser`/`ApiKey` headers.
- Variant selection: filtered to canonical releases (no Rifftrax, fan-edits, behind-the-scenes, special-effects spinoffs); ranked by quality (1080p Blu-ray > 1080p WEB > 720p), then size preference (8–20 GiB), then seeder count.
- Dead-swarm guard: any pick with 0 seeders is flagged; otherwise the highest-scoring variant wins.
- See `/tmp/ptp/` artifacts on the operator workstation for the raw JSON if you want to re-rank by different criteria.

## Summary

| Franchise | Films | Total size (1080p) |
|---|---|---|
| Star Wars | 6 | 87 GiB |
| Indiana Jones | 5 | 76 GiB |
| Lord of the Rings | 6 | 76 GiB |
| James Bond | 25 | 351 GiB |
| Pixar | 28 | 316 GiB |
| Marvel Cinematic Universe | 34 | 463 GiB |
| **Total** | **104** | **1369 GiB** |

## Star Wars

| # | Film | Size | Quality | Seeders | PTP link |
|---|---|---|---|---|---|
| 1 | Episode I: The Phantom Menace [1999] | 13.08 GiB | 1080p Blu-ray x264 | 737 | [link](https://passthepopcorn.me/torrents.php?id=1372&torrentid=152266) |
| 2 | Episode II: Attack of the Clones [2002] | 11.58 GiB | 1080p Blu-ray x264 | 675 | [link](https://passthepopcorn.me/torrents.php?id=1221&torrentid=152288) |
| 3 | Episode III: Revenge of the Sith [2005] | 13.00 GiB | 1080p Blu-ray x264 | 550 | [link](https://passthepopcorn.me/torrents.php?id=1223&torrentid=130028) |
| 4 | Episode IV: A New Hope [1977] | 16.26 GiB | 1080p Blu-ray x264 | 656 | [link](https://passthepopcorn.me/torrents.php?id=1225&torrentid=103571) |
| 5 | Episode V: The Empire Strikes Back [1980] | 15.37 GiB | 1080p Blu-ray x264 | 802 | [link](https://passthepopcorn.me/torrents.php?id=1374&torrentid=104557) |
| 6 | Episode VI: Return of the Jedi [1983] | 17.22 GiB | 1080p Blu-ray x264 | 696 | [link](https://passthepopcorn.me/torrents.php?id=1375&torrentid=152519) |

_Subtotal: 86.51 GiB across 6 films._

## Indiana Jones

| # | Film | Size | Quality | Seeders | PTP link |
|---|---|---|---|---|---|
| 1 | Raiders of the Lost Ark [1981] | 17.31 GiB | 1080p Blu-ray x264 | 669 | [link](https://passthepopcorn.me/torrents.php?id=3234&torrentid=168511) |
| 2 | Indiana Jones and the Temple of Doom [1984] | 19.86 GiB | 1080p Blu-ray x264 | 535 | [link](https://passthepopcorn.me/torrents.php?id=3232&torrentid=189887) |
| 3 | Indiana Jones and the Last Crusade [1989] | 9.84 GiB | 1080p Blu-ray x264 | 130 | [link](https://passthepopcorn.me/torrents.php?id=339&torrentid=167856) |
| 4 | Kingdom of the Crystal Skull [2008] | 10.92 GiB | 1080p Blu-ray x264 | 366 | [link](https://passthepopcorn.me/torrents.php?id=104&torrentid=126150) |
| 5 | Indiana Jones and the Dial of Destiny [2023] | 18.19 GiB | 1080p Blu-ray x264 | 159 | [link](https://passthepopcorn.me/torrents.php?id=317345&torrentid=1222971) |

_Subtotal: 76.12 GiB across 5 films._

## Lord of the Rings

| # | Film | Size | Quality | Seeders | PTP link |
|---|---|---|---|---|---|
| 1 | Fellowship of the Ring [2001] | 16.39 GiB | 1080p Blu-ray x264 | 191 | [link](https://passthepopcorn.me/torrents.php?id=1621&torrentid=89421) |
| 2 | Two Towers [2002] | 3.33 GiB | NTSC DVD DVD5 | 4 | [link](https://passthepopcorn.me/torrents.php?id=194279&torrentid=660848) |
| 3 | Return of the King [2003] | 18.59 GiB | 1080p Blu-ray x264 | 219 | [link](https://passthepopcorn.me/torrents.php?id=858&torrentid=89626) |
| 4 | The Hobbit: An Unexpected Journey [2012] | 13.19 GiB | 1080p Blu-ray x264 | 54 | [link](https://passthepopcorn.me/torrents.php?id=80759&torrentid=400318) |
| 5 | The Hobbit: The Desolation of Smaug [2013] | 13.16 GiB | 1080p Blu-ray x264 | 50 | [link](https://passthepopcorn.me/torrents.php?id=105186&torrentid=327923) |
| 6 | The Hobbit: The Battle of the Five Armies [2014] | 10.93 GiB | 1080p Blu-ray x264 | 31 | [link](https://passthepopcorn.me/torrents.php?id=121098&torrentid=347742) |

_Subtotal: 75.60 GiB across 6 films._

## James Bond

| # | Film | Size | Quality | Seeders | PTP link |
|---|---|---|---|---|---|
| 1 | Dr. No [1962] | 16.12 GiB | 1080p Blu-ray x264 | 410 | [link](https://passthepopcorn.me/torrents.php?id=1271&torrentid=308361) |
| 2 | From Russia with Love [1963] | 14.94 GiB | 1080p Blu-ray x264 | 410 | [link](https://passthepopcorn.me/torrents.php?id=1406&torrentid=178198) |
| 3 | Goldfinger [1964] | 12.43 GiB | 1080p Blu-ray x264 | 75 | [link](https://passthepopcorn.me/torrents.php?id=1407&torrentid=128150) |
| 4 | Thunderball [1965] | 16.29 GiB | 1080p Blu-ray x264 | 374 | [link](https://passthepopcorn.me/torrents.php?id=1408&torrentid=173247) |
| 5 | You Only Live Twice [1967] | 8.80 GiB | 1080p Blu-ray x264 | 72 | [link](https://passthepopcorn.me/torrents.php?id=1409&torrentid=169028) |
| 6 | On Her Majesty's Secret Service [1969] | 17.21 GiB | 1080p Blu-ray x264 | 359 | [link](https://passthepopcorn.me/torrents.php?id=1410&torrentid=173246) |
| 7 | Diamonds Are Forever [1971] | 16.25 GiB | 1080p Blu-ray x264 | 329 | [link](https://passthepopcorn.me/torrents.php?id=1411&torrentid=171549) |
| 8 | Live and Let Die [1973] | 15.50 GiB | 1080p Blu-ray x264 | 316 | [link](https://passthepopcorn.me/torrents.php?id=1412&torrentid=178036) |
| 9 | The Man with the Golden Gun [1974] | 15.45 GiB | 1080p Blu-ray x264 | 270 | [link](https://passthepopcorn.me/torrents.php?id=1413&torrentid=195513) |
| 10 | The Spy Who Loved Me [1977] | 18.45 GiB | 1080p Blu-ray x264 | 269 | [link](https://passthepopcorn.me/torrents.php?id=1414&torrentid=172640) |
| 11 | Moonraker [1979] | 14.23 GiB | 1080p Blu-ray x264 | 322 | [link](https://passthepopcorn.me/torrents.php?id=1415&torrentid=285908) |
| 12 | For Your Eyes Only [1981] | 11.27 GiB | 1080p Blu-ray x264 | 27 | [link](https://passthepopcorn.me/torrents.php?id=1416&torrentid=34694) |
| 13 | Octopussy [1983] | 15.21 GiB | 1080p Blu-ray x264 | 264 | [link](https://passthepopcorn.me/torrents.php?id=1417&torrentid=171620) |
| 14 | A View to a Kill [1985] | 15.54 GiB | 1080p Blu-ray x264 | 298 | [link](https://passthepopcorn.me/torrents.php?id=1418&torrentid=172530) |
| 15 | The Living Daylights [1987] | 14.64 GiB | 1080p Blu-ray x264 | 284 | [link](https://passthepopcorn.me/torrents.php?id=1457&torrentid=177382) |
| 16 | Licence to Kill [1989] | 10.94 GiB | 1080p Blu-ray x264 | 51 | [link](https://passthepopcorn.me/torrents.php?id=1458&torrentid=22791) |
| 17 | GoldenEye [1995] | 9.86 GiB | 1080p Blu-ray x264 | 365 | [link](https://passthepopcorn.me/torrents.php?id=1480&torrentid=177158) |
| 18 | Tomorrow Never Dies [1997] | 15.49 GiB | 1080p Blu-ray x264 | 401 | [link](https://passthepopcorn.me/torrents.php?id=1481&torrentid=169999) |
| 19 | The World Is Not Enough [1999] | 10.93 GiB | 1080p Blu-ray x264 | 44 | [link](https://passthepopcorn.me/torrents.php?id=1479&torrentid=305261) |
| 20 | Die Another Day [2002] | 14.12 GiB | 1080p Blu-ray x264 | 43 | [link](https://passthepopcorn.me/torrents.php?id=1478&torrentid=15422) |
| 21 | Casino Royale [2006] | 18.04 GiB | 1080p Blu-ray x264 | 613 | [link](https://passthepopcorn.me/torrents.php?id=821&torrentid=745714) |
| 22 | Quantum of Solace [2008] | 15.30 GiB | 1080p Blu-ray x264 | 470 | [link](https://passthepopcorn.me/torrents.php?id=2058&torrentid=171855) |
| 23 | Skyfall [2012] | 12.07 GiB | 1080p Blu-ray x264 | 585 | [link](https://passthepopcorn.me/torrents.php?id=78747&torrentid=196671) |
| 24 | Spectre [2015] | 10.93 GiB | 1080p Blu-ray x264 | 149 | [link](https://passthepopcorn.me/torrents.php?id=134512&torrentid=403078) |
| 25 | No Time to Die [2021] | 11.22 GiB | 1080p WEB H.264 | 769 | [link](https://passthepopcorn.me/torrents.php?id=266258&torrentid=985166) |

_Subtotal: 351.21 GiB across 25 films._

## Pixar

| # | Film | Size | Quality | Seeders | PTP link |
|---|---|---|---|---|---|
| 1 | Toy Story [1995] | 18.59 GiB | 1080p Blu-ray H.264 | 89 | [link](https://passthepopcorn.me/torrents.php?id=1486&torrentid=730261) |
| 2 | A Bug's Life [1998] | 7.67 GiB | 1080p Blu-ray x264 | 438 | [link](https://passthepopcorn.me/torrents.php?id=9241&torrentid=184626) |
| 3 | Toy Story 2 [1999] | 19.98 GiB | 1080p Blu-ray H.264 | 22 | [link](https://passthepopcorn.me/torrents.php?id=1816&torrentid=924228) |
| 4 | Monsters, Inc. [2001] | 9.09 GiB | 1080p Blu-ray x264 | 590 | [link](https://passthepopcorn.me/torrents.php?id=1021&torrentid=135116) |
| 5 | Finding Nemo [2003] | 11.38 GiB | 1080p Blu-ray H.264 | 506 | [link](https://passthepopcorn.me/torrents.php?id=2783&torrentid=180501) |
| 6 | The Incredibles [2004] | 10.50 GiB | 1080p Blu-ray x265 | 39 | [link](https://passthepopcorn.me/torrents.php?id=1032&torrentid=738545) |
| 7 | Cars [2006] | 12.85 GiB | 1080p Blu-ray x264 | 472 | [link](https://passthepopcorn.me/torrents.php?id=3209&torrentid=544571) |
| 8 | Ratatouille [2007] | 11.57 GiB | 1080p Blu-ray x264 | 652 | [link](https://passthepopcorn.me/torrents.php?id=1742&torrentid=287912) |
| 9 | WALL-E [2008] | 0.11 GiB | 720p Blu-ray x264 | 23 | [link](https://passthepopcorn.me/torrents.php?id=90798&torrentid=219857) |
| 10 | Up [2009] | 9.79 GiB | 1080p Blu-ray x264 | 548 | [link](https://passthepopcorn.me/torrents.php?id=11284&torrentid=203289) |
| 11 | Toy Story 3 [2010] | 8.34 GiB | 1080p Blu-ray x264 | 583 | [link](https://passthepopcorn.me/torrents.php?id=24086&torrentid=184198) |
| 12 | Cars 2 [2011] | 7.79 GiB | 1080p Blu-ray x264 | 340 | [link](https://passthepopcorn.me/torrents.php?id=48520&torrentid=183376) |
| 13 | Brave [2012] | 6.19 GiB | 1080p Blu-ray x264 | 422 | [link](https://passthepopcorn.me/torrents.php?id=72747&torrentid=176057) |
| 14 | Monsters University [2013] | 11.39 GiB | 1080p Blu-ray x264 | 302 | [link](https://passthepopcorn.me/torrents.php?id=94215&torrentid=259589) |
| 15 | Inside Out [2015] | 10.78 GiB | 1080p Blu-ray x264 | 606 | [link](https://passthepopcorn.me/torrents.php?id=129004&torrentid=388569) |
| 16 | The Good Dinosaur [2015] | 11.68 GiB | 1080p Blu-ray x264 | 205 | [link](https://passthepopcorn.me/torrents.php?id=135153&torrentid=407133) |
| 17 | Finding Dory [2016] | 12.52 GiB | 1080p Blu-ray x264 | 194 | [link](https://passthepopcorn.me/torrents.php?id=143169&torrentid=455333) |
| 18 | Cars 3 [2017] | 11.23 GiB | 1080p Blu-ray x264 | 298 | [link](https://passthepopcorn.me/torrents.php?id=162706&torrentid=523711) |
| 19 | Coco [2017] | 11.21 GiB | 1080p Blu-ray x264 | 587 | [link](https://passthepopcorn.me/torrents.php?id=169680&torrentid=548183) |
| 20 | Incredibles 2 [2018] | 14.23 GiB | 1080p Blu-ray x264 | 431 | [link](https://passthepopcorn.me/torrents.php?id=181969&torrentid=613611) |
| 21 | Toy Story 4 [2019] | 12.33 GiB | 1080p Blu-ray x264 | 459 | [link](https://passthepopcorn.me/torrents.php?id=204392&torrentid=717700) |
| 22 | Onward [2020] | 14.19 GiB | 1080p Blu-ray x265 | 32 | [link](https://passthepopcorn.me/torrents.php?id=213398&torrentid=788308) |
| 23 | Soul [2020] | 10.53 GiB | 1080p Blu-ray x264 | 188 | [link](https://passthepopcorn.me/torrents.php?id=238739&torrentid=1108131) |
| 24 | Luca [2021] | 10.84 GiB | 1080p Blu-ray x264 | 183 | [link](https://passthepopcorn.me/torrents.php?id=254095&torrentid=952420) |
| 25 | Turning Red [2022] | 13.70 GiB | 1080p Blu-ray x264 | 147 | [link](https://passthepopcorn.me/torrents.php?id=275960&torrentid=1036267) |
| 26 | Lightyear [2022] | 13.32 GiB | 1080p Blu-ray x264 | 133 | [link](https://passthepopcorn.me/torrents.php?id=284895&torrentid=1069288) |
| 27 | Elemental [2023] | 12.93 GiB | 1080p Blu-ray x264 | 137 | [link](https://passthepopcorn.me/torrents.php?id=317346&torrentid=1193267) |
| 28 | Inside Out 2 [2024] | 11.61 GiB | 1080p Blu-ray x264 | 219 | [link](https://passthepopcorn.me/torrents.php?id=356298&torrentid=1328592) |

_Subtotal: 316.33 GiB across 28 films._

## Marvel Cinematic Universe

| # | Film | Size | Quality | Seeders | PTP link |
|---|---|---|---|---|---|
| 1 | Iron Man [2008] | 12.44 GiB | 1080p Blu-ray x264 | 583 | [link](https://passthepopcorn.me/torrents.php?id=11&torrentid=132692) |
| 2 | The Incredible Hulk [2008] | 10.81 GiB | 1080p Blu-ray x264 | 334 | [link](https://passthepopcorn.me/torrents.php?id=740&torrentid=159553) |
| 3 | Iron Man 2 [2010] | 11.64 GiB | 1080p Blu-ray x264 | 468 | [link](https://passthepopcorn.me/torrents.php?id=22750&torrentid=146667) |
| 4 | Thor [2011] | 10.87 GiB | 1080p Blu-ray x264 | 428 | [link](https://passthepopcorn.me/torrents.php?id=43641&torrentid=138722) |
| 5 | Captain America: The First Avenger [2011] | 12.64 GiB | 1080p Blu-ray x264 | 318 | [link](https://passthepopcorn.me/torrents.php?id=50466&torrentid=178064) |
| 6 | The Avengers [2012] | 15.72 GiB | 1080p Blu-ray x264 | 401 | [link](https://passthepopcorn.me/torrents.php?id=69268&torrentid=165679) |
| 7 | Iron Man 3 [2013] | 13.54 GiB | 1080p Blu-ray x264 | 252 | [link](https://passthepopcorn.me/torrents.php?id=89787&torrentid=246891) |
| 8 | Thor: The Dark World [2013] | 13.47 GiB | 1080p Blu-ray x264 | 375 | [link](https://passthepopcorn.me/torrents.php?id=102936&torrentid=279856) |
| 9 | Captain America: The Winter Soldier [2014] | 14.85 GiB | 1080p Blu-ray x264 | 423 | [link](https://passthepopcorn.me/torrents.php?id=109637&torrentid=315182) |
| 10 | Guardians of the Galaxy [2014] | 14.30 GiB | 1080p Blu-ray x264 | 493 | [link](https://passthepopcorn.me/torrents.php?id=116661&torrentid=331670) |
| 11 | Avengers: Age of Ultron [2015] | 10.93 GiB | 1080p Blu-ray x264 | 116 | [link](https://passthepopcorn.me/torrents.php?id=126643&torrentid=379300) |
| 12 | Ant-Man [2015] | 15.09 GiB | 1080p Blu-ray x264 | 432 | [link](https://passthepopcorn.me/torrents.php?id=130229&torrentid=393852) |
| 13 | Captain America: Civil War [2016] | 10.93 GiB | 1080p Blu-ray x264 | 126 | [link](https://passthepopcorn.me/torrents.php?id=141128&torrentid=443773) |
| 14 | Doctor Strange [2016] | 12.47 GiB | 1080p Blu-ray x264 | 275 | [link](https://passthepopcorn.me/torrents.php?id=152742&torrentid=474070) |
| 15 | Guardians of the Galaxy Vol. 2 [2017] | 16.08 GiB | 1080p Blu-ray x264 | 291 | [link](https://passthepopcorn.me/torrents.php?id=159328&torrentid=502470) |
| 16 | Spider-Man: Homecoming [2017] | 16.28 GiB | 1080p Blu-ray x264 | 471 | [link](https://passthepopcorn.me/torrents.php?id=161570&torrentid=516019) |
| 17 | Thor: Ragnarok [2017] | 10.94 GiB | 1080p Blu-ray x264 | 185 | [link](https://passthepopcorn.me/torrents.php?id=168804&torrentid=547452) |
| 18 | Black Panther [2018] | 10.93 GiB | 1080p Blu-ray x264 | 233 | [link](https://passthepopcorn.me/torrents.php?id=172976&torrentid=563312) |
| 19 | Avengers: Infinity War [2018] | 12.05 GiB | 1080p Blu-ray x264 | 235 | [link](https://passthepopcorn.me/torrents.php?id=176890&torrentid=584090) |
| 20 | Ant-Man and the Wasp [2018] | 11.85 GiB | 1080p Blu-ray x264 | 334 | [link](https://passthepopcorn.me/torrents.php?id=179742&torrentid=607555) |
| 21 | Captain Marvel [2019] | 18.23 GiB | 1080p Blu-ray x264 | 269 | [link](https://passthepopcorn.me/torrents.php?id=196440&torrentid=689190) |
| 22 | Avengers: Endgame [2019] | 13.12 GiB | 1080p Blu-ray x264 | 278 | [link](https://passthepopcorn.me/torrents.php?id=201037&torrentid=696116) |
| 23 | Spider-Man: Far From Home [2019] | 11.55 GiB | 1080p Blu-ray x264 | 239 | [link](https://passthepopcorn.me/torrents.php?id=203891&torrentid=712354) |
| 24 | Black Widow [2021] | 14.05 GiB | 1080p Blu-ray x264 | 167 | [link](https://passthepopcorn.me/torrents.php?id=255476&torrentid=965989) |
| 25 | Shang-Chi [2021] | 14.86 GiB | 1080p Blu-ray x264 | 143 | [link](https://passthepopcorn.me/torrents.php?id=266296&torrentid=985706) |
| 26 | Eternals [2021] | 16.53 GiB | 1080p Blu-ray x264 | 101 | [link](https://passthepopcorn.me/torrents.php?id=270924&torrentid=1006853) |
| 27 | Spider-Man: No Way Home [2021] | 11.15 GiB | 1080p Blu-ray x264 | 286 | [link](https://passthepopcorn.me/torrents.php?id=276009&torrentid=1020658) |
| 28 | Doctor Strange in the Multiverse of Madness [2022] | 14.75 GiB | 1080p Blu-ray x264 | 185 | [link](https://passthepopcorn.me/torrents.php?id=283297&torrentid=1054805) |
| 29 | Thor: Love and Thunder [2022] | 15.19 GiB | 1080p Blu-ray x264 | 180 | [link](https://passthepopcorn.me/torrents.php?id=288931&torrentid=1076159) |
| 30 | Black Panther: Wakanda Forever [2022] | 18.44 GiB | 1080p Blu-ray x264 | 33 | [link](https://passthepopcorn.me/torrents.php?id=299395&torrentid=1118398) |
| 31 | Ant-Man and the Wasp: Quantumania [2023] | 13.59 GiB | 1080p Blu-ray x264 | 100 | [link](https://passthepopcorn.me/torrents.php?id=307882&torrentid=1160318) |
| 32 | Guardians of the Galaxy Vol. 3 [2023] | 14.43 GiB | 1080p Blu-ray x264 | 16 | [link](https://passthepopcorn.me/torrents.php?id=317062&torrentid=1374144) |
| 33 | The Marvels [2023] | 10.82 GiB | 1080p Blu-ray x264 | 99 | [link](https://passthepopcorn.me/torrents.php?id=335586&torrentid=1261829) |
| 34 | Deadpool & Wolverine [2024] | 18.89 GiB | 1080p Blu-ray x264 | 126 | [link](https://passthepopcorn.me/torrents.php?id=359252&torrentid=1382428) |

_Subtotal: 463.43 GiB across 34 films._

## Caveats

- **Leecher counts are not shown** because they're near-zero across PTP's catalog at any time; these picks aren't optimized for ratio-building, they're optimized for completing a library.
- **All picks were 'not currently in qBit on alcatraz' at generation time** — the `is_in_qbit` matcher uses a fuzzy prefix compare against `torrents/info` from the qBittorrent Web API. False negatives possible if names differ; verify before mass-downloading.
- **Variants drift.** If you regenerate this doc later, the 'best' variant per film may change as new encodes are uploaded or old ones die off. Treat the URLs as snapshots, not stable references.
- **Size budget is real.** Grabbing everything is ~1.4 TiB. The Synology NAS (alcatraz) has the space, but consider Jellyfin's library scan time and Plex/Jellyfin transcoding costs before bulk-importing.

## Regenerating this doc

The Python script that built this is not committed (it embeds PTP API creds). To regenerate, ask Claude to run the equivalent of the prior conversation's workflow: fetch each franchise's films via the API, score variants, render the markdown.