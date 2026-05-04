---
status: planned
last_modified: 2026-05-03
---

# Snapcast — HifiBerry client rollout

Wire the two HifiBerry devices on the LAN as `snapclient` clients of the in-cluster `snapcast` server, so audio queued via the `navidrome` / `spotify` / future streams plays in the kitchen and living room.

## Context

Today, the HifiBerries (`kitchen` at `10.42.2.38`, `living-room` at `10.42.2.39`) run **`vollibrespot`** as their Spotify Connect endpoint. Audio plays locally on each device when a Spotify client casts to it.

The cluster-side `snapcast` Service (`apps/base/snapcast/`, namespace `snapcast-prod`) is a `LoadBalancer` annotated with `lbipam.cilium.io/ip-pool: home-c-pool`, with stream sources for Spotify (via the `go-librespot` sidecar) and — once PR #426 lands — Mopidy/Navidrome. Cilium L2 announcements (`l2-announcement-policy-staging` in `kube-system`) advertise the LB IP onto the LAN segment via gratuitous ARP.

What's missing is the client side: the HifiBerries don't yet have `snapclient` configured to subscribe to the cluster's `snapserver`. Once they do, every cluster stream becomes a multi-room, time-synchronized output.

## Goal

`snapclient` running on both HifiBerries, bound to a stable `snapserver` LAN IP, visible in Snapweb (`https://snapcast.burntbytes.com`), with audio playing in sync.

## Non-goals

- Replacing `vollibrespot` on the HifiBerries. The existing local Spotify Connect path stays as a fallback.
- Switching multi-room control away from Snapweb. The plan lands the clients; Snapweb continues to be the user-facing controller.

## Phases

### Phase 0 — Diagnose (operator)

Establish current ground truth before changing anything. Three checks; each rules out one failure mode.

```bash
# 1. Does snapcast have an LB IP?
kubectl get svc -n snapcast-prod snapcast -o wide
# Expect: EXTERNAL-IP populated, e.g. 10.42.2.4X. Note the IP.

# 2. Is Cilium L2 actually advertising it?
#    Look for the leader-election lease on the snapcast service.
kubectl get lease -n kube-system | grep -i snapcast
kubectl get ciliumloadbalancerippool home-c-pool -o yaml | grep -A3 "blocks:"

# 3. From a HifiBerry, can the LB IP be reached?
ssh root@10.42.2.38 "ping -c 3 <snapcast-EXTERNAL-IP> && nc -zv <snapcast-EXTERNAL-IP> 1704"
ssh root@10.42.2.39 "ping -c 3 <snapcast-EXTERNAL-IP> && nc -zv <snapcast-EXTERNAL-IP> 1704"
```

Outcomes drive the rest of the plan:

| Check 1 | Check 2 | Check 3 | Most likely problem | Phase to focus on |
|---|---|---|---|---|
| ✅ IP | ✅ Lease | ✅ Reachable | Network is fine; client isn't installed | Phase 2 |
| ✅ IP | ✅ Lease | ❌ Unreachable | UniFi VLAN / firewall isolating | Phase 0.5 (UniFi) |
| ✅ IP | ❌ No lease | — | L2 announcement broken | Phase 0.5 (Cilium) |
| ❌ No IP | — | — | Cilium IPAM exhausted or pool misconfigured | Phase 0.5 (IPAM) |

### Phase 0.5 — Resolve diagnostic blockers (only if Phase 0 finds problems)

#### If UniFi network blocking
- Confirm the cluster nodes (`10.42.2.20-25`) and the HifiBerries (`.38`, `.39`) are on the **same LAN/VLAN** in the UniFi controller (UCG-Fiber → Networks).
- If they're on separate networks, either move the HifiBerries onto the cluster's network, or add a UniFi firewall rule allowing the relevant ports (TCP 1704/1705/1780, UDP/TCP for ARP if cross-VLAN).
- L2 announcements (gratuitous ARP) **do not cross VLANs** — if the topology requires it, switch to BGP via the `bgp-rollout` plan, or assign the HifiBerries a static route to the snapcast IP.

#### If Cilium L2 announcement leader missing
- `kubectl logs -n kube-system -l k8s-app=cilium --tail=200 | grep -i "l2\|announce\|snapcast"` for clues.
- Confirm `infra/configs/cilium/l2-announcement-policy.yaml` covers the snapcast service (currently it has `serviceSelector` commented out, so it should select all LB services).
- Force a reconcile: `flux reconcile kustomization infra-configs -n flux-system`.

#### If no IP assigned
- `kubectl describe svc -n snapcast-prod snapcast` — look for IPAM events.
- Confirm `home-c-pool` has free IPs: `kubectl get ciliumloadbalancerippool home-c-pool -o yaml | grep -A5 "status:"`.
- Pool spans `10.42.2.40-254`; with adguard primary at `.43`, adguard secondary at `.45`, gateways at `.40` and `.42`, plenty of headroom.

### Phase 1 — Pin the snapcast LB IP (IaC)

Once Phase 0 confirms the current IP, pin it so HifiBerry config can hardcode without worrying about reassignment after a cluster rebuild or service recreate. Add to `apps/base/snapcast/service.yaml`:

```yaml
metadata:
  annotations:
    lbipam.cilium.io/ip-pool: home-c-pool
    lbipam.cilium.io/ips: "10.42.2.<NN>"   # <-- set to the current EXTERNAL-IP from Phase 0
```

Use the **current** assigned IP. Don't pick a different one — that would briefly drop service for any existing snapclients.

This is a one-line PR. Validate with `kustomize build apps/base/snapcast/`, `apps/staging/snapcast/`, `apps/production/snapcast/`. Merge.

### Phase 2 — Configure `snapclient` on each HifiBerry

HifiBerry OS ships with `snapclient` available via the **beocreate** `extension_snapcast` package. Two installation paths:

#### Path A — beocreate UI (preferred)
1. Open the device's beocreate UI: `http://10.42.2.38/` (kitchen) and `http://10.42.2.39/` (living-room).
2. Settings → **Sources** → enable **Snapcast** if not already.
3. Set **Server** to the pinned IP from Phase 1, port `1704`.
4. Save. The device should appear in `https://snapcast.burntbytes.com` within a few seconds.

#### Path B — direct systemd unit (fallback if beocreate UI is broken)
HifiBerry OS uses `snapclient.service`. SSH to the device:

```bash
ssh root@10.42.2.38 'cat > /etc/default/snapclient' <<EOF
SNAPCLIENT_OPTS="-h 10.42.2.<NN> -p 1704 --hostID kitchen --logsink null"
EOF
ssh root@10.42.2.38 'systemctl restart snapclient.service'
ssh root@10.42.2.38 'systemctl status snapclient.service --no-pager | head -20'
```

Repeat for `10.42.2.39` with `--hostID living-room`.

The `--hostID` flag gives each client a stable identifier in Snapweb regardless of MAC changes (the default is the device's MAC).

#### Optional: Ship a config script in this repo
If Path B is the operational path, drop `scripts/hifiberry/snapclient.env` (the env file) and `scripts/hifiberry/install-snapclient.sh` (a thin install wrapper) under `scripts/hifiberry/` — same pattern as the existing `beocreate-watchdog.sh`. The current plan defers this to Phase 5; if Path A works, no scripts are needed.

### Phase 3 — Verify each client in Snapweb (operator)

1. Browse to `https://snapcast.burntbytes.com`.
2. Both `kitchen` and `living-room` should appear under the connected clients list.
3. Adjust their group/stream assignment via the UI:
   - Default group → `default` stream (`/tmp/snapfifo` — the main mix).
   - Or assign one to `spotify`, the other to `navidrome` (after PR #426 lands), to test independent streams.
4. Send audio: cast a Spotify track to the `Snapcast` device (the cluster's go-librespot zeroconf service), and confirm playback on the assigned HifiBerry.

### Phase 4 — Multi-room sync test (operator)

1. Assign both HifiBerries to the same group + stream.
2. Cast a track. Both rooms should play **bit-identical, time-synchronized** audio. Snapcast's whole point is sample-accurate sync; if the output is detectably out of phase, check NTP on each device (`timedatectl status`) — drift of more than a few ms ruins the effect.
3. Move into different rooms; confirm no perceptible delay between them.

### Phase 5 — Documentation (IaC, can land with Phase 1 or separately)

Update `docs/operations/apps/snapcast.md` to add:
- The pinned LB IP as the canonical "client target" address.
- A "HifiBerry clients" section that summarizes Phase 2 (Path A primary, Path B fallback).
- A "Multi-room verification" snippet.

Optional in this phase: write `docs/operations/hifiberry-os-snapclient-setup.md` mirroring the structure of the existing `hifiberry-os-spotify-setup.md` and `hifiberry-os-watchdog.md`. Deferred for now; merge if the operator goes Path B in Phase 2.

## Risks / mitigations

- **L2 announcements are sticky to one node.** Cilium leader-election binds the LB IP advertisement to one of the worker nodes. If that node fails, the IP migrates within seconds — but during that window snapclients reconnect. Acceptable for audio.
- **Cluster rebuild reassigns the IP.** Mitigated by Phase 1 (pin via `lbipam.cilium.io/ips`).
- **VLAN topology drift.** If the operator ever moves the cluster to a separate VLAN from the HifiBerries, Phase 0 has to be re-run — L2 ARP doesn't cross. The `bgp-rollout` plan would resolve this end-to-end if it lands.
- **HifiBerry OS upgrades replace `/etc/default/snapclient`.** Path B writes that file; HifiBerry OS major version upgrades may overwrite it. Path A (beocreate UI) survives upgrades because it persists in beocreate's config DB.

## Verification checklist

- [ ] Phase 0 diagnostic completed; current snapcast LB IP recorded.
- [ ] Phase 0.5 resolved any blockers (or confirmed none).
- [ ] Phase 1 IaC pin merged; `kubectl get svc -n snapcast-prod snapcast` shows the pinned IP.
- [ ] `kitchen` reachable at `10.42.2.38` via SSH and beocreate UI.
- [ ] `living-room` reachable at `10.42.2.39` via SSH and beocreate UI.
- [ ] Both clients appear in `https://snapcast.burntbytes.com`.
- [ ] Audio plays on each via Spotify Connect cast.
- [ ] Both clients in sync when grouped and casting.
- [ ] Plan flipped to `complete`.

## Cross-references

- `docs/plans/2026-03-14-navidrome-snapcast-mopidy.md` — companion plan; Mopidy sidecar adds the `navidrome` stream that the HifiBerries will consume after PR #426 lands.
- `docs/plans/2026-03-08-bgp-rollout.md` — would replace L2 announcements with BGP, eliminating the VLAN-coupling caveat.
- `docs/operations/hifiberry-os-spotify-setup.md` — historical context for the avahi-on-Docker-bridge problem; the snapclient rollout doesn't have the same trap because we hardcode the IP rather than rely on mDNS.
- `apps/base/snapcast/` — server-side IaC.
- Source prompt: `~/src/config/prompts/2026-05-02-snapserver-home-network.md`.
