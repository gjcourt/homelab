# Snapcast (Snapserver)

## 1. Overview
Snapcast is a multi-room client-server audio player, where all clients are time-synchronized with the server to play perfectly synced audio. In this homelab, it serves as the backbone for multi-room audio, allowing various clients (Raspberry Pis, PCs, mobile devices) to play synchronized music.

## 2. Architecture
Snapcast is deployed as a Kubernetes `Deployment` with a single replica in the `snapcast-prod` (and `snapcast-stage`) namespace.
- **Containers**:
  - `snapserver`: The main Snapcast server that reads audio streams and serves them to clients.
  - `go-librespot` (Sidecar): An open-source Spotify client that acts as a Spotify Connect receiver. It outputs raw PCM audio to a shared named pipe (FIFO) that `snapserver` reads.
- **Storage**:
  - **Spotify State / Server State**: Uses a PersistentVolumeClaim (`snapcast-spotify-state`) backed by the `synology-iscsi` storage class. Both containers share this PVC:
    - `go-librespot` mounts it at `/config` — stores `state.json` (Spotify OAuth credentials and device ID).
    - `snapserver` mounts it at `/var/lib/snapserver` (`XDG_CONFIG_HOME`) — stores `snapserver/server.json` (client MAC addresses and their group/stream assignments). This persistence means kitchen and living-room stay on the `spotify` stream across pod restarts.
  - **Shared Audio**: Uses an `emptyDir` volume to share the named pipe (`/audio/spotify.fifo`) between the `go-librespot` sidecar and `snapserver`.
- **Networking**:
  - The `snapcast` Service is a `LoadBalancer` (via Cilium IPAM) exposing:
    - `1704/TCP`: Snapcast audio stream
    - `1705/TCP`: Snapcast control / RPC
    - `1780/TCP`: Snapweb (web UI)
  - Exposed via Cilium Gateway API (`HTTPRoute`) for the web UI.

## 3. URLs
- **Staging**: https://snapcast.stage.burntbytes.com
- **Production**: https://snapcast.burntbytes.com

## 4. Configuration
- **Environment Variables**: N/A
- **ConfigMaps/Secrets**:
  - `snapcast-config` (ConfigMap): Contains the `snapserver.conf` file, defining the audio streams (e.g., `pipe:///audio/spotify.fifo?name=Spotify&sampleformat=44100:16:2`).
- **Spotify Connect**: The `go-librespot` sidecar is configured via command-line arguments in the deployment to output to the shared FIFO.

## 5. Usage Instructions
- **Web UI (Snapweb)**: Navigate to the URL to control client volume, group clients, and select the active audio stream for each group.
- **Clients**: Run `snapclient` on your devices (e.g., Raspberry Pi) pointing to the `snapserver` LoadBalancer IP:
  ```bash
  snapclient -h <snapserver-lb-ip>
  ```
- **Spotify Connect**: Open the Spotify app on your phone or computer, select "Devices Available", and choose "Snapcast". Playback will be routed through the Snapserver to all connected clients.

### Playing Navidrome (the `navidrome` stream, via MPD)

The Mopidy sidecar bridges Navidrome into Snapcast: it exposes the **MPD
protocol on port 6600** (on the snapcast LB, `10.42.2.37:6600`), pulls the
library from Navidrome over the Subsonic API, and writes audio into the
`navidrome` snapcast stream. There are **two control surfaces** — one chooses
the music, the other chooses the speakers:

| Task | Where | Address |
|---|---|---|
| Choose & play music (Navidrome library) | an **MPD client** → Mopidy | `10.42.2.37:6600` (no password) |
| Route audio to speakers | **Snapweb** | `snapcast.burntbytes.com` / `http://10.42.2.37:1780` |

1. **Assign a speaker to the `navidrome` stream** in Snapweb: open a client
   (Kitchen `10.42.2.38`, Living Room `10.42.2.39`) and set its stream/group to
   `navidrome` (the streams are `default`, `spotify`, `navidrome`). Multi-room
   works by grouping clients onto the same stream, or putting different clients
   on `navidrome` vs `spotify`.
2. **Play music from an MPD client** pointed at `10.42.2.37:6600`:
   - Android: **Symfonium** (paid, best) or **M.A.L.P.** (free).
   - Desktop: **Cantata** (GUI) or `ncmpcpp -h 10.42.2.37 -p 6600`.
   - The Navidrome library appears under a Subsonic/subidy browse root.
   Queue tracks and play; audio comes out of whatever HifiBerry is on the
   `navidrome` stream.

> **No sound after hitting play?** Almost always Step 1 — no speaker is assigned
> to the `navidrome` stream. The stream is idle (a snapserver "connect to pipe"
> log line) until an MPD client actually plays something; that's normal.

## 6. Testing
To verify Snapcast is working:
1. Navigate to the Snapweb UI and ensure it loads.
2. Connect a `snapclient` and verify it appears in the Snapweb UI.
3. Play music via Spotify Connect to the "Snapcast" device and verify audio plays on the connected client.
4. Verify the pod is running: `kubectl get pods -n snapcast-prod`

### Quick Test (Noise)
To test audio output without Spotify, you can pipe random noise into a test FIFO:
```bash
kubectl -n snapcast-prod exec deploy/snapcast -c snapserver -- sh -c 'cat /dev/urandom > /tmp/snapfifo'
```

## 7. Monitoring & Alerting
- **Metrics**: Snapcast does not expose Prometheus metrics natively.
- **Logs**: Check the pod logs for server errors or Spotify Connect issues:
  ```bash
  kubectl logs -n snapcast-prod deploy/snapcast -c snapserver
  kubectl logs -n snapcast-prod deploy/snapcast -c go-librespot
  ```

## 8. Spotify Connect: Initial Auth and Re-auth

go-librespot uses interactive OAuth (Spotify's PKCE flow). The one-time auth must be performed manually; credentials persist in the PVC afterwards and survive pod restarts.

### Initial authentication (or after PVC loss)

1. Port-forward the OAuth callback port from your Mac to the pod:
   ```bash
   kubectl port-forward -n snapcast-prod deploy/snapcast 57622:57622 &
   ```

2. Capture the auth URL from the go-librespot log:
   ```bash
   kubectl logs -n snapcast-prod deploy/snapcast -c go-librespot | grep "accounts.spotify.com"
   ```

3. Open the URL in your browser. Spotify will redirect to `http://127.0.0.1:57622/login?code=...` — the port-forward catches this callback.

4. Verify success:
   ```bash
   kubectl logs -n snapcast-prod deploy/snapcast -c go-librespot | grep "authenticated"
   kubectl exec -n snapcast-prod deploy/snapcast -c go-librespot -- cat /config/state.json
   ```
   `state.json` should have a non-empty `credentials.username` and `credentials.data`.

5. Kill the port-forward once done: `pkill -f "port-forward.*57622"`

**Notes:**
- The URL expires if the pod restarts. If the pod crashes during auth (it can happen if the callback arrives malformed), delete the pod to get a fresh URL: `kubectl delete pod -n snapcast-prod -l app=snapcast`
- `zeroconf_enabled: false` in the go-librespot config means the device registers via Spotify cloud, not mDNS. "Snapcast" appears in the Spotify "Devices Available" list without needing mDNS propagation.

## 9. Disaster Recovery
- **Backup Strategy**:
  - **Spotify State + Server State**: The `snapcast-spotify-state` PVC stores both go-librespot credentials (`state.json`) and snapserver's client/stream assignments (`snapserver/server.json`). Backed up via Synology Snapshot Replication.
  - **Config**: `snapserver.conf` and `go-librespot` config are in Git (ConfigMaps).
- **Restore Procedure**:
  1. Restore the `snapcast-spotify-state` LUN via Synology DSM if necessary.
  2. Re-deploy the Snapcast manifests. If the Spotify credentials are lost, redo the auth flow in Section 8. Client stream assignments will also be lost — clients reconnect to the `default` stream; use Snapweb or the JSON-RPC one-liner below to move them back to `spotify`:
     ```bash
     # Move a group to the spotify stream (get group IDs from Server.GetStatus first)
     curl -s http://10.42.2.37:1780/jsonrpc -H "Content-Type: application/json" \
       -d '{"id":1,"jsonrpc":"2.0","method":"Group.SetStream","params":{"id":"<group-id>","stream_id":"spotify"}}'
     ```

## 10. Troubleshooting

### Spotify Connect device "Snapcast" not showing up
- Verify go-librespot is running and not crash-looping: `kubectl get pods -n snapcast-prod`
- Check go-librespot logs for auth errors. If you see `"to complete authentication visit the following link"` on every startup, credentials were lost — redo the auth flow in Section 8.
- `zeroconf_enabled: false` means the device uses cloud registration, not mDNS. It should appear in Spotify's device list on any device logged into the same account without needing mDNS.

### No audio on clients (spotify stream is idle)
- Check which stream clients are assigned to:
  ```bash
  curl -s http://10.42.2.37:1780/jsonrpc -H "Content-Type: application/json" \
    -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' | python3 -m json.tool
  ```
  Clients should have `"stream_id": "spotify"`. If they show `"stream_id": "default"`, the server state was lost (PVC issue or first boot after PVC wipe). Use Snapweb or the `Group.SetStream` RPC call from Section 9 to reassign them.
- Verify the spotify stream status is `"playing"` (not `"idle"`) in the same output. If it's idle, go-librespot is not writing audio — check its logs.

### Playback keeps transferring away from "Snapcast" to "Kitchen" or "Living Room"
The HifiBerry OS devices (kitchen/living-room) ship with a native Spotify Connect implementation (Vollibrespot / HifiBerry's built-in service) that registers separate Spotify Connect devices named after the hostname. These compete with "Snapcast" for the active playback session.

When this happens, go-librespot logs: `"playback was transferred to Kitchen"`. Audio on the snapcast stream stops because go-librespot stops writing to the FIFO.

**Workaround**: Disable native Spotify on the HifiBerry devices. SSH in and check for a Vollibrespot or HifiBerry Spotify service:
```bash
ssh root@10.42.2.38 "systemctl list-units | grep -i spotify"
ssh root@10.42.2.38 "ps aux | grep -i librespot"
```
Stop/disable any conflicting service and verify only "Snapcast" appears in Spotify's device list.

If the native Spotify service cannot be cleanly disabled, as a workaround you can unregister it by deleting its credentials file (location varies by HifiBerry OS version — look in `/data/` or `/etc/`).

### No audio despite spotify stream showing "playing"
- Check client volumes — they default to ~28-30% after first connection. Use Snapweb to raise them.
- Verify the client is not muted in Snapweb.
- Check snapserver logs for FIFO read errors: `kubectl logs -n snapcast-prod deploy/snapcast -c snapserver`

### HA volume moves the client but nothing gets quieter

**Symptom:** the HA slider *does* change the Snapcast client volume server-side
(verifiable via `Server.GetStatus`), but the audible level does not change.

**Cause:** the node is running `snapclient --mixer software`, which attenuates
**only the Snapcast stream**. `go-librespot` is a separate process writing to
the same dmix, so whenever **Spotify Connect** is what is playing, the software
mixer changes nothing you can hear. Confirm who actually holds the DAC:

```bash
ssh root@<node> 'fuser -v /dev/snd/pcmC1D0p'
# root … go-librespot   -> Spotify is playing; the Snapcast mixer is irrelevant
```

**Fix:** drive the DAC's own mixer instead, which sits *under* every source on
the node. `hosts/dietpi-audio/` now detects this automatically
(`--mixer "hardware:<control>"`); older nodes may need it applied by hand.
A named PCM also needs a matching CTL — `ctl.snapdmix` alongside
`pcm.snapdmix` — or snapclient fails with `Invalid CTL snapdmix`.

⚠️ **The DAC's front-panel volume is a SEPARATE stage in series** with the USB
(UAC2) control, and the two multiply. Measured on both the D30 Pro and the
DX5 II: driving the UAC2 control does not move the front-panel reading at all.
Set the physical knob once as a ceiling; drive day-to-day level from HA.
Otherwise a slider at 100% can still be quiet, and nothing about that looks
broken.

**Historical note:** nodes with the HiFiBerry DAC+ DSP did not have this problem
— `snap-dsp-volume-bridge` set the SigmaDSP master, which sat beneath all
sources. Retiring the HAT removes that, and the DAC's own mixer is the
replacement.

### HA volume control stops working after swapping a Pi between rooms

**Symptom:** a room's volume slider in Home Assistant does nothing, or the card
looks greyed out, after the node for that room was replaced or re-flashed.

**Cause — two identifiers that drift apart.** The HA dashboard binds entities by
a *name-derived* ID:

```yaml
entity: media_player.living_room_snapcast_client
```

but the Snapcast integration keys each entity's `unique_id` on the **client ID,
which is the Pi's MAC address**. Snapserver likewise identifies clients by MAC,
not by hostname. So a new Pi registers as a **brand-new client that happens to
share the old one's name** — leaving two clients called `living-room`, one dead
and one live, and an HA entity still bound to the dead one.

⚠️ **MACs follow the Pi, not the SD card.** Moving a card between machines does
*not* carry the client identity with it. Worse, if Pi A used to be `living-room`
and later becomes `office`, its MAC is still the ID HA has recorded as
"living-room" — so an entity named for one room will control another.

**A second failure rides along with it:** a newly-registered client lands in its
own group on whatever stream is default, not the stream the room used to be on.
So even once the slider works, the room can be fed the wrong source.

**Fix — server-side first, UI second.**

```bash
SNAP() { kubectl -n snapcast-prod exec deploy/snapcast -c snapserver -- sh -c \
  "wget -qO- --post-data='$1' --header='Content-Type: application/json' \
   http://localhost:1780/jsonrpc"; }

# 1. Find duplicates: same name, one connected=false
SNAP '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' \
  | jq -r '.result.server.groups[]?.clients[]? | "\(.id)  \(.host.name)  connected=\(.connected)"'

# 2. Delete the stale client. Reversible -- clients re-register on connect,
#    losing only their stored volume/latency.
SNAP '{"id":2,"jsonrpc":"2.0","method":"Server.DeleteClient","params":{"id":"<old-mac>"}}'

# 3. Put the live client on the right stream (deleting clients re-shuffles groups)
SNAP '{"id":3,"jsonrpc":"2.0","method":"Group.SetStream","params":{"id":"<group>","stream_id":"spotify"}}'
```

Then in HA: **Settings → Devices & Services → Snapcast → ⋮ → Reload**. With the
stale client gone, the integration usually re-binds the existing entity to the
live client and nothing else is needed. Only if an orphan remains (shown
*Unavailable*) do you delete it and rename the live entity to the ID the
dashboard expects — the rename fails while the old entity still holds that ID.

**Verify by the loop, not the icon.** An entity can render perfectly and command
nothing. Move the slider and confirm the change server-side:

```bash
SNAP '{"id":4,"jsonrpc":"2.0","method":"Server.GetStatus"}' \
  | jq -r '.result.server.groups[]?.clients[]? | select(.connected==true) | "\(.host.name) \(.config.volume.percent)%"'
```

If the percentage moves, control is genuinely wired. An absent error icon proves
only that HA has an entity.

**Expect to repeat this on every hardware swap.** It is not a misconfiguration
to be fixed once — it is the predictable consequence of identity living in the
hardware while the dashboard refers to names.

### Audio sync issues
- Ensure all clients and the server have accurate NTP time synchronization.
- Adjust the latency offset for specific clients in the Snapweb UI if necessary.

## 11. Living room: DietPi + UR27 (as-built 2026-08-28)

The living room was rebuilt and **the HiFiBerry DAC+DSP HAT is gone**. Any doc
describing that room as Pi -> I2S -> HAT -> S/PDIF -> D30 Pro is stale.

| Stage | Was | Is now |
|---|---|---|
| OS | HiFiBerryOS + Docker extension | DietPi Trixie, `snapclient` native |
| Pi -> DAC | I2S to HAT, S/PDIF out to D30 Pro | **USB direct** to Topping **D90 III Discrete** |
| TV audio | TV optical -> HAT S/PDIF in | TV optical -> **HiFime UR27** (`SA9227`, ALSA card 0) -> `alsaloop` -> shared dmix |
| Volume | SigmaDSP master via `snap-dsp-volume-bridge` | the DAC's own UAC2 mixer, auto-detected (`--mixer hardware:'D90 III Discrete'`) |
| DSP / PEQ | SigmaDSP profile on the HAT | **none** — nothing replaced it in this room |

**Three sources now share one dmix**: `snapclient`, `go-librespot`, and the TV
capture. They mix rather than contend, and all three inherit the Home Assistant
volume because that drives the DAC's hardware mixer *underneath* all of them.

**Retiring the HAT dropped the room's DSP, and it has not been replaced.**
The D90 III has the hardware to do it, but the room is uncorrected today. The D30 Pro that first replaced the HAT has no PEQ, so the room
ran uncorrected. The D90 III has a 10-band PEQ (`EQ Max NUM:10`, read off the
device), applied on **every input except I2S**. It ships **disabled** — setup
item 12, register `0x9E sub 01` — so a loaded curve does nothing until it is
switched on. No curve is loaded yet: ten bands without a measurement are tone
controls, so this needs REW and a calibration mic before it means anything.

**Measured, and not worth re-litigating:**

- **A/V offset is ~75 ms** and does **not** respond to `alsaloop -t`, nor to the
  dmix buffer size. Halving dmix was predicted to save 22 ms and moved it 9 ms
  the *wrong* way. `buffer_size` is a ring capacity, not a fixed delay. The
  delay is upstream in the TV and/or capture hardware. Do not tune Pi buffers
  for lip-sync.
- `alsaloop -t 20000` is the lowest that ran clean: `5000` gave 22 underruns,
  explicit `-B 1024 -E 256` gave 9.
- **Sample-rate split is unresolved**: TV optical is 48 kHz, Navidrome/Spotify
  are 44.1 kHz, and dmix resamples one of them. Nobody has measured which, or
  what it costs.

**Addressing: the node is on `10.42.2.39`, but not because the node says so.**
`/etc/network/interfaces` is `inet dhcp` — there is no static address configured
on the box. `.39` comes from a router reservation. Both facts are true and only
one of them is visible from the node, which is how an earlier revision of this
document came to claim `.137` (a lease it briefly held mid-rebuild). If the
reservation is removed the node moves silently. See also section 10: a rebuilt
Pi does not carry its old Snapcast client identity.

### Volume on the D90 III: three stages, and only one should move

There are **three** attenuators in series in this room, and they multiply. Two
are on the DAC and are easy to mistake for one:

| Stage | Driven by | Survives a power cycle? |
|---|---|---|
| ALSA `index 0` (per-channel) | `snapclient` / Home Assistant | **No** — resets to max, but snapclient re-applies it on reconnect |
| ALSA `index 1` (master) | nothing should | **No** — resets to max |
| Device volume (front panel) | knob / IR remote | **Yes** — setup item 19 saves it with the menu settings |

**The two ALSA controls are the same USB Feature Unit** (Unit 10): `index 0` is
the L+R pair (`cmask=0x3`), `index 1` is that unit's *master* channel
(`cmask=0x0`). UAC combines them **additively in dB**, so setting both to -16
gives -32, not -16. Read them with:

```bash
amixer -c <card> sget 'D90 III Discrete',0     # per-channel — what HA drives
amixer -c <card> sget 'D90 III Discrete',1     # master — pin at 0 dB
```

Scale is 1/16 dB per step over -127..0 dB (2032 steps), so `-16 dB` is raw
`1776`. Prefer `sset ... -16dB` over raw values — `cset numid=N 0` sets raw 0,
which is **-127 dB**, not 0 dB. That mistake looks like a mute.

**The rule: one stage owns volume, the rest are pinned.** Here that is
`index 0` for Home Assistant, `index 1` pinned at `0 dB`, and the front-panel
volume set once as a ceiling. If anything else starts moving, every level in
the room is offset and nothing looks broken.

**The asymmetry matters after a power cut.** The device stage persists; the USB
stage comes back at maximum. What saves you is `snapclient` re-applying the
stored Snapcast volume when it reconnects — verified after a firmware update
and again after a setup-menu restart. If snapclient does *not* come back, the
room is at full output on the USB stage with nothing to catch it.

### PEQ: available, enabled, not yet driveable from code

The D90 III reports `EQ Max NUM:10` and PEQ is enabled on the device. Curves
are set with the **desktop Topping Tune** app (the web app at
`home.toppingaudio.com` does not support this model), and `Export`/`Import`
write curve files — so a curve belongs in this repo, with the device holding a
copy, not the other way round.

**Driving PEQ from `toppingctl` does not work yet, and the reason is not
established.** Writes reach the device and are echoed back with correct values,
PEQ is on, and still nothing is audible — a -20 dB notch parked on a 1 kHz test
tone produced no change. The leading theory is that the device holds five
built-in presets plus five custom slots (manual item 13) and our writes land in
a slot that is not the active one. Settling it needs the DAC on a bench beside
Topping Tune with the HID tap running, diffing vendor frames against ours.

⚠️ **Test audio changes with a locally generated tone, never through Spotify.**
`go-librespot` normalises, which rides the level on steady content and produced
several convincing false positives here before it was spotted:

```bash
systemctl stop go-librespot          # remove normalisation from the path
aplay -D snapdmix /tmp/tone1k.wav    # a locally generated sine
```

### Why the TV goes through the UR27 and not straight into the DAC

The obvious simplification is to drop the UR27 and feed the TV's optical
straight into the DAC: one box fewer, no `alsaloop`, no resample, and the
D90 III even auto-selects the live input (setup item 1, `SIG`). **It was tried
on 2026-08-28 and reverted, because it costs all software volume control of TV
audio.** Three independent confirmations, so do not spend another evening on it:

1. **No HID volume register.** Enumerating all 74 device fields while the front
   panel was moved from -0.5 dB to -20.0 dB showed no field tracking the change.
   Topping's own Tune app never touches `0x71` in 298 captured frames.
2. **The UAC2/ALSA mixer does not reach the optical input.** With TV audio
   playing over TOSLINK, cycling the USB stage between -20 dB and -55 dB four
   times was inaudible. That stage is on the USB path only.
3. **The TV cannot vary its own optical output.** The S95C manual, line 11172:
   *"If a device is connected via Optical, volume control may not be possible,
   depending on the device."* There is no "Digital Output Audio Volume" setting
   — only Format and Delay. Confirmed independently against Samsung community
   reports for this exact model.

Routing TV audio in over USB puts it in the same volume domain as everything
else, which is why the UR27 is load-bearing rather than transitional. The cost
is the ~75 ms offset documented above.

**The one path that would give both** is an IR emitter driven from Home
Assistant: the DAC's remote does control volume on any input. It is open-loop —
HA could send up/down but never read the level back — so the slider becomes a
pair of buttons. Untried.

**ARC/eARC is why soundbars do not have this problem.** HDMI-CEC System Audio
Control lets a TV remote drive a connected device's volume. The D90 III has no
HDMI input, so it cannot participate. This is a limitation of the connection
type, not of this room.

## 12. Network policy

The snapcast CNP uses `fromEntities: world` (not `fromCIDR: 10.42.2.0/24`) for ports 1704/1705/1780. This is intentional: Cilium SNATs LB traffic to a node IP before it reaches the pod, so a CIDR rule for the LAN subnet never matches. The security boundary is the LAN VLAN — `10.42.2.37` is unreachable from outside VLAN 2.

This is **current and applies to the DietPi nodes too** — it is a property of how
Cilium handles LoadBalancer traffic, not of what the client runs. It lived inside
the HiFiBerry section for historical reasons only. See also
`docs/operations/2026-08-15-networking-gotchas.md`.

## 13. HifiBerry Clients (kitchen / living-room) — SUPERSEDED

> ⚠️ **This section describes the previous architecture and is kept for history.**
> Both endpoints now run **DietPi (Debian)** with `snapclient` installed
> natively — not HiFiBerryOS, and not a Docker extension. Verified 2026-08-28:
> `kitchen` runs `/usr/bin/snapclient … --mixer "hardware:D50s "` as the
> unprivileged `snapclient` user, and `living-room` was rebuilt on DietPi Trixie
> with a USB DAC.
>
> **Current provisioning: `hosts/dietpi-audio/`** — one script provisions a node
> end to end (snapclient, go-librespot, shared dmix at the detected USB card
> index, udev re-detection, root SSH key, `toppingctl`). Design record:
> [lab `01-016`](https://github.com/gjcourt/lab/blob/main/01-audio-midi/01-016-diy-digital-domain-streamer.md).
>
> **The patched `ghcr.io/gjcourt/snapcast-hifiberry` image therefore has no
> consumers.** Check before deleting it — but it is maintenance surface being
> carried for nothing if both endpoints are DietPi.

Historically, two HifiBerry OS devices ran snapclient as a Docker extension:
- `kitchen` — `10.42.2.38`
- `living-room` — `10.42.2.39`

### Image (historical — no longer deployed)

The upstream HifiBerry extension image (`ghcr.io/hifiberry/extension_snapcast:0.28.0`) has two bugs that prevent snapclient from running. A patched image is maintained at `ghcr.io/gjcourt/snapcast-hifiberry` with a build pipeline in `images/snapcast-hifiberry/`. See `images/snapcast-hifiberry/README.md` for full details on the bugs and upgrade procedure.

**Known upstream bugs fixed by the patch image:**
1. Runtime audio libs missing from the final build stage — `libasound`, `libvorbis`, `libogg`, `libFLAC`, `libopus`, `libsoxr` are built in the compile stage but not installed in the runtime image.
2. Wrong binary path — `snapcastmpris.py` hardcodes `/bin/snapclient` but the binary lands at `/usr/local/bin/snapclient`. The patch adds a symlink.

### Device setup (historical — no longer deployed)

Each device *had*:
- `/data/extensions/snapcast/docker-compose.yaml` — extension config; references `ghcr.io/gjcourt/snapcast-hifiberry:<tag>`
- `/etc/snapcastmpris.conf` — INI file (no section header) with `server = 10.42.2.37` (the production LB VIP)

Status was checked with — **this no longer applies; both nodes run DietPi**:
```bash
ssh root@10.42.2.38 "docker exec snapcast ps aux"
# Should show: /usr/bin/python3 snapcastmpris.py AND /bin/snapclient -e -h 10.42.2.37
```

Images were rolled out with — **do not run this; there are no HiFiBerry nodes left**:
```bash
for ip in 10.42.2.38 10.42.2.39; do
  ssh root@$ip "
    docker pull ghcr.io/gjcourt/snapcast-hifiberry:<tag>
    sed -i 's|image: ghcr.io/gjcourt/snapcast-hifiberry:.*|image: ghcr.io/gjcourt/snapcast-hifiberry:<tag>|' /data/extensions/snapcast/docker-compose.yaml
    docker-compose -f /data/extensions/snapcast/docker-compose.yaml up -d
  "
done
```
