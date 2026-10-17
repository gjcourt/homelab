---
status: planned
last_modified: 2026-10-12
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
  latest **stable** release at implementation time (2.10.6 on 2026-10-12; no nightlies).
- PVC `snapcast-music-assistant-data` (`truenas-iscsi`, 1Gi) mounted at `/data`.
- Service: add port 8095 (MA web UI + HA websocket API). Remove 6600/6680 with Mopidy (step 5).
- HTTPRoute: `cast.burntbytes.com` → 8095 at cutover (step 4). Until then MA gets a
  temporary host (`ma.burntbytes.com` / `ma.stage.burntbytes.com`; `music.*` is
  already Navidrome's).
- Network policy: allow 8095 from the gateway (`ingress`) and from the
  `homeassistant-*` namespaces; MA egress to Navidrome 4533 (already allowed for Mopidy,
  to `navidrome-prod` only).
- HA: add the built-in **Music Assistant** integration pointing at
  `http://snapcast.snapcast-prod.svc:8095`.

## Steps

1. **Stage.** Add the MA sidecar next to Mopidy in `snapcast-stage`. Configure the
   Snapcast provider ("use existing server", 127.0.0.1:1705) and the Subsonic provider
   (`navidrome-prod`, which stage Mopidy already uses; the base network policy allows
   egress only there). Run a throwaway snapclient against the stage server (no real
   speakers in stage) and confirm: MA lists it as a player, a Navidrome track plays,
   and a stream appears in `Server.GetStatus` for the MA session.
2. **Prod, side by side.** Same sidecar in prod, Mopidy still running. Add the HA
   integration. Tests, each on a real speaker:
   - Navidrome album → kitchen only; living-room keeps its Spotify stream.
   - Navidrome album → kitchen + living-room as one MA group, in sync.
   - `tts.speak` (Piper, `narrator_high`) → kitchen only. Then an announcement over
     music playing in the kitchen; the music should resume afterwards.
   - Spotify Connect to a Pi and to the pod's "Snapcast" device still work.
3. **George uses it for a while** at the temporary host. Iris stays at
   `cast.burntbytes.com` as the fallback.
4. **Cutover.** Point `cast.burntbytes.com` at MA (8095) and drop its `/` → `/iris/`
   redirect. Remove the temporary host.
5. **Remove Mopidy** once George is happy: the `mopidy` container, its ConfigMap and
   PVC, the `navidrome` stream and FIFO, Service and network-policy ports 6600/6680,
   and the `navidrome-credentials` Secret if MA's Subsonic login moves to its own Secret. Update
   `docs/operations/apps/snapcast.md`.

Steps 1–4 are reversible by reverting the PR; step 5 is the only one that removes anything.

## Open questions to settle in step 1–2

- Does snapserver 0.34 accept MA's `Stream.AddStream` calls with our config as-is, or
  does it need a JSON-RPC permission setting?
- MA's Subsonic login: reuse the `navidrome-credentials` Secret or a dedicated Navidrome user.
- Does MA leave groups as it found them after playback, or does it regroup clients?
  Spotify on a Pi and MA on the same speaker are two ways into one room — confirm they
  don't fight.
- The `snapcast` pod runs as uid 1000 with `runAsNonRoot`. Does the MA server image run
  as non-root, or does it need its own `securityContext`?
- MA web UI authentication. `cast.burntbytes.com` is LAN-only today; keep it that way.

## UI

This adopts MA's own web app rather than building one, so there is no prototype step.
Per `~/src/agents/UI.md`, judge it in step 3 on George's real task ("play this album in
the kitchen"): steps taken vs Iris + Snapweb (efficiency), whether the speaker choice is
obvious (learnability), and whether it is easy to start music in the wrong room by
mistake (safety).

## Out of scope

- Moving Spotify into MA. go-librespot stays as is.
- AirPlay/Chromecast players and Audiobookshelf in MA — possible later, not needed for
  the goal.
