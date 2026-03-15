# Incident: Periodic Subnet-Wide Ethernet Blackouts — STP TCN Storm

**Date:** 2026-02-28 — 2026-03-10
**Status:** Resolved
**Severity:** High — all devices on `10.42.2.0/24` suffering ~40s periodic blackouts
**Duration:** ~10 days (first observed ~2026-02-28, root cause resolved 2026-03-10)
**Environments affected:** Physical LAN (`10.42.2.0/24`)
**Authors:** Copilot

---

## Summary

All devices on the `10.42.2.0/24` subnet experienced periodic ~40–60 second connectivity
blackouts. During each episode the affected MAC addresses could not be reached from any
other device on the subnet, even though every device's physical link (carrier) remained
stable. The Kubernetes cluster, Raspberry Pi HifiBerry nodes, and the Talos node were
all affected simultaneously.

**Root cause:** The UniFi Flex 2.5G PoE switch (firmware 2.1.8) had a firmware bug that
caused it to emit STP Topology Change Notifications (TCNs) autonomously — at ~30–60s
intervals — with no connected devices and no actual topology change. Each TCN caused all
switches in the spanning tree to flush their MAC address tables, making every device on
the subnet temporarily unreachable for 15–40s while MAC addresses were re-learned.

**Contributing factor:** The Raspberry Pi 4's `bcmgenet` NIC enables Energy Efficient
Ethernet (EEE) by default. EEE causes brief PHY Low Power Idle (LPI) micro-sleeps that
some UniFi switch firmware versions interpret as link-down events, generating additional
TCNs. This was the original suspected cause and remained a valid defense-in-depth fix
after the primary cause was identified.

---

## Affected Services

| Service / Host | Impact |
|---|---|
| All hosts on `10.42.2.0/24` | ~40–60s blackouts every 30–60s, outbound pings unaffected |
| Kubernetes cluster (`10.42.2.20`) | API server unreachable during blackouts; Flux reconcile spikes |
| `kitchen` HifiBerry (`10.42.2.38`) | SSH drops, Snapcast client disconnects |
| `living-room` HifiBerry (`10.42.2.39`) | SSH drops, Snapcast client disconnects |
| `kitchen-pi` (`10.42.2.143`) | SSH drops, NM DHCP renewal failures |
| DHCP clients generally | First DHCP attempt after a blackout always timed out (45s) |

---

## Timeline

| Time | Event |
|------|-------|
| ~2026-02-28 | First reports of ~40s connectivity blackouts on `10.42.2.0/24` |
| 2026-03-06 | Confirmed one-way packet loss; Pi can ping gateway but Mac cannot ping Pi |
| 2026-03-07 | Confirmed subnet-wide scope — multiple devices drop anti-correlated (not isolated to one host) |
| 2026-03-08 | Identified STP TCN as root cause via DHCP log analysis and MAC flush signature |
| 2026-03-08 | Applied EEE disable on `kitchen-pi` (`10.42.2.143`) — reduced frequency but did not stop blackouts |
| 2026-03-08 | Set STP bridge priority to `4096` on Pro XG 48 PoE — **no effect** |
| 2026-03-09 | Factory reset Flex 2.5G PoE, connected only uplink — autonomous TCNs confirmed |
| 2026-03-10 | Removed Flex 2.5G PoE from UniFi UI — **blackouts stopped immediately** |

---

## Root Cause Analysis

### How STP TCNs Cause Subnet-Wide Blackouts

IEEE 802.1D Spanning Tree Protocol defines a TCN (Topology Change Notification) as the
mechanism switches use to signal that a forwarding topology has changed. When any switch
sends a TCN toward the root bridge, the **root bridge sets MAC address aging for the
entire spanning tree to `ForwardDelay` (15s) and flushes MAC tables on all switches.**

This means that even a single TCN from a switch at the edge of the network causes every
switch in the tree to simultaneously evict all learned MAC addresses, making every device
on every VLAN temporarily invisible until ARP/MAC re-learning completes. With a faulty
switch generating TCNs every 30–60s, this created the observed periodic ~40s blackouts.

```
Flex 2.5G PoE (firmware bug)
        │
        │  TCN every 30–60s
        ▼
Pro XG 48 PoE (root bridge)
        │
        │  "set aging = ForwardDelay, flush all MACs"
        ▼
All switches flush MAC tables
        │
        ▼
Subnet-wide ARP failure for ~15–40s
        │
        ▼
MACs re-learned → connectivity restored
```

### Root Cause 1: Flex 2.5G PoE Firmware Bug (Primary)

The UniFi Flex 2.5G PoE running firmware **2.1.8** generated STP TCNs autonomously,
independent of any connected device. This was proven definitively:

1. Factory reset the switch (eliminating any config artifact)
2. Connected only the uplink cable — no other devices
3. TCNs continued at the same ~30–60s interval with the switch otherwise completely idle
4. Removed the switch from the UniFi UI → blackouts stopped **within seconds**

Setting bridge priority to `4096` on the Pro XG 48 PoE to make it the explicit root
bridge had **no effect.** The Flex 2.5G PoE was generating TCNs regardless of which
switch was the root, because the TCNs were caused by internal firmware behavior, not by
responding to actual topology events.

### Root Cause 2: RPi 4 EEE Micro-Sleeps (Contributing)

The Raspberry Pi 4 `bcmgenet` NIC enables Energy Efficient Ethernet (EEE / IEEE 802.3az)
by default. In EEE, the PHY negotiates Low Power Idle (LPI) mode with the switch port
during idle periods, briefly reducing transmit power. Some UniFi switch firmware versions
interpret the LPI exit sequence as a link-down event and generate a TCN, even though the
carrier has not truly changed (confirmed: `carrier_changes` counter stayed at 1 across
all apparent outages).

This contributed to the initial investigation but was **not the primary cause**. Disabling
EEE on `kitchen-pi` (`.143`) reduced apparent event frequency but did not stop blackouts
because the Flex switch was generating TCNs independently.

---

## Diagnosis Evidence

### One-way packet loss confirmed (not a Pi crash)

```bash
# From Mac: 50% loss to Pi
ping -c 60 -i 1 10.42.2.143
# result: 30 packets, 15 received, 50.0% packet loss

# Simultaneously, from Pi: 0% loss to gateway
ssh george@10.42.2.143 'ping -c 10 10.42.2.1 | tail -2'
# result: 10 packets, 10 received, 0.0% packet loss
```

Pi→gateway works; Mac→Pi fails. The Pi's stack is healthy. The **return path** (switch
MAC table) is the problem.

### Physical carrier was stable

```bash
ssh george@10.42.2.143 'cat /sys/class/net/eth0/carrier_changes'
# Output: 1
```

`carrier_changes` of 1 means only one carrier event since boot (the initial link-up).
No physical link flaps during any of the blackouts.

### Subnet-wide anti-correlated drops

```
[seq=10] .143: reply   .220: timeout  ← .143 visible, .220 not (MAC not yet re-learned)
[seq=11] .143: timeout .220: timeout  ← TCN flush, both MAC evicted
[seq=12] .143: timeout .220: reply    ← .220 re-learned, .143 evicted again
```

When one Pi came back, the other dropped. This is the MAC re-learning "wave" — exactly
what happens when a MAC flush propagates across a switch, affecting one forwarding domain
at a time.

### DHCP STP delay signature

```
NetworkManager[]: dhcp4 (eth0): beginning transaction (timeout in 45 seconds)
NetworkManager[]: dhcp4 (eth0): request timed out                       ← 45s
NetworkManager[]: dhcp4 (eth0): new lease, address=10.42.2.143          ← 5s later
```

First DHCP request sent while switch port is in STP Listening/Learning state (packet
dropped). Port transitions to Forwarding ~30s later; the immediate retry succeeds. This
is the textbook STP convergence delay signature.

### STP root bridge priority had no effect

After setting the Pro XG 48 PoE bridge priority to `4096`:
```bash
ssh root@10.42.2.1
vtysh -c "show spanning-tree"
# Pro XG 48 PoE confirmed as root bridge
```
Blackouts continued at the same rate. The Flex was generating TCNs autonomously; root
bridge identity was irrelevant.

---

## Remediation Options

The table below ranks all possible mitigations from most effective to least.

| Rank | Fix | Effectiveness | Permanence | Risk | Status |
|:----:|:----|:-------------|:-----------|:-----|:-------|
| 1 | **Remove/replace the Flex 2.5G PoE** | Eliminates root cause entirely | Permanent | None | ✅ Done |
| 2 | **Enable RSTP edge ports on all end-device ports** | Prevents TCN propagation from any device port | Permanent | Low | ⏳ Recommended |
| 3 | **Disable EEE on all RPi 4 (`bcmgenet`) hosts** | Removes bcmgenet LPI trigger; defense-in-depth | Permanent | None | 🔵 Partial (.143 done) |
| 4 | **TC Guard on inter-switch uplinks** | Blocks TCNs from propagating between switches | Permanent | Medium — must not block legitimate topology | ⏳ Future |
| 5 | **Set explicit STP root bridge priority** | Speeds reconvergence, no effect on TCN flood rate | Persistent | Low | 🔴 Tried — no effect |

---

## Explicit Remediation Instructions

### Fix 1 — Remove or replace the Flex 2.5G PoE ✅ Done

**When to apply:** Immediately when a Flex 2.5G PoE is suspect. This is the definitive
fix.

1. Identify the switch in the UniFi UI (Network → Devices).
2. Click the device → **Forget** (or `Unmanage`).
3. Physically disconnect it from the uplink and from all downlink devices.
4. Confirm blackouts stop (they should stop within seconds of the switch going offline).

**To re-add later (after a firmware fix is available):**
```bash
# SSH onto the UCGF
ssh root@10.42.2.1
# Check current UniFi OS firmware on the Flex
ubnt-systool ver
# Check for Flex 2.5G PoE firmware updates in UniFi UI before re-adding
```

---

### Fix 2 — Enable RSTP Edge Ports on End-Device Switch Ports

**When to apply:** On every port that connects to an end device (Raspberry Pi,
HifiBerry, PC, NAS) — not on inter-switch uplinks.

Edge ports (IEEE 802.1w "PortFast" equivalent) transition directly to Forwarding on
link-up without going through Listening/Learning. More importantly, **they do not send
TCNs when they detect a link-down/up event.** This means a single device with a flapping
NIC cannot trigger a subnet-wide MAC flush.

**Via UniFi Network UI (recommended):**

1. Navigate to **Network → Devices → [Switch Name]**.
2. Click the **Ports** tab.
3. For each port connected to an end device, click the port → **Edit**.
4. Under **STP**, enable **STP Edge Port** (also called PortFast).
5. Optionally also enable **BPDU Guard** — this will disable the port if it receives a
   BPDU (i.e., if someone plugs a switch into an "edge" port accidentally).
6. Apply and repeat for all end-device ports.

**Via SSH on the Pro XG 48 PoE:**

```bash
ssh admin@<switch-ip>

# Enter configuration mode
configure

# Set edge port on interfaces 1–8 (adjust range for your end-device ports)
# Note: interface numbering is 1-based; eth1 = port 1, etc.
set interfaces switch0 switch-port port-security storm-control broadcast-rate 1000
set interfaces switch0 switch-port port-security storm-control multicast-rate 1000

# For STP edge port (portfast) — UniFi switch CLI syntax:
set protocols rstp interface eth1 edge
set protocols rstp interface eth2 edge
# ... repeat for each end-device port

commit
save
```

> **Note:** The exact CLI syntax varies by UniFi switch model and firmware. Verify
> with `show protocols rstp` before and after. Always test on a single port first.

---

### Fix 3 — Disable EEE on Raspberry Pi 4 hosts

**When to apply:** On every Raspberry Pi 4 connected to a UniFi managed switch. Safe with
no downside on a LAN (EEE saves milliwatts; the risk of TCN-induced blackouts is not worth
it).

#### Immediately (temporary — lost on reboot):

```bash
sudo ethtool --set-eee eth0 eee off
```

Verify:

```bash
ethtool --show-eee eth0 | grep 'EEE status'
# EEE status: disabled
```

#### Persistently via NetworkManager dispatcher:

```bash
sudo mkdir -p /etc/NetworkManager/dispatcher.d
sudo tee /etc/NetworkManager/dispatcher.d/10-disable-eee > /dev/null << 'EOF'
#!/bin/bash
# Disable EEE on eth0 on every link-up event.
# Prevents bcmgenet LPI micro-sleeps from triggering STP TCNs on UniFi switches.
[ "$1" = "eth0" ] && [ "$2" = "up" ] && ethtool --set-eee eth0 eee off
EOF
sudo chmod +x /etc/NetworkManager/dispatcher.d/10-disable-eee
```

This fires on every `eth0` link-up event (including boot), so EEE is always disabled
even after reboots or reconnects.

**For HifiBerry OS hosts (Buildroot/BusyBox — no NetworkManager):**

```bash
# Create a systemd oneshot service
cat > /etc/systemd/system/disable-eee.service << 'EOF'
[Unit]
Description=Disable EEE on eth0 to prevent bcmgenet STP TCNs
After=network.target
Wants=network.target

[Service]
Type=oneshot
ExecStart=/sbin/ethtool --set-eee eth0 eee off
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now disable-eee.service
```

**Remaining hosts to apply this to:**

| Host | IP | Status |
|------|-----|--------|
| `kitchen-pi` | 10.42.2.143 | ✅ Done (NM dispatcher installed) |
| `kitchen` | 10.42.2.38 | ⏳ Pending |
| `living-room` | 10.42.2.39 | ⏳ Pending |
| `10.42.2.142` | 10.42.2.142 | ⏳ Pending (unreachable during fixes) |
| `10.42.2.220` | 10.42.2.220 | ⏳ Pending (unreachable during fixes) |

---

### Fix 4 — TC Guard on Inter-Switch Uplinks

**When to apply:** As a future hardening measure after re-introducing any switch into the
topology. TC Guard (a.k.a. TCN Guard) prevents TCNs received on a port from being
propagated toward the root, effectively containing a faulty or misbehaving downstream
switch.

> ⚠️ **Caution:** Do not enable TC Guard on legitimate inter-switch uplinks without
> understanding your spanning tree topology. TC Guard suppresses topology change
> information that may be legitimate. Apply on downlink ports that connect to access
> switches, not on the uplink to the core.

**Via UniFi Network UI:**

1. Navigate to **Network → Devices → Pro XG 48 PoE**.
2. Open the **Ports** tab.
3. For the port where the Flex 2.5G PoE (or any access-layer switch) was connected:
   - Enable **STP Edge Port**: No (it's a switch-to-switch link)
   - Look for **TC Guard** or **TCN Guard** in the STP section and enable it.

> As of UniFi OS 4.x this option may not be exposed in the UI. In that case use SSH.

**Via SSH on the upstream switch:**

```bash
ssh admin@<pro-xg-48-ip>
configure

# Suppress TCN propagation on the port where the downstream switch (Flex) connects
# adjust 'eth24' to the actual uplink port
set protocols rstp interface eth24 tc-guard

commit
save

# Verify
show protocols rstp interface eth24
```

---

### Fix 5 — Set Explicit STP Root Bridge Priority (Incomplete — Already Tried)

**When to apply:** This does NOT stop TCN floods. It only affects which switch wins the
root election during reconvergence. It was tried and confirmed to have no impact on the
blackout frequency.

Document for completeness only — prefer Fixes 1–4 instead.

**Via UniFi Network UI:**

1. Navigate to **Network → Devices → [Switch]**.
2. Under **RSTP** settings → set **Bridge Priority** to `4096` (lower = preferred root).
3. Apply.

**Via CLI (UniFi Cloud Gateway Fiber):**

```bash
ssh root@10.42.2.1
vtysh
configure terminal
spanning-tree priority 4096
end
write memory
```

**Result on this incident:** Pro XG 48 PoE became confirmed root bridge. Blackouts
continued unchanged. The Flex 2.5G PoE firmware bug generates TCNs independently of
root bridge identity.

---

## Prevention

1. **Monitor switch firmware** — Ubiquiti releases firmware updates that fix known STP
   bugs. Before re-adding the Flex 2.5G PoE, verify the current firmware version and
   changelogs. Subscribe to the UniFi firmware release notes.

2. **Enable edge ports preemptively** — Any port connected to a non-switch device should
   have edge port / PortFast enabled from day one. This eliminates an entire category of
   TCN-related incidents regardless of switch firmware quality.

3. **Disable EEE on all RPi 4 hosts** — Route cause 2 is still present on `.38`, `.39`,
   `.142`, and `.220`. Complete Fix 3 on these hosts.

4. **STP monitoring** — On the UCGF (`10.42.2.1`), consider logging TCN events:
   ```bash
   ssh root@10.42.2.1
   vtysh -c "debug spanning-tree all"
   # watch /var/log/messages for "topology change" events
   ```

---

## Related

- [Guide: Raspberry Pi 4 Ethernet Drops on UniFi Switches](../guides/rpi-unifi-ethernet-drops.md) — step-by-step
  diagnosis and EEE fix instructions
- [Guide: HifiBerry OS Spotify Connect Setup](../guides/hifiberry-os-spotify-setup.md) — avahi/mDNS fix applied
  during same session
