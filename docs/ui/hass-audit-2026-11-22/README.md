# Home Assistant dashboards: UI.md audit, 2026-11-22

HASS-13, steps 1 and 2: record every dashboard view, then audit it against
[`UI.md`](https://github.com/gjcourt/agents/blob/main/UI.md) (usability goals, principles, review
checklist). George, 2026-11-08: "I'd also like to do an audit of the HASS dashboard using my ui.md
principles and try to clean it up / improve it. I'd like to make HASS as maximally efficient and
useful as possible."

This audits the state **after** homelab#1867 (phone-first Home, merged 2026-11-17). It follows the
first audit on 2026-11-08, which is not in this repo; findings that were raised then and are still
open are marked *(still open)*.

## How it was recorded

- Production, `https://hass.burntbytes.com`, HA 2026.10.4, Sunday 2026-11-22 at about 07:48 PT.
  Every light was off and nothing was playing, so the "while it applies" tiles (fridge door, oven,
  lights offline, daily shot due) were not visible.
- [uirec](https://github.com/gjcourt/agents/tree/main/tools/uirec) (gjcourt/agents#56), headless
  Chromium, light theme, one cold load per view with a 5 s settle. Walks:
  [`walks/hass13-desktop.yaml`](walks/hass13-desktop.yaml) (1440 × 900) and
  [`walks/hass13-phone.yaml`](walks/hass13-phone.yaml) (390 × 844). Both list no clicks and
  `allow_writes: false`: navigation is by URL only, nothing was tapped, nothing changed state.
- 25 URLs per width, 24 distinct views (`/` opens Home), 50 full-page screenshots:
  [`desktop/`](desktop/) and [`phone/`](phone/). PNGs are reduced to 128 colours.
- **Redacted, because this repo is public:** the map tiles in `*/17-map.png` (they show where the
  house is) and the street address in the Floor Plan title (`*/18-floorplan.png`). The videos are
  not committed for the same reason.
- **Not covered:** dark mode (uirec has no colour-scheme option yet), keyboard and screen reader,
  tap/hold behaviour and more-info dialogs (read-only), and repeated cold loads (one load per view,
  so the old "Configuration error" race is not proven fixed, only not seen).

## Inventory

| # | View | URL | In sidebar | Purpose as built | Screens on phone |
|---|---|---|---|---|---|
| 02 | **Home** (default) | `/home-page/home` | Home | Needs you, Music, Lights, Weather, Daily shot | 1.8 |
| 03 | Config → Overview | `/home-control/overview` | Config | Older copy of Home | 1.7 |
| 04 | Config → Auto-shutoff | `…/auto-shutoff` | tab | Fan and closet timers | 1.2 |
| 05 | … settings (subview) | `…/auto-shutoff-settings` | — | Timer lengths | 1.4 |
| 06 | Config → Light schedules | `…/light-schedules` | tab | Dining nightlight | 1 |
| 07 | … settings (subview) | `…/light-schedules-settings` | — | Nightlight settings | 1 |
| 08 | Config → Maintenance | `…/maintenance` | tab | Chores due / up to date | 1.1 |
| 09 | … intervals (subview) | `…/maintenance-intervals` | — | Chore intervals | 1.3 |
| 10 | Config → Audio / Visual | `…/av` | tab | Volume; IR remotes (offline) | 1 |
| 11 | Config → Lights | `…/lights` | tab (behind chevron on phone) | Every light by room | 3.2 |
| 12–16 | Config → 5 room subviews | `…/area-*` | — | One room's lights (Kitchen: appliances) | 1–1.2 |
| 17 | Map | `/map` | Map | People | 1 |
| 18 | Floor Plan | `/floorplan/floor-plan` | Floor Plan | Lights and switches on a plan | 1 |
| 19 | (default `lovelace`, auto) | `/lovelace/0` | no | HA auto-generated; renders as 20 | 1.6 |
| 20 | HA Overview (built-in) | `/home` | no | Generated summaries and areas | 1.6 |
| 21 | HA Lights (built-in) | `/light` | no | Every light by area | >1 |
| 22 | HA Climate (built-in) | `/climate` | no | Fridge, freezer, ovens | 1 |
| 23 | HA Security (built-in) | `/security` | no | Lock, appliance doors | 1 |
| 24 | HA Maintenance (built-in) | `/maintenance` | no | Batteries, unavailable devices | 1 |
| 25 | Energy | `/energy` | Energy | Setup wizard, step 1 of 6 | 1 |

Screenshot names below are `D/NN` (desktop) and `P/NN` (phone), numbered as in the table.

## What improved since 2026-11-08

- Home loads with built-in cards only, and no card showed "Configuration error" on any view in
  either run (`D/02`–`D/16`, `P/02`–`P/16`), including the Mushroom and clock-weather cards on
  Config. One load each, so treat this as "not reproduced", not "fixed".
- Home's All lights off asks first and offers Undo for 2 minutes; the predicted Favorites (and the
  breaker-switch risk that came with them) are gone.
- Maintenance (`P/08`) is a good view: overdue items first, the figure that matters on line one
  ("Overdue by 2.5 years"), colour by status, "Never recorded" instead of a blank.
- Honest empty states: "IR blaster offline. Its remotes are hidden until it reconnects" (`P/10`);
  Kitchen explains that fridge temperatures are set points, not readings (`P/13`).

## Findings

Ranked by severity. Each names the UI.md goal or principle it breaks, the screenshot, and a fix.

### High

**H1. "All clear" can be shown while the house knows something needs attention.**
*Honest data; Safety ("never show uncertain data as certain"); checklist "most common question".*
- Needs you is built from six checks only (fridge door, freezer door, oven, maintenance, lights
  offline, daily shot). It does not see things other views already show:
  the dishwasher's "Needs attention · Rinse aid low" (`P/13`, `D/13`); the built-in Maintenance
  view's unavailable and unknown-battery devices (`P/24`); the 7–8 unread HA notifications
  badged in the sidebar on every desktop screenshot (`D/01`–`D/25`).
- At the moment Needs you shows only Maintenance (`P/02`), so this is a gap rather than a live
  false "All clear". It becomes one the day maintenance is caught up.
- **Fix:** decide with George which sources count (question 3 below), then feed them into
  `sensor.needs_you`. Device health and HA notifications probably belong to George only, not to
  both phones.

**H2. Config → Overview still turns off every light with one tap, no confirmation, no undo.**
*Safety ("undo beats confirmation"; "nothing destructive without undo").* *(still open)*
- `D/03`, `P/03`: the large "All Lights Off" card. `control.yaml:60-64` calls `light.turn_off` on
  `entity_id: all`. Home fixed this in #1867 (`script.lights_off_with_undo`); the copy on Config
  did not get the fix.
- **Fix:** delete Config → Overview (see M5), or at least point it at
  `script.lights_off_with_undo` with the same confirmation text.

**H3. The Lights view shows a by-design state as a fault, and disagrees with Home.**
*Consistency; Honest data.*
- Config → Lights, Dining Room: red icon, "0 on · 2 offline", and two warning-badged tiles,
  "Central" and "Flowers", "Unavailable" (`P/11`, `D/11`). Light schedules shows the same two as
  "Unavailable" (`P/06`).
- `sensor.lights_offline` reads 0 and Home's Needs you is silent, on purpose:
  `configuration.yaml:1043-1045` says these two Zigbee LEDs "go unavailable whenever their switch
  is off, by design". So two screens give two answers, and the one in red is the wrong one. A
  warning that is always on teaches everyone to ignore warnings.
- **Fix:** show them as "Off (switch off)" while the dining master switch is off, and as offline
  only when the switch is on and they still don't answer. Use the same rule for the count, the
  Lights view and Light schedules.

### Medium

**M1. Home's order differs from the order George gave.** *Process: task analysis drives layout.*
- George, 2026-11-08 (HASS-13 comment), default-page order: "Music & lights, then needs". #1867
  puts Needs you first (`P/02`; `home.yaml` header). It may be the right call, since Needs you
  is one line when clear, but it is a change to his answer and was not put back to him.
- On desktop the layout is not what the source comment says ("needs and music on the left, lights
  on the right"): Needs you and Lights are on the left, Music and Weather on the right, and a
  ~350 px empty block sits under Needs you (`D/02`).
- **Fix:** question 1 below. On desktop, keep each column's height balanced or use one column.

**M2. Music rows hide whether anything is playing.** *Visibility; Safety.*
- Home shows only the volume: "Living Room 72%", "Kitchen 100%", "Office 100%" (`P/02`). Config
  shows "Idle · 100%" for the same players (`P/03`, `P/10`). Nothing says what is playing or where.
- Two rooms are at 100% while idle, so the next thing to play there starts at full volume, and
  nothing on Home warns of it.
- **Fix:** state and volume on each row ("Idle · 100%", or the title when playing); consider a
  default volume cap when a room goes idle (question 4).

**M3. Actions that can't apply are still offered.** *Safety ("disable what can't apply now").*
- "All lights off · 0 on" with an amber icon (`P/02`, `D/02`); the same on Config (`P/03`), and
  "All lights · 0 on" for every room on the Lights view (`P/11`). Daily shot shows a full-size
  Done tile and a separate "Today's side" tile while nothing is due (`P/02`).
- **Fix:** hide or grey out All lights off at 0 on. Daily shot: see M4.

**M4. Daily shot is three tiles for one fact, and still reads ambiguously.** *Simplicity;
Learnability.* *(still open, in a new form)*
- "Today · To do · Left", "Today's side · Left", "Done" (`P/02`). The "Left" on the first tile is
  `last_side`, the second is today's side; today both are Left, so nothing on screen says which
  is which.
- **Fix:** one tile, "Not done · today: Left", tap = Done (it already confirms).

**M5. Config is still a second Home, with its own names and order.** *Simplicity; Consistency.*
*(still open)*
- Config → Overview (`P/03`, `D/03`) repeats Home with a clock-weather card that takes a third of
  the desktop viewport, music in a different order (Kitchen, Living Room, Office vs Home's Living
  Room, Kitchen, Office), five room cards with no state, and a Summaries block.
- Its tabs are icons with no labels (`D/03`); on phone, five fit and the Lights tab sits behind a
  chevron (`P/04`). Recognition over recall.
- **Fix:** delete Overview; rename Config to "Settings" with labelled tabs; keep daily tasks on
  Home and its subviews (question 6).

**M6. Room pages are lights-only copies of the Lights view, and not every room has one.**
*Simplicity; Consistency; Mappings.*
- The five room subviews (`P/12`–`P/16`) list the same lights as Lights (`P/11`), larger. The
  master bath fan is on Auto-shutoff, not on Master Bathroom (`P/16`). Ten rooms with lights have
  no room page; Home's room tiles toggle and hold for the dimmer, so these pages are reached only
  from Config.
- Lights that are off show their brightness slider as an empty grey bar (`P/12`, `P/13`, `P/15`,
  `D/13`), which reads as "still loading".
- **Fix:** either one room template for every room (lights → fan/timer → appliances), reached
  from the Home tile, or drop room pages and keep Lights (question 5). Hide the slider while off.

**M7. Names drift between screens.** *Consistency; Match the user's world.* *(still open)*
- Entryway (Home, Config) vs "Front Foyer Closet/Spot/Chandelier" (built-in Lights, `P/21`).
- Exterior (Home) vs "Back Yard" (built-in Overview areas, `P/20`); Niccolo Bedroom vs "Secondary
  Bedroom" (`P/20`).
- The dining chandelier is "Master Switch" on Light schedules (`P/06`) and "Chandelier" on Lights.
- Raw Zigbee ids: "0x000d6f000fec…" (a lock, under Kitchen, `P/23`; two unknown batteries,
  `P/24`), "0x00158d00091…" (`P/24`). Areas named "office-presence" and "Devices" (`P/20`).
- Case: "All Lights Off" (Config) vs "All lights off" (Home); "Preset Brightness",
  "Turn-Off Time" (`P/07`).
- **Fix:** one name per room and fixture, set in HA's registry so every generated view picks it
  up; sentence case; give the lock and sensors names and the right areas.

**M8. HA's built-in dashboards are reachable, unmaintained, and two carry controls.**
*Safety; Consistency.*
- HA 2026.10 serves Overview, Lights, Climate, Security and Maintenance dashboards. None is in the
  sidebar, but each opens from its URL and from search.
- Climate (`P/22`) puts the fridge (38 °F) and freezer (1 °F) set points behind one-tap − / +
  steppers. Security (`P/23`) shows the lock as "0x000d6f000fec… Locked" under Kitchen.
- "Maintenance" now means two things: George's chores (Config) and HA's battery and offline-device
  view (`P/24`).
- **Fix:** curate area assignments and names (M7) so the generated views are right, or turn the
  built-ins off. Rename the chores view if the built-in stays (question 7).

**M9. Energy is a setup wizard in the sidebar.** *Visibility; Simplicity.* *(still open)*
- `P/25`, `D/25`: "Step 1 of 6, Electricity grid". Blocked on HASS-3 (naming the 23 LDATA
  breakers).
- **Fix:** hide Energy from the sidebar until it is configured.

### Low

**L1. Floor Plan doesn't work at phone width and is cluttered on desktop.** *Visibility.*
- Phone (`P/18`): the plan is about 140 px tall, and the icons overlap each other and the room
  names. Desktop (`D/18`): icons sit on top of labels ("Office", "Living Room"); the phone-shaped
  icons (switches?) have no label. Every light shows the same off icon, so it carries less than
  Home's room tiles. **Fix:** remove it, or redraw it for phone with fewer, labelled marks
  (question 7).

**L2. Map lists "Me" rather than George, and a "Claude" person** at "Unknown · 2 days ago"
(`P/17`, `D/17`). *(still open)* **Fix:** hide non-human persons; show names.

**L3. Settings subviews: grammar and truncation.** "1 years" and each interval shown twice, as
subtitle and stepper (`P/09`) *(still open)*. "Master bedroom…", "Niccolo bedroo…" truncated
(`P/05`). **Fix:** "1 year"; drop the subtitle; shorter names ("Bedroom closet").

**L4. Jargon and hidden gestures.** "Jitter (today) · 3 min" on Light schedules (`P/06`) needs its
one-line definition there, not only on the settings subview. Auto-shutoff's hold-to-keep-on is
explained only in a paragraph (`P/04`). *Learnability.*

**L5. A permanent notification badge.** 7–8 unread notifications on every screen (`D/*`, orange
dot on the phone menu, `P/*`). A badge that never clears stops meaning anything. **Fix:** clear
them, or route the useful ones into Needs you (H1).

**L6. Fifteen identical "Off" tiles.** With everything off, Lights is an 8-row wall of "Off"
(`P/02`), pushing Weather and Daily shot below the second screen. **Fix:** consider a single
"All off" line with rooms one tap away, or rooms that are on first (question 5).

## UI.md checklist, as built today

| Item | Home | Config | Built-ins / other |
|---|---|---|---|
| Main task works for a first-time user | Yes | Partly: icon-only tabs, gestures explained in text | No: raw names, wizard |
| Default view answers the most common question | Mostly; order differs from George's (M1) | n/a | n/a |
| Visible feedback; progress when slow | Not tested (read-only) | Not tested | Not tested |
| Nothing destructive without undo or preview | Yes | **No** (H2) | Climate steppers (M8) |
| Missing / uncertain values labelled | Partly (H1, M2) | **No**: by-design state shown as fault (H3) | Raw "Unknown" batteries |
| Names, units, colours consistent | Partly (M7) | Partly (M5, M7) | No (M7) |
| One purpose per screen, visible hierarchy | Yes | Overview duplicates Home | Floor Plan (L1) |
| Phone width / dark / keyboard / screen reader | Phone yes; others not tested | Phone: tabs overflow | Floor Plan fails at phone width |
| George tried it on a real task | Not yet | — | — |

## Questions for George's task analysis

Already answered on 2026-11-08 (HASS-13 comment): two of you, on phones; most common is "Anything
needed of me, music volume, lights"; breakers behind confirmation; daily shot and chores "Only
until done". These are the open ones.

1. **Home order.** You said "Music & lights, then needs"; Home now starts with Needs you.
   (a) Keep Needs you first: it is one line when nothing is due; (b) Music, Lights, then Needs
   you, as you said; (c) Needs you only when something is due, otherwise not shown at all.
2. **Does Mara use the same views?** (a) Home only; (b) Home and the Lights view; (c) everything
   you use. This decides whether Config becomes admin-only.
3. **What counts as "needs you"?** Choose any: (a) appliances (dishwasher rinse aid, filters);
   (b) device health (offline lights, low batteries) for you only; (c) HA notifications, repairs
   and updates, for you only; (d) none of these, keep it to doors, oven, chores and the shot.
4. **When you open Music, what do you do?** (a) change the volume of what's already playing;
   (b) start something, so Home needs play and source; (c) check what's playing where. And
   should an idle room drop to a safe volume? (yes / no)
5. **Single fixtures or whole rooms?** How often do you change one light rather than a room?
   (a) rarely: drop the room pages, keep the Lights view; (b) weekly: one room page per room,
   opened from the Home tile; (c) daily: put fixtures on Home.
6. **Config dashboard.** (a) Delete Overview, rename to "Settings", labelled tabs, admin-only;
   (b) delete Overview only; (c) leave it.
7. **The rest of the sidebar and the built-ins.** For each of Floor Plan, Map, Energy, and HA's
   Overview / Lights / Climate / Security / Maintenance: (a) remove; (b) keep as is;
   (c) keep and fix. Suggested: remove Floor Plan and the built-ins, keep Map, hide Energy
   until HASS-3.
8. **How often do you each open HA?** (a) several times a day; (b) about once a day;
   (c) only when something is wrong or a notification arrives. Mostly (a) favours controls on
   top; mostly (c) favours Needs you on top and actionable notifications.
9. **Where is the daily shot logged from?** (a) the Home tile; (b) the 19:00 notification's
   action; (c) both, so keep both.
