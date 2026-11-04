---
status: in-progress
last_modified: 2026-11-04
summary: "Music Assistant replaces the Mopidy/Iris sidecar: play Navidrome to chosen speakers, and per-speaker TTS announcements from HA"
---

# Music Assistant replaces Mopidy/Iris

## Goal

Two things, one change:

1. **Play Navidrome to chosen speakers from one screen.** Today that takes two tools:
   Iris at `cast.burntbytes.com` (Mopidy sidecar) to pick music, then Snapweb to point
   each speaker at the `navidrome` stream. George uses `cast.burntbytes.com` only for
   this, and has no other use for Mopidy (2026-10-12).
2. **Speak HA announcements on one speaker, or a chosen subset.** HA's Snapcast
   `media_player`s cannot play media (no `PLAY_MEDIA`), so Piper TTS has no path to
   the speakers today.

Music Assistant (MA) does both: it browses Navidrome (Subsonic provider), plays to any
Snapcast client or group of clients, and exposes each speaker to HA as a
`media_player` that accepts `play_media` and announcements.

## Current state (2026-10-12)

- `snapcast` pod (stage + prod): `snapserver` 0.34.0, `go-librespot` (Spotify Connect
  "Snapcast"), `mopidy` (Iris on 6680, MPD on 6600, Subsonic → Navidrome).
- Streams: `default` (`/tmp/snapfifo`, 48 kHz), `spotify` and `navidrome` (44.1 kHz FIFOs).
- Prod clients: kitchen, living-room, office. Each is already in its own group, so
  per-speaker routing is a per-group stream switch.
- `cast.burntbytes.com` → Service port 6680 (Iris), prod only; stage has no Iris route.
- Each Pi also runs its own go-librespot (per-room Spotify Connect). Untouched by this plan.

## Design

**MA runs as a sidecar in the `snapcast` pod, replacing `mopidy`.**

MA's Snapcast provider, against an existing server, talks JSON-RPC on 1705 and creates
TCP streams on the server in the range **4953–5153** for the players it drives. As a
sidecar it reaches all of that on `127.0.0.1` (MA's default), so nothing new is exposed
on the LoadBalancer and the network policy only gains the MA UI port. A separate
Deployment would need ~200 extra Service ports (Kubernetes Services have no port
ranges) — not worth it.

MA's documented requirements for an external server, checked against ours:

| Requirement | Ours |
|---|---|
| snapserver ≥ 0.27, not 0.30 | 0.34.0 |
| a stream named `default` | yes |
| 48 kHz / 16-bit (defaults) | `default` is 48000:16:2 |
| ports 1704, 1705, 4953–5153 reachable from MA | localhost (sidecar) |

Pieces:

- `music-assistant` container, image `ghcr.io/music-assistant/server` pinned to the
  latest **stable** release at implementation time (2.10.9 on 2026-11-02; no nightlies).
- PVC `snapcast-music-assistant-data` (`truenas-iscsi`, 1Gi) mounted at `/data`.
- Service: add port 8095 (MA web UI + HA websocket API). Remove 6600/6680 with Mopidy (step 5).
- **Hostnames** (George, 2026-11-03: MA becomes his primary music app):
  - **MA** takes `music.burntbytes.com` / `music.stage.burntbytes.com`. It's LAN only and
    has no Authelia rule, because MA has its own login and HA's MA integration calls it
    through the gateway.
  - **Navidrome** moves to `navidrome.burntbytes.com` / `navidrome.stage.burntbytes.com`.
    Navidrome trusts the `Remote-User` header that Authelia sets, so every Navidrome
    hostname gets its `one_factor` rule in the same commit that adds the hostname.
  - **`cast.burntbytes.com`** is removed together with Mopidy in step 5.
- Network policy: allow 8095 from the gateway (`ingress`) and from the
  `homeassistant-*` namespaces; MA egress to Navidrome 4533 (already allowed for Mopidy,
  to `navidrome-prod` only).
- HA: add the built-in **Music Assistant** integration at MA's **public** URL
  (`https://music.burntbytes.com`). The integration's login step opens that URL in the
  browser, so the in-cluster Service URL doesn't work (found in stage, 2026-11-03).

## Steps

1. **Stage.** Add the MA sidecar next to Mopidy in `snapcast-stage`. Configure the
   Snapcast provider ("use existing server", 127.0.0.1:1705) and the Subsonic provider
   (`navidrome-prod`, which stage Mopidy already uses; the base network policy allows
   egress only there). Run a throwaway snapclient against the stage server (no real
   speakers in stage) and confirm: MA lists it as a player, a Navidrome track plays,
   and a stream appears in `Server.GetStatus` for the MA session.
2. **Prod, side by side.** Same sidecar in prod, Mopidy still running.
   - **Hostnames.** Before this step, Navidrome gets `navidrome.burntbytes.com` alongside
     `music.`, and George moves his Subsonic clients to the new name. In this step MA takes
     `music.burntbytes.com`, Navidrome drops `music.`, and the `music.` Authelia rule goes.
     That's the only breaking moment for a client still on `music.`.
   - **HA.** Add the HA integration. Tests, each on a real speaker:
   - Navidrome album → kitchen only; living-room keeps its Spotify stream.
   - Navidrome album → kitchen + living-room as one MA group, in sync.
   - `tts.speak` (Piper, `narrator_high`) → kitchen only. Then an announcement over
     music playing in the kitchen; the music should resume afterwards.
   - Spotify Connect to a Pi and to the pod's "Snapcast" device still work.
3. **George uses it for a while** at `music.burntbytes.com`. Iris stays at
   `cast.burntbytes.com` as the fallback.
4. **No separate cutover.** MA already has its permanent hostname from step 2, so this
   step is gone (2026-11-03).
5. **Remove Mopidy** once George is happy: the `cast.burntbytes.com` route and its
   homepage tile, the `mopidy` container, its ConfigMap and
   PVC, the `navidrome` stream and FIFO, Service and network-policy ports 6600/6680,
   and the `navidrome-credentials` Secret if MA's Subsonic login moves to its own Secret. Update
   `docs/operations/apps/snapcast.md`.

Steps 1–3 are reversible by reverting the PR; step 5 is the only one that removes anything.

**Who builds it (2026-11-02).** Every step changes the `snapcast` network policy, which the HOMELAB-85
bench gate does not allow for agent Jobs. So the orchestrator implements it as ordinary homelab PRs,
one per step, not as a bench Job.

## Step 1 results (stage, 2026-11-02 to 11-03)

MA 2.10.9 runs as the sidecar in `snapcast-stage`, as uid 1000 with all capabilities dropped.
A throwaway snapclient on George's Mac (`--hostID mac-ma-test`) stood in for a speaker.

**Works:**

- **Providers.** Snapcast set to "existing server" at 127.0.0.1:1705, and OpenSubsonic pointed at
  `navidrome-prod`. Chromecast, DLNA and AirPlay are disabled.
- **The player.** The Mac client shows up in MA as a player.
- **Streams.** Playing to it makes MA call `Stream.AddStream` for a TCP stream named
  `Music Assistant - <player>` and switch the client's group to it. snapserver 0.34 accepts this
  with our config as-is, with no JSON-RPC permission setting. The client receives FLAC at
  48000:16:2.
- **Volume.** MA's volume slider sets the client's Snapcast volume.

**Gotchas:**

- **The "existing server" switch.** While it is off, MA starts its own bundled snapserver, which
  competes with the sidecar for port 1705. Turn it on before saving the provider.
- **Provider labels.** MA 2.10.9's provider manifest marks Snapcast `unmaintained` and Sendspin
  `beta`. Sendspin can't be disabled. MA's docs list a community maintainer for the Snapcast
  provider, and give no deprecation or removal timeline (checked 2026-11-03).
- **Navidrome podcasts.** The OpenSubsonic provider logs a 501 warning about them. It's harmless,
  and turning podcasts off in the provider stops it.

**End to end, confirmed 2026-11-03 by George:**

- **Audio:** a Navidrome album played from MA through snapserver to the Mac client was audible
  on the Mac's speakers. The earlier "silence" was the Mac sending its output to a monitor with
  no speakers.
- **Control from the phone:** with MA open in Safari on George's iPhone, an album played to the
  Mac client. One uninterrupted play lasted about a minute (03:18:32–03:19:35 UTC), and George
  reported it still playing after that. The client's group stayed on
  `Music Assistant - mamacmatest`, with the stream `playing`.
- **The earlier 5–20 s plays** came from switching between players, not from a fault. Each stop
  matches a play starting on another player, and MA logged no error.

**MA's browser tab is a player.** Opening the MA web app registers that browser as a Sendspin
player ("Web (Safari on iPhone)", "Web (Chrome on Mac)"). The first play from the phone went to
the phone itself for about 8 s, before George chose the Mac. That matters for the UI safety
check in step 3; see "UI" below. MA also logs "non-compliant client" warnings about its own
Safari web player, a sign of how fast Sendspin is changing.

**Not settled:**

- **Does MA leave groups as it found them?** After playback MA puts the group back on `default`.
  In this test the group was already on `default`, so "restores the previous stream" and "always
  resets to default" look the same. Test it in step 2 with a group on `spotify`.

**Announcements, rehearsed in stage 2026-11-03 (George):**

- **It worked.** `music_assistant.play_announcement` (with `tts.google_translate_en_com`) on
  `media_player.georges_macbook_air_local` played on the Mac. HA called MA through the gateway,
  and MA fetched the TTS audio from HA on 8123 (#1790).
- **Use MA's public URL in the integration,** not the Service URL. Its login opens the URL in
  the browser.
- **The OAuth callback goes to prod HA.** In stage, `my.home-assistant.io` redirects it there,
  which shows "Invalid state". Change the host to `hass.stage.burntbytes.com` by hand.
- **Not yet tested:** an announcement over music, and whether the music resumes afterwards.
  That's still a step 2 test.
- **Re-adding the integration from a phone** (2026-11-04, after the move to `music.stage`): the
  HA companion app is signed in to prod, so the login callback opens the app. It works in Safari
  if `my.home-assistant.io`'s default instance is set to `https://hass.stage.burntbytes.com` first
  and the open-the-app prompt is cancelled. Enter the MA URL without a trailing slash: with
  one, MA logs requests to `//login`.

**Audiobookshelf, tried in stage 2026-11-04 (George):**

- **It works end to end.** MA's Audiobookshelf provider (stable, built in) logs in to staging ABS
  at `http://audiobookshelf.audiobookshelf-stage.svc:3005` with a username and password; an ABS
  API key also works. Books played to the Mac player, and ABS recorded a playback session for
  "Music Assistant" and saved the position.
- **Staging writes progress only to staging ABS**, the only ABS that staging MA's network policy
  reaches (#1795). Prod listening progress is untouched.
- **Staging ABS needed media.** It had no mounts and no libraries, so the provider reported "no
  libraries visible". #1802 mounts prod's audiobooks/podcasts tree read-only, as staging Navidrome
  does for music. After that, add a Book and a Podcast library in ABS, let the scan finish, then
  sync the provider in MA.
- **ABS was OOMKilled once** at the 512 MiB limit, during its first full scan while MA was
  syncing. The limit is raised in a separate PR.
- **Prod is not decided.** Whether prod MA gets ABS too is a step 2 question, below.

## Sendspin, MA's own protocol (researched 2026-11-03)

MA's direction is Sendspin, an Open Home Foundation protocol. MA still calls it a "technical
preview".

**Clients for our Pis:**

- **`sendspin-cpp-cli`.** C++ binaries for arm64, armv7 and armv6. It installs with a script that
  creates a systemd unit, and the ALSA device is set in `/etc/sendspin-cli.conf`. Its wiki
  names HiFiBerry HATs.
- **`sendspin`.** A Python client with `sendspin daemon`. It needs Python 3.12 or later and
  libportaudio2.

**Sync:** the spec uses a Kalman filter on clock offset and drift. No accuracy figure is
published. One open client PR addresses a start-up offset that is then corrected aggressively.

**Maturity:**

- The Python client went from 0.1.0 (2025-12) to 9.1.0 (2026-10-27).
- The C++ client's 0.4.0 changed the protocol, adding encryption and pairing. Re-adding clients
  after upgrades is documented.
- No first-hand HiFiBerry or DietPi reports were found.

**Our Pis (checked 2026-11-03):**

| Pi | OS | glibc | Python | C++ client | Python client |
|---|---|---|---|---|---|
| living-room (.39) | trixie | 2.41 | 3.13 | ✓ | ✓ |
| kitchen (.38) | bookworm | 2.36 | 3.11 | ✗ (armv7/arm64 need glibc 2.38) | ✗ |

The office Pi was not checked.

**Decision for now: stay on Snapcast for steps 2–5.**

- It's what the Pis run today.
- The Snapcast provider works in stage.
- Sendspin is changing its protocol from release to release.

Revisit when any of these happens:

- MA announces it is removing the Snapcast provider;
- Sendspin reaches a stable 1.0 client release;
- the kitchen Pi moves to trixie.

Migrating later only touches the Pis' client service. MA stays the same.

## Open questions to settle in step 2

Settled in step 1:

- **Does snapserver accept `Stream.AddStream`?** Yes, as-is, with no JSON-RPC permission setting.
- **Does MA need to run as root?** No. It runs as uid 1000 with all capabilities dropped. The one
  loss is AirPlay's PTP on UDP 319, and AirPlay isn't used.
- **Does MA keep a stream playing?** Yes. The "stops" were player switches (see above).
- **Authentication.** MA 2.10 has its own user accounts. Keep the host LAN-only as well.

Still open:

- MA's Subsonic login: reuse the `navidrome-credentials` Secret or a dedicated Navidrome user.
- Does MA leave groups as it found them? See "Not settled" above. Spotify on a Pi and MA on the
  same speaker are two ways into one room, so confirm they don't fight.
- Does an announcement over music resume the music afterwards? Not tested yet.
- Should prod MA get Audiobookshelf too? It worked in stage (above). If yes, prod MA writes
  listening progress to prod ABS, the same as the ABS app does.

## UI

This adopts MA's own web app rather than building one, so there is no prototype step.
Per `~/src/agents/UI.md`, judge it in step 3 on George's real task ("play this album in
the kitchen"): steps taken vs Iris + Snapweb (efficiency), whether the speaker choice is
obvious (learnability), and whether it is easy to start music in the wrong room by
mistake (safety). Step 1 found one concrete safety risk: MA's browser tab registers
itself as a player, and a phone's first play can land on the phone. Check whether MA can hide or
disable the web player, or keep the last chosen speaker as the default.

## Out of scope

- Moving Spotify into MA. go-librespot stays as is.
- AirPlay/Chromecast players in MA — possible later, not needed for the goal.
  (Audiobookshelf was tried in stage; see step 1 results.)
