---
status: planned
last_modified: 2026-11-06
summary: "Home Assistant overhaul (Plane HASS): current state, then four workstreams (maintenance reminders, energy tracking, floor plan, everyday-use app) plus the staging-refresh and http-block prerequisites, their order, and the questions only George can answer"
---

# Home Assistant overhaul — plan

**Plane:** HASS-1 (this plan), covering HASS-2, HASS-3, HASS-4, HASS-5, with HASS-6 and HASS-9 as
prerequisites · **Scope:** `apps/base/homeassistant/`, `apps/{staging,production}/homeassistant/`,
`renovate.json` · **This PR:** plan only, no config change.

George, 2026-10-31: "I'd like to also create a home assistant project for doing a comprehensive
overhaul. I'd like to add things like fine grained energy tracking, adding a floor plan, and
extending HA to be more of an every day use app."

Where the work happens (George, 2026-10-31): HA config stays in this repo, in
`apps/base/homeassistant/`. Each change is a homelab PR that George reviews and merges.

How to read this plan: statements marked **(verified)** were checked against the repo or the live
cluster on 2026-11-06, read-only (`kubectl get`/`describe`/`logs`, no exec into production).
Statements marked **(unverified)** could not be checked that way and need George or a UI read.
Anything only George can decide is a numbered question in [Questions for George](#questions-for-george).

## Current state

### Version

- **2026.10.4** in both environments (verified): `deployment.yaml` pins
  `ghcr.io/home-assistant/home-assistant:2026.10.4@sha256:23f4…e133`, and the running pods in
  `homeassistant-prod` and `homeassistant-stage` use that image. HASS-1's text says 2026.10.2; Renovate
  moved it to 2026.10.4 in #1659 (2026-10-31).
- Renovate proposes HA bumps but does not automerge them (`renovate.json`, `automerge: false` for
  `ghcr.io/home-assistant/home-assistant`) (verified). Nothing holds back 2027.2, which drops the
  `http:` YAML block (HASS-9).

### What is git-managed and what is UI-only

From `apps/base/homeassistant/bootstrap/README.md`, `kustomization.yaml` and `deployment.yaml`
(verified):

| Config | Where it lives |
|---|---|
| `configuration.yaml`, `automations.yaml` (22 automations), `scripts.yaml`, `binary_sensors.yaml`, theme `tinted` | git, mounted from the `homeassistant-config` ConfigMap (subPath) |
| Maintenance reminders and Daily shot (`homeassistant.packages`) | git, inside `configuration.yaml` |
| Home dashboard (`/home-page`) and Config dashboard (`/home-control`) | git, YAML mode, `files/dashboards/{home,control}.yaml` |
| Mushroom, card-mod, clock-weather-card | git, vendored in `files/www/`, loaded by `frontend.extra_module_url` |
| `secrets.yaml` (`agent_desk_webhook_id`) | per-environment SOPS Secret, operator-encrypted |
| Which dashboard opens first | UI-only (`.storage/frontend.system_data`); no YAML key exists |
| Areas | UI-only; snapshot in `bootstrap/core.area_registry.json` |
| Floor Plan and Map dashboards | UI-only (storage mode); snapshots in `bootstrap/` |
| auto-entities card | HACS, on the PVC, used by the Maintenance views |
| Integration config entries (Lutron Caséta, MQTT, ESPHome, mobile_app, …) and the device/entity registries | UI-only; HA has no YAML for them |

Not yet in the README's table (verified from the production log, 2026-11-06): five custom
integrations load from the PVC, not from git: `hacs`, `ldata` (v2.0.13), `subzero`, `pirateweather`
and `extended_openai_conversation`. Staging loads the first four (its copy came with the manual
restore of 2026-10-31, HASS-6).

### Dashboards

- **Home** (`files/dashboards/home.yaml`, one view): HA's Overview rebuilt from its own cards plus the
  Config Overview content (HASS-10). Its header says Energy was left out because it is "not known to
  be configured".
- **Config** (`files/dashboards/control.yaml`): Overview, Auto-shutoff (+ settings), Light schedules
  (+ settings), Maintenance (+ intervals), Audio / Visual, Lights, and per-area views.
- **Floor Plan** (snapshot `bootstrap/lovelace.floorplan.json`, taken 2026-07-26): one panel view, a
  `picture-elements` card over `/local/floorplan.svg` with 33 `state-icon` elements, all lights and
  switches (bathroom fans, closet lights). No sensors, presence, doors or energy. The SVG is not in
  git; whether it is still on the PVC is unverified.
- **Map** (snapshot `bootstrap/lovelace.map.json`): only `strategy: map`. Whether it still exists in
  production is unverified.
- Both HA environments log `duplicate key "area"` for `dashboards/home.yaml` lines 328 and 338
  (verified). It comes from the `&area_card` anchor carrying `area: living_room` and each
  `<<: *area_card` overriding it. The cards render; the warning is noise, and the fix is to move
  `area:` out of the anchor.

### Areas

`bootstrap/core.area_registry.json` lists 17 areas (verified), none of them assigned to a floor and
none with a picture. One, `office_presence`, is the ESPresense node's area, not a room (the Home
dashboard's header says so). The snapshot was taken 2026-07-26 and corrected against production on
2026-11-01/02 (#1774, `home.yaml` comment). It is a snapshot, so production may have drifted since.

### Integrations and devices in use

| Integration | Evidence | Used for |
|---|---|---|
| Lutron Caséta | netpol egress to the bridge on 8081/8083; automations | Most lights and switches, bathroom fans |
| MQTT (mosquitto) | netpol egress 1883; production log | zigbee2mqtt devices; ESPresense `mqtt_room` presence |
| zigbee2mqtt | bootstrap README "Depends on" | Zigbee devices via MQTT (incl. the dining room chandelier LEDs) |
| ESPHome | netpol egress to the IoT VLAN on 6053 | IR blaster, presence sensors (netpol comment) |
| Snapcast, Music Assistant | production overlay patches (1705, 8095) | Room volume, announcements |
| Wyoming (Piper), openai_tts (Kokoro) | overlay patches; production log | Text-to-speech |
| mobile_app | automations; production log | Phone notifications with Done actions |
| met, pirateweather | production log | Weather |
| subzero (custom) | production log | Kitchen appliances (status and faults) |
| ldata (custom) | production log: "Panel status poll error" every few minutes; "Leviton API version changed" | Unverified: what it exposes. See HASS-3 |
| extended_openai_conversation (custom) | production log, loader warning only | Unverified: whether it is in use |

All rows come from the repo or the 2026-11-06 logs (verified as evidence); the full list of config
entries is in `.storage/core.config_entries`, which this plan did not read.

### Staging vs production

- Same base, two overlays. Production reconciles from `master`; staging from the `staging` branch that
  `staging-deploy.yaml` builds from `master` plus every open PR with green CI (verified).
- Staging is `hass.stage.burntbytes.com`, production `hass.burntbytes.com`.
- Per-overlay egress keeps each HA on its own Piper, Kokoro, agent-desk and Music Assistant. Snapcast
  1705 moved to the production overlay on 2026-11-03 (verified), so HASS-6's note that staging can
  reach Snapcast is out of date.
- **Staging still inherits from base the egress to the Lutron bridge, to mosquitto and to the ESPHome
  VLAN** (verified). There is one mosquitto broker for both environments (one pod, namespace
  `mosquitto`), so MQTT from staging is MQTT to production's Zigbee devices. Staging HA has no MQTT
  config entry today (its log: "MQTT integration is not available"), and its 2026-11-02 to 11-06 log
  shows no Lutron, ESPHome, ldata, subzero or mobile_app activity (verified). The policy allows it
  anyway, so one restore or one UI click would let staging drive production devices.
- **Staging's PVC is stuck at 1Gi** (verified): spec 5Gi, condition `FileSystemResizePending`, and the
  pod's events repeat `resize2fs: Permission denied to resize filesystem` (online resize). Production
  completed the same resize to 5Gi. The condition message asks for a pod restart to finish it.

### Backups

- **HA's own backups:** a production backup exists, since George restored one onto staging on
  2026-10-31 (HASS-6). Whether automatic backups are scheduled, how many are kept and where they are
  written are unverified. If they are written to HA's default local location, they sit on the same
  PVC they back up.
- **CSI snapshots:** one VolumeSnapshot, `homeassistant-config-pre-2026-10-2` in `homeassistant-prod`,
  taken by hand on 2026-10-19 (verified). It is on the same TrueNAS pool as the PVC.
- **Off-box copy:** none found in the repo (verified absence in the repo; a TrueNAS-side task would
  not show here).
- Production's recorder filled the old 1Gi PVC on 2026-11-04 to 05 (`database or disk is full` until
  2026-11-05 04:10, verified). Cause and fix are in `storage.yaml`'s comment. `configuration.yaml` has
  no `recorder:` block, so HA's defaults apply (SQLite on the PVC, 10 days of history per HA's docs).

## Rules that carry across every workstream

1. **Config in git (YAML mode) wherever HA allows.** George, 2026-10-31: "I would prefer to keep as
   much of HA as possible in IaC, unless there is a strong argument not to". Anything that can't live
   in git gets a line in `bootstrap/README.md` saying why and how to rebuild it.
2. **`~/src/agents/UI.md` for every dashboard.** Task analysis, then a low-fidelity prototype George
   tries, then production YAML. Each UI PR reports against the UI.md checklist.
3. **Staging first.** Every change lands on `hass.stage.burntbytes.com` through the open PR before
   it merges. `scripts/validate-ha-yaml.py` and the Lint workflow gate the YAML.
4. **Staging never drives a physical device production owns.** No staging change, restore or
   prototype may reach the Lutron bridge, the shared MQTT broker, ESPHome devices, Snapcast, or a
   phone through a restored `mobile_app` registration.
5. **No secret in git** except SOPS files George encrypts. Agents ship a `.yaml.example`.
6. **The repo is public.** Neutral names in entities and notifications (as HASS-11 set), and no floor
   plan artwork or address detail without George's say-so (Q9).

## Workstreams

### HASS-2 — Maintenance reminders (done)

Shipped as `homeassistant.packages.maintenance_*` in `configuration.yaml`, the Config dashboard's
Maintenance views, and phone notifications with a Done action. Seven tasks are defined today (cat
pad, under-sink filter, fridge filter, espresso filter, car oil, furnace filters, grill cleaning).
Adding a task is one package block. Left here as the pattern the other workstreams follow: packages
in git, `default_entity_id` pinned, "never recorded" shown instead of a date.

### HASS-3 — Energy tracking

**Goal.** "Fine grained energy tracking": know where energy goes, by circuit and by device, over days
to years.

**What HA has today.**

- No `device_class: energy` or `power` entity, `utility_meter` or `recorder:` block in git (verified).
- The `ldata` custom integration runs in production and polls a Leviton panel (verified from the
  log). What it exposes is unverified: which circuits, whether it reports power (W) and energy (kWh),
  and how often.
- Whether HA's Energy dashboard has any source configured is unverified (it is UI-only,
  `.storage/energy`).

**Approach.**

1. **Inventory, read-only.** George shares Settings → Dashboards → Energy and the `ldata` device's
   entities (Q2). Record the entity list (entity id, unit, `state_class`, update rate)
   in this plan.
2. **Design from what exists**, before any hardware purchase:
   - Sources: whatever the panel reports as whole-home and per-circuit kWh. A per-device layer only
     where a metering device exists.
   - `utility_meter` helpers in git for day/week/month/year per circuit group, defined in one package
     so a circuit is one line.
   - Circuits grouped by area using `core.area_registry.json`, which is also what HASS-4's "energy by
     room" needs.
   - Cost only if George gives a tariff (Q4): a flat price, or a time-of-use schedule as a template
     sensor.
   - Retention: rely on long-term statistics (hourly, kept indefinitely per HA's docs) for history,
     and keep the recorder's raw history short so the PVC doesn't fill again. Set `recorder:`
     explicitly in git so the retention is written down.
3. **Then options by hardware**, only for what George says he has or wants (Q3): utility data,
   circuit-level CT clamps, smart plugs with metering, solar, battery, EV. For each: what "fine
   grained" becomes (per home, per circuit, per device), and its cost. Prices are researched at that
   point, not assumed here.
4. Build tickets follow George's answers.

**Decisions for George:** Q2, Q3, Q4, Q5.

### HASS-4 — Floor plan

**Goal.** A floor plan George uses, in git, built on the area registry.

**What exists.** The UI-managed Floor Plan dashboard: 33 light and switch icons over
`/local/floorplan.svg` (see Current state). The bootstrap README asks for it to move to YAML mode "when
it's revisited".

**Approach.**

1. **Task analysis** (UI.md step 1), folded into the HASS-5 interview: which questions George brings
   to a floor plan. Candidates from HASS-4, to confirm or drop, not assume: lights on, who is home and
   where, open doors, temperatures, energy by room (needs HASS-3).
2. **Artwork.** Ask George for the floor-plan source or dimensions (Q9). No layout is invented. Check
   with George whether `/local/floorplan.svg` still exists on the PVC; it is not in git.
3. **Low-fidelity prototype** George tries on his phone (Q8 for the form).
4. **Production:** a YAML-mode dashboard in `files/dashboards/floorplan.yaml`, added to the
   configMapGenerator and mounted like the other two. Elements keyed by area from the registry, so a
   room's icons come from its area. Then delete the storage-mode dashboard and keep the snapshot for
   history, as was done for Switchboard.
5. Areas need two fixes first (Q10): floors, if the house has more than one, and the
   `office_presence` area.

**Decisions for George:** Q7, Q8, Q9, Q10.

### HASS-5 — Everyday-use app

**Goal.** HA as the app George opens every day, not only a control panel.

**Approach.**

1. **Task analysis** (UI.md step 1): a short session with George on what he does each day that HA
   could answer or do. HASS-5's candidates (morning and evening routines, audio, lights, HASS-2
   reminders, HASS-3 energy, presence) are prompts, not findings. Record who uses it (George only, or
   other household members with their own phones; Q6) and on which device.
2. **Low-fidelity prototype** on his phone before any production dashboard change (Q8).
3. **Evaluate**: George does real tasks on it; note where he hesitates. Iterate.
4. **Production** in YAML mode, most likely as changes to the Home dashboard (`home.yaml`) rather than
   a third dashboard, since HASS-10 made Home the single main page. PR reports against the UI.md
   checklist.

**Decisions for George:** Q6, Q7, Q8.

### Prerequisite: HASS-6 — safe staging refresh from production

**Why it comes before the UI work.** Prototypes and dashboard PRs are tried on staging. A staging HA
without production's areas and entity registry shows "entity not available" where cards should be,
which defeats an evaluation. The 2026-10-31 manual restore failed on
`OSError: [Errno 16] Resource busy: '/config/automations.yaml'` (ConfigMap mount).

**Approach** (from HASS-6, adjusted to the current state):

1. **Staging network policy first, before any restore.** In the staging overlay, remove the base
   egress to the Lutron bridge, to mosquitto and to the ESPHome VLAN. Snapcast is already
   production-only. Staging's ESPresense `mqtt_room` sensor stops working there; it already does
   ("MQTT integration is not available").
2. **A staging CronJob** that takes production's latest HA backup from a location staging can read
   with its own read-only credential (a SOPS Secret George creates), restores only `.storage` into
   staging's `/config`, and leaves the git-mounted YAML alone.
3. **Sanitise before HA starts:** drop `auth`, `auth_provider.*`, `onboarding`, `cloud` and every
   token-bearing file; drop config entries for device and account integrations (Lutron Caséta, MQTT,
   ESPHome, mobile_app, ldata, subzero, Music Assistant, Snapcast, and whatever the audit of
   `core.config_entries` adds). Staging keeps git's automations only.
4. **Where production's backup goes** is decided with Q12. It should be off the PVC, which also
   closes the gap in Current state → Backups.
5. A weekly or monthly run (Q11) doubles as a restore drill for production's backups.
6. Finish staging's stuck resize first (Q13).

### Prerequisite: HASS-9 — remove the `http:` block before 2027.2

`configuration.yaml` keeps `http:` on purpose: a fresh `/config` imports it into `.storage/http`, and
without it every request through the gateway gets 400. HA 2027.2 stops reading it.

1. **Now:** a Renovate rule that holds `ghcr.io/home-assistant/home-assistant` below 2027.2 until
   this lands (Q14).
2. Read production's and staging's Settings → Network values (George, from the UI) and compare with
   the YAML, masks included.
3. Replace the safety net: extend the existing busybox init container to write a `.storage/http` seed
   from a git-managed file only when `.storage/http` is absent, in the schema 2026.10 uses (read from
   HA's source). Fallback: a manual step in the bootstrap README.
4. Test on a throwaway HA: fresh `/config` + seed is reachable through a proxy with
   `X-Forwarded-For`, and the repair doesn't appear. Then remove the block.

HASS-6 and HASS-9 meet here: a sanitised staging restore drops `.storage` files, and `.storage/http`
must not be one of them.

### Housekeeping found while writing this plan

Small PRs, no decision needed beyond review:

- Add the five custom integrations to `bootstrap/README.md` ("not in git, why, how to reinstall"), the
  way auto-entities is listed.
- Move `area:` out of the `&area_card` anchor in `home.yaml` to clear the duplicate-key warning.
- Add an explicit `recorder:` block once Q5 is answered.

## Order and dependencies

| Step | Ticket | Depends on | Who acts |
|---|---|---|---|
| 1 | Answer the questions below | this plan | George |
| 2 | Renovate hold below 2027.2 | Q14 | PR |
| 3 | Finish staging PVC resize | Q13 | George or an approved restart |
| 4 | Housekeeping PRs | — | PR |
| 5 | HASS-6 step 1: staging netpol | — | PR |
| 6 | HASS-3 inventory | Q2, Q3 | George reads, plan updated |
| 7 | HASS-5 task analysis (incl. floor-plan tasks) | Q6, Q7 | George + orchestrator |
| 8 | HASS-6 steps 2–5: backup location + refresh CronJob | Q11, Q12, step 5 | PR, George creates the SOPS Secret |
| 9 | HASS-5 / HASS-4 prototypes | step 7, Q8, Q9 | PR or mock, George tries |
| 10 | HASS-3 design and build tickets | step 6, Q4, Q5 | PR |
| 11 | HASS-5 / HASS-4 production dashboards | step 9 evaluated; HASS-4 energy-by-room needs step 10 | PR |
| 12 | HASS-9 seed + block removal | step 8 (so the sanitiser keeps `.storage/http`) | PR, before any 2027.2 bump |

Steps 2–7 can run in parallel. HASS-2 is already done.

## Questions for George

Each has a recommendation; none is decided until George answers, except where marked decided.

1. **Who builds HASS tickets.** On 2026-10-31 the decision was orchestrator-written PRs, with no
   agent-desk Jobs because homelab was on the deny list. Since HOMELAB-85 (2026-11-02),
   `apps/production/agent-desk/config.yaml` maps `HASS: gjcourt/homelab` with `denyRepos: []`, so a
   `ready` HASS ticket would now start a Job. Which do you want for this project? *Recommendation:*
   keep orchestrator PRs for the UI work (HASS-4, HASS-5), where the prototype loop needs you in the
   conversation; allow Jobs for the bounded YAML tickets (housekeeping, Renovate hold) once T13 has
   run.
   **Decided 2026-11-06 (George): the split.** The orchestrator writes the UI work (HASS-4, HASS-5)
   as PRs; agent-desk Jobs take bounded YAML tickets once T13 has run.
2. **Energy inventory read.** This plan does not exec into production, and an HA long-lived token
   carries the full rights of its user, so there is no read-only API path. Will you share the Energy
   settings page and the `ldata` device's entity list (screenshot or copied text)?
   *Recommendation:* yes, that; no new token.
3. **Energy hardware.** What does the `ldata` integration connect to (which panel, how many circuits
   are monitored)? Do you have, or want, any of: utility meter data, CT clamps, metering smart plugs,
   solar, battery, EV charging? *Recommendation:* design on what `ldata` already reports before
   buying anything.
4. **Tariff.** Do you want cost, and if so what rate plan (flat or time-of-use, and the rates)?
   *Recommendation:* energy first, cost once you give the rate.
5. **History retention.** HA's default keeps 10 days of raw history; hourly long-term statistics are
   kept indefinitely. Is that enough, or do you want longer raw history? *Recommendation:* keep the
   default and write it into `recorder:`; long-term statistics cover energy trends.
6. **Who uses the everyday app.** Only you, or other people in the house with their own phones (the
   Daily-shot reminder already plans for a second phone)? *Recommendation:* design for you first,
   with the second user named as a later round.
7. **Task analysis session.** Can we do a 20–30 minute session where you walk through a normal day and
   what you'd want HA to answer, including what a floor plan would be for? *Recommendation:* yes,
   before any prototype.
8. **Prototype form.** A clickable static mock first (no live data, no risk to devices), or a
   staging-only YAML dashboard? *Recommendation:* static mock for round one; staging dashboard for
   round two, after HASS-6 gives staging real registries.
9. **Floor-plan artwork in a public repo.** Do you have source artwork or dimensions? Is
   `/local/floorplan.svg` still on the PVC? And may a floor plan of the house be committed to this
   public repo? *Recommendation:* don't commit artwork; keep the image on the PVC (documented in the
   bootstrap README) and keep only the dashboard YAML in git.
10. **Areas.** Does the house have more than one floor (HA floors are unset)? Should
    `office_presence` stay an area or move to a label? *Recommendation:* add floors only if there are
    several; drop `office_presence` as an area if nothing else uses it.
11. **Staging refresh cadence.** Weekly or monthly? *Recommendation:* weekly; once automated it is
    cheap, and it tests production's backup every week.
12. **Production backup location.** Where should production HA backups go, off the PVC? (Today's
    location is unverified.) *Recommendation:* a location on a different box from the PVC's pool,
    which staging can read with a read-only credential; you choose the box.
13. **Staging PVC resize.** OK to restart staging HA to finish the 1Gi → 5Gi resize (condition
    `FileSystemResizePending`)? *Recommendation:* yes; staging only, and the online resize keeps
    failing.
14. **Hold HA below 2027.2.** OK to add a Renovate rule now that stops HA bumps at 2027.2 until
    HASS-9 lands? *Recommendation:* yes.
15. **extended_openai_conversation.** It loads in production but nothing in git refers to it. Still
    in use? *Recommendation:* if not, remove it through HACS.
