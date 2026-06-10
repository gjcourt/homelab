---
status: complete
last_modified: 2026-06-10
---

# Guest VLAN DNS Recovery + HifiBerry Speaker Access

> **Execution complete (2026-06-09 / 2026-06-10).** All three phases
> landed end-to-end:
>
> - **Phase A** — Guest DHCP DNS already on Cloudflare `1.1.1.2 / 1.0.0.2`
>   (malware-filtered) as a prior live fix; superset of the plan's
>   vanilla Cloudflare. Skipped formal Phase B (AdGuard migration)
>   tonight as a polish item — `1.1.1.2` is a reasonable terminal state.
>   _Update: Phase B executed shortly after — see below._
> - **Phase B** — UniFi firewall policy `Guest → AdGuard DNS` (TCP/UDP
>   53 to `10.42.2.43`, `10.42.2.45`) created, validated on a single
>   device with manual override, then enabled; Guest DHCP DNS flipped
>   back to AdGuard. AdGuard's filter confirmed active from CoS
>   (`doubleclick.net → 0.0.0.0`).
> - **Phase C** — UniFi firewall policy `Guest → HifiBerry speakers`
>   to `10.42.2.37` / `.38` / `.39` on TCP `80, 1705, 1780, 4070, 7000`
>   (port `4070` added after Spotify-Connect ephemeral-port debugging,
>   see Open items below). SSH (`:22`) explicitly excluded and
>   verified still blocked. mDNS reflector confirmed already enabled
>   on UCGF (`enable-reflector=yes`, `allow-interfaces` includes
>   `br6`, `_raop._tcp` + `_airplay._tcp` + `_spotify-connect._tcp`
>   in reflect filters). avahi `SIGHUP`'d once to clear stale cache.
>   iOS Spotify on Chamber of Secrets now sees `Kitchen` and
>   `Living-Room` essentially instantly.
>
> Plan's Open Items resolution:
> - Pinned Snapcast LB IP — verified `10.42.2.37` matches.
> - AdGuard service IPs — verified `10.42.2.43` / `.45` match.
> - mDNS reflector scope — confirmed cross-VLAN reflection works; no
>   noticeable clutter in iOS pickers from non-allowlisted services.
> - Logging volume — left enabled; will revisit if UCGF event log
>   floods.
> - **New gotcha surfaced during execution (not in original plan):**
>   `go-librespot`'s Spotify Connect zeroconf endpoint binds an
>   ephemeral high port by default (`43077` / `43991`). The plan's
>   AirPlay/web/Snapcast allow-list didn't cover it, so iOS Spotify
>   saw the mDNS announcement but couldn't complete the discovery
>   handshake. Fix: set `zeroconf_port: 4070` in both HifiBerries'
>   `/etc/go-librespot/config.yml`, restart `spotify.service`, add
>   `4070` to the firewall policy. See memory
>   `feedback_spotify_connect_ephemeral_port.md`.

> **Context:** The "Chamber of Secrets" SSID (Guest VLAN, `br6`,
> `10.42.6.0/24`) lets clients associate and obtain a DHCP lease but
> appears to have "no internet." Confirmed root cause: the Guest network
> has `network_isolation_enabled: true`, which the UCGF translates into
> auto-generated UBIOS isolation rules (`UBIOS_LAN_GUEST_USER` chain).
> DHCP hands out AdGuard at `10.42.2.43` / `.45` (Lab VLAN) as the DNS
> servers, but the isolation rules drop every Guest → Lab packet. WAN
> egress works (clients can ping `8.8.8.8`); DNS resolution fails; the
> apparent symptom is "no internet."
>
> This plan ships the fix in two phases. Phase A is the immediate
> unblock (point Guest at public DNS); Phase B is the staged migration
> back to AdGuard via a narrow firewall exception. Phase C adds a
> separate set of exceptions so guests can control the HifiBerry
> speakers.

## Phases at a glance

| Phase | What changes | Reversible? | Disruption |
|---|---|---|---|
| A | Guest DHCP DNS → `1.1.1.1, 1.0.0.1` | Yes — flip back in UI | None for already-working clients; Guest clients gain working DNS |
| B.1 | Add disabled firewall policy: Guest → AdGuard:53 | Yes — delete policy | None |
| B.2 | Test policy on a single device (manual DNS override) | Yes — revert override | Single test client only |
| B.3 | Enable policy, swap Guest DHCP DNS back to AdGuard | Yes — disable policy and revert DHCP | Guest clients pick up new DNS on next lease renewal |
| C.1 | Add firewall exceptions for HifiBerry control | Yes — disable/delete policy | None until enabled |
| C.2 | (Optional) Enable mDNS reflector for cross-VLAN discovery | Yes | Mild — touches mDNS for whole house |

## Constraints

1. **Reversible per phase.** Each phase has explicit rollback.
2. **Test before broad rollout.** Phase B's policy is created disabled and validated on a single device before being enabled and DHCP is flipped.
3. **Network isolation stays on.** The point of the Guest VLAN is to keep guests off the Lab/Family/etc. networks. We add narrow exceptions, never disable isolation.
4. **No SSH access for guests.** HifiBerry exceptions in Phase C cover control protocols only (HTTP, AirPlay, Snapcast); SSH (TCP 22) is explicitly excluded.

---

## Phase A — Quick fix: Guest uses public DNS

**Risk:** very low. Single config change, fully reversible.

### A.1 Apply

UniFi UI: **Settings → Networks → Guest → Advanced → DHCP Service Management → DNS Server** → set to:

```
1.1.1.1
1.0.0.1
```

(Cloudflare. Equivalent: `8.8.8.8, 8.8.4.4` for Google.)

Save. The UCGF reloads `dnsmasq.dhcp.conf.d/` automatically; new leases get the new DNS, existing leases keep the old DNS until they renew.

### A.2 Force renewal on a test device

To pick up the new DNS without waiting for the lease half-life:

```bash
# On a connected guest device (macOS):
sudo dhclient -r en0 && sudo dhclient en0
# Or simpler: toggle Wi-Fi off then on
```

### A.3 Verify

From a Guest-WiFi-connected device:

```bash
# DNS query against the new server
nslookup google.com 1.1.1.1   # Should resolve

# Full-stack test
curl -sI https://example.com   # Should return 200
```

### Phase A GO criteria

- Guest client gets DHCP DNS = `1.1.1.1, 1.0.0.1` on `cat /etc/resolv.conf` (or equivalent).
- HTTPS to a public host works.

### Phase A rollback

UI: revert DNS field to `10.42.2.43, 10.42.2.45`. Trade-off: returns to the broken state until Phase B is in place.

> **Trade-off accepted at end of Phase A:** Guest clients are out of the AdGuard ad/tracker filter. Acceptable for an explicitly-named guest network; revisited in Phase B.

---

## Phase B — Migrate to AdGuard with a narrow exception

**Risk:** low. The policy is created **disabled** and validated on a single device before being made authoritative.

### B.1 Create the firewall policy (disabled)

UniFi UI: **Settings → Security → Firewall → Add Policy**

- **Name:** `Guest → AdGuard DNS`
- **Status:** **Disabled** (intentional — we'll enable in B.3)
- **Action:** Allow
- **Source:** Zone Internal, network "Guest" (the `10.42.6.0/24` network)
- **Destination:** Specific hosts `10.42.2.43`, `10.42.2.45`
- **Protocol:** TCP and UDP
- **Destination Port:** `53`
- **Logging:** Enabled (helps verification)

Save. No traffic effect yet because the policy is disabled.

### B.2 Test the policy on a single device

This verifies the rule is correctly written **before** swapping DHCP back to AdGuard for everyone.

1. **Manually override DNS** on one Guest-connected device to point at AdGuard only:
   - macOS: System Settings → Wi-Fi → Chamber of Secrets → Details → DNS → set servers to `10.42.2.43`, `10.42.2.45`. Click OK.
   - Linux: `sudo resolvectl dns wlan0 10.42.2.43 10.42.2.45`
2. **Confirm DNS fails (policy still disabled):**
   ```bash
   nslookup google.com 10.42.2.43
   # Expected: timeout or "no servers could be reached"
   ```
   This proves the isolation rules are still active. If it succeeds, isolation is not what we thought; stop and re-diagnose.
3. **Enable the policy** in the UniFi UI (toggle on the `Guest → AdGuard DNS` policy created in B.1).
4. **Re-test DNS:**
   ```bash
   nslookup google.com 10.42.2.43
   # Expected: returns an A record
   curl -sI https://example.com
   # Expected: 200 OK
   ```
5. **Disable the policy again, re-test:** DNS should fail again. This sanity-checks that the policy is what's making the difference (rules out coincidence / cache).
6. **Re-enable** before moving on.
7. **Remove the manual DNS override** from the test device.

### B.3 Cut DHCP DNS over to AdGuard

UniFi UI: **Settings → Networks → Guest → Advanced → DHCP Service Management → DNS Server** → set back to:

```
10.42.2.43
10.42.2.45
```

Force renewal on a fresh test device, repeat the verification:

```bash
sudo dhclient -r en0 && sudo dhclient en0
cat /etc/resolv.conf   # Should show 10.42.2.43, 10.42.2.45
nslookup google.com    # Should resolve via AdGuard
curl -sI https://example.com   # 200 OK
```

### Phase B GO criteria

- The `Guest → AdGuard DNS` policy is enabled.
- Test client receives AdGuard DNS via DHCP and resolves successfully.
- Disabling the policy reproducibly breaks DNS (confirms the policy is load-bearing).
- Browsing on the Guest network shows AdGuard's filter behavior on a known-blocked test domain (e.g. a tracker).

### Phase B rollback

Two reversible steps depending on what failed:
- **DNS broken after B.3:** revert DHCP DNS to `1.1.1.1, 1.0.0.1` (back to Phase A state).
- **Policy is causing collateral issues:** disable the policy. DNS will break for clients still pointing at AdGuard; combine with the DHCP revert above.

---

## Phase C — Allow Guest control of HifiBerry speakers

**Goal:** guests connected to "Chamber of Secrets" can play music on the kitchen and living-room HifiBerry speakers without losing the rest of the Guest VLAN's isolation.

### What "control" means — pick the protocol(s)

HifiBerry units (`10.42.2.38` kitchen, `10.42.2.39` living-room) currently expose:
- TCP `22` — SSH (do **not** allow from guests)
- TCP `80` — HifiBerryOS web UI (per-speaker control)
- TCP `7000` — AirPlay (Shairport-Sync target)

The Snapcast control surface lives in the cluster, not on the HifiBerries:
- `10.42.2.37:1780` — Snapcast web UI (multi-room control)
- `10.42.2.37:1705` — Snapcast JSON-RPC

| Use case | Destination | Ports |
|---|---|---|
| Guest casts from iOS via AirPlay | `10.42.2.38`, `10.42.2.39` | TCP `7000` |
| Guest opens HifiBerryOS web UI per speaker | `10.42.2.38`, `10.42.2.39` | TCP `80` |
| Guest controls all rooms via Snapcast Web | `10.42.2.37` | TCP `1780`, `1705` |

Most "guest comes over and wants to play music" flows are covered by **AirPlay alone**. Add the others if they're part of your actual workflow.

### C.1 Create the speaker firewall policy

UniFi UI: **Settings → Security → Firewall → Add Policy**

- **Name:** `Guest → HifiBerry speakers`
- **Status:** Disabled (test before enabling)
- **Action:** Allow
- **Source:** Zone Internal, network "Guest"
- **Destination:** Specific hosts (pick the ones you want):
  - `10.42.2.38`, `10.42.2.39` for direct speaker control / AirPlay
  - and/or `10.42.2.37` for Snapcast multi-room
- **Protocol:** TCP
- **Destination Port:** the relevant subset from the table above
  - Recommended starting point: TCP `7000` to `.38`/`.39` (AirPlay only)
- **Logging:** Enabled

> **Do not include port 22.** Don't grant SSH to anything from the Guest VLAN. Keep destination port allow-list explicit.

### C.2 Test before enabling

Same pattern as B.2, but using `nc` for a TCP-level connectivity test instead of relying on DNS:

1. **Confirm currently blocked** (policy disabled):
   ```bash
   # On a Guest-connected device:
   nc -zv 10.42.2.38 7000
   # Expected: timeout / connection refused
   ```
2. **Enable the policy.**
3. **Re-test:**
   ```bash
   nc -zv 10.42.2.38 7000
   # Expected: succeeds
   ```
4. From an iOS device on the Guest network, open the AirPlay target picker — the kitchen/living-room HifiBerry should be selectable (assuming mDNS reflector is configured per Phase C.3 below; without it, AirPlay discovery will not work).
5. **Disable, re-test fail, re-enable.** Sanity check the policy is what's making the difference.

### C.3 (Optional) Enable mDNS reflector

AirPlay (and most casting/streaming protocols) discover targets via multicast DNS on `224.0.0.251:5353`. Multicast doesn't traverse VLAN boundaries unless the gateway reflects it. Without the reflector, guests must enter the IP manually — annoying for AirPlay (no manual-entry path on iOS) but workable for browser-based control.

UniFi UI: **Settings → Networks → "Multicast DNS"** (location varies by firmware). Enable mDNS for the **Guest** network and the **Lab** network (where the speakers live).

> **Compatibility note:** UCG-Fiber's mDNS reflector has historically had quirks with `_homekit._tcp` advertisements. AirPlay (`_raop._tcp`, `_airplay._tcp`) is generally well-supported. Test before relying on it.

### Phase C GO criteria

- Guest-connected device can open a TCP connection to the chosen ports/hosts (`nc -zv` or equivalent).
- iOS AirPlay picker shows the speakers (only if C.3 is also done).
- Guest device cannot open new connections to ports outside the allow-list (e.g. `nc -zv 10.42.2.38 22` still fails).
- Guest device cannot reach other Lab-VLAN hosts (e.g. `nc -zv 10.42.2.10 22` fails — hestia stays inaccessible).

### Phase C rollback

- Disable the `Guest → HifiBerry speakers` policy. Speakers immediately drop off the Guest VLAN.
- (If C.3 was applied) disable the mDNS reflector for the Guest network if it caused unexpected service advertisements.

### C.4 Cilium NetworkPolicy — no change required (but verify)

The Snapcast pod sits behind a `CiliumNetworkPolicy`
(`apps/base/snapcast/networkpolicy.yaml`). Cilium SNATs external
traffic to a node IP before the pod sees it, which means `fromCIDR:
10.42.6.0/24` (Guest VLAN) would silently never match. PR #494 already
addressed this by using `fromEntities: world` for ingress on ports
`1704`/`1705`/`1780`, so **Guest traffic that's permitted by the UniFi
firewall in C.1 will be accepted by the cluster CNP without further
changes**.

Two implications:

1. The boundary for "who can hit Snapcast" is the UniFi firewall —
   not the CNP. The plan must keep the UniFi policy narrow (specific
   Guest network as source, specific ports as dest) because the CNP
   no longer reduces blast radius for LAN-sourced traffic.
2. If a future hardening pass attempts to re-tighten the CNP to a
   CIDR list, it will break LAN snapclients. See
   `feedback_cilium_lb_snat_pattern.md` (memory) and the PR #494
   commit message for the rationale.

HifiBerry speakers `10.42.2.38` / `.39` are not Kubernetes
endpoints — they sit on the LAN and have no CNP at all, so this
section only applies to the Snapcast control surface
(`10.42.2.37:1780/1705`).

**Verify** after C.1 + C.2 lands: from a Guest-connected device,
`nc -zv 10.42.2.37 1780` should succeed and the Snapcast Web UI
should load in a browser. If it fails despite the UniFi rule being
on, inspect the CNP — somebody may have reverted to a CIDR-based
policy in the interim.

---

## Verification matrix (run end-to-end after each phase lands)

| Test | After A | After B | After C (AirPlay-only) |
|---|---|---|---|
| `ping 8.8.8.8` from Guest device | ✓ | ✓ | ✓ |
| `nslookup google.com` (default DNS) | ✓ | ✓ | ✓ |
| `nslookup google.com 10.42.2.43` | ✗ | ✓ | ✓ |
| `nc -zv 10.42.2.38 7000` (AirPlay) | ✗ | ✗ | ✓ |
| `nc -zv 10.42.2.38 22` (SSH — must still fail) | ✗ | ✗ | ✗ |
| `nc -zv 10.42.2.10 22` (hestia SSH — must still fail) | ✗ | ✗ | ✗ |
| AdGuard filter active for guest browsing | ✗ | ✓ | ✓ |
| iOS AirPlay picker shows kitchen/living-room | depends on mDNS | depends on mDNS | ✓ if C.3 done |

---

## Notes on the underlying mechanism

The UniFi 9+ "Network Isolation" toggle on a network creates auto-generated zone-based firewall rules under the hood, surfaced as the `UBIOS_LAN_GUEST_USER` chain in `iptables -L`. These rules don't appear as user-editable items in the `firewall_policy` mongo collection — they're synthesized at apply-time. Adding an "Allow" policy in `Settings → Security → Firewall` creates an explicit `firewall_policy` row that's evaluated **before** the auto-generated isolation deny, which is why a narrow allow lets specific traffic through without disabling the broader isolation.

To inspect the live state on the UCGF:

```bash
ssh root@10.42.2.1 'iptables -L UBIOS_LAN_IN_USER -n -v | head -30'
ssh root@10.42.2.1 'mongo --port 27117 --quiet ace --eval "db.firewall_policy.find({}).pretty()"'
```

---

## Out of scope

- **Per-device guest authentication / captive portal.** Current model is shared PSK on Chamber of Secrets. If you want per-guest credentials and time-bounded access, switch the network's `purpose` to `guest` and set up the UniFi guest portal — separate plan.
- **Spotify Connect / DLNA from Guest VLAN.** If specific use cases beyond AirPlay/web come up, extend Phase C's port list.
- **Replacing the Guest VLAN with a hardened IoT VLAN for HifiBerries.** Tracked separately in `docs/plans/2026-05-06-network-resilience-and-bgp-completion.md` Phase F.1.

---

## Cross-references

- `docs/plans/2026-05-03-snapcast-hifiberry-rollout.md` — the server-side
  rollout: how the Snapcast LB IP gets advertised on the LAN (Cilium
  L2 announcements via `home-c-pool`), how the HifiBerries are wired
  as snapclients, and the Phase 1 pin of `10.42.2.37` as the canonical
  client target. This plan piggybacks on that LB IP for Snapcast
  control access from the Guest VLAN.
- `docs/plans/2026-03-14-navidrome-snapcast-mopidy.md` — the parallel
  stream-sources plan; once Mopidy lands, guests with Snapcast Web
  access can also drive Navidrome playback.
- `apps/base/snapcast/networkpolicy.yaml` — the CNP that uses
  `fromEntities: world` for ports 1704/1705/1780 (see Phase C.4).
- `docs/architecture/networking/addressing.md` — VLAN map + DHCP
  ranges; confirms Guest VLAN is `br6` / `10.42.6.0/24`, HifiBerries
  on `10.42.2.0/24` Lab VLAN.

---

## Open items to resolve mid-execution

- **Pinned Snapcast LB IP**: this plan assumes `10.42.2.37` as the
  Snapcast control surface address. The snapcast-hifiberry-rollout
  plan's Phase 1 pins this via
  `lbipam.cilium.io/ips`. Confirm the actually-assigned IP with
  `kubectl get svc -n snapcast-prod snapcast -o wide` before
  hard-coding it in the UniFi firewall policy in Phase C.1. If
  rollout-Phase-1 hasn't merged yet, the IP can drift on cluster
  rebuild and the UniFi policy will silently start denying traffic.
- **AdGuard service IPs (`10.42.2.43` / `.45`)**: same risk. These
  come from `home-c-pool` (cluster LB pool). If either gets re-IPed
  during a cluster rebuild, Phase B's UniFi exception becomes a deny.
  Pin them too, or add a documented step in the AdGuard runbook to
  update this policy on IP change.
- **mDNS reflector scope**: the UCG-Fiber's reflector advertises *all*
  services it sees, not a subset. Enabling it for Guest may surface
  Lab-VLAN devices (printers, NAS, HomeKit bridge) in iOS pickers
  even though the firewall still blocks the underlying connections.
  Cosmetic clutter, not a security hole — note in operator handoff.
- **Logging volume**: Phase C.1 enables UniFi logging on the speaker
  policy. With AirPlay's chatty TCP behaviour, this can flood the
  UCGF event log. Plan to disable logging once Phase C is GO unless
  active monitoring is needed.
