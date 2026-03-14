# Raspberry Pi 4 Ethernet Drops on UniFi Switches

**Applies to**: Any Raspberry Pi 4 (bcmgenet NIC) connected to a UniFi managed switch  
**Affected hosts**: `kitchen` (10.42.2.143), `snapcast-office` (10.42.2.220)  
**Fix date**: 2026-03-10  
**Updated**: 2026-03-13 — true root cause identified as Flex 2.5G PoE firmware bug

---

> **Update 2026-03-13**: After publishing this guide, STP bridge priority on the
> Pro XG 48 PoE was set to `4096` (making it the explicit root bridge) while
> leaving all other switches at the default `32768`. **TCNs still fired and the
> exact same blackout behavior was observed.** Further investigation (factory
> reset of the Flex 2.5G PoE, uplink-only test with zero devices connected)
> revealed that the switch was **generating STP TCNs autonomously — a firmware
> bug in Flex 2.5G PoE firmware 2.1.8**. Removing the switch from the UniFi UI
> **immediately and completely stopped the blackouts**. The EEE fix on the RPi 4s
> remains valid (and was a contributing factor in the initial investigation) but
> the primary cause was the faulty switch, not EEE.

---

## Summary

Raspberry Pi 4 devices connected to UniFi managed switches experienced periodic
~40–60 second connectivity blackouts affecting **the entire subnet**, not just the
Pi itself.

The investigation initially attributed the cause to a two-part interaction
between EEE on the RPi 4 `bcmgenet` NIC and STP TCNs. **The true root cause was
a firmware bug in the UniFi Flex 2.5G PoE switch (firmware 2.1.8): the switch
generated STP Topology Change Notifications autonomously, even with no devices
connected.** EEE micro-sleeps on the RPi 4 contributed to the early investigation
but were not the underlying cause.

Each TCN causes every switch in the spanning tree to flush its MAC address table,
making all devices on the subnet temporarily unreachable while MAC addresses are
re-learned (~15–40s).

---

## Symptoms

- Periodic ~40s blackouts where the Mac (or other devices) **cannot ping the Pi
  or any other device on the same subnet**
- The Pi itself appears healthy throughout: Pi→gateway ping shows 0% loss, only
  inbound traffic is affected
- First DHCP attempt after an outage always times out (~45s), second always
  succeeds quickly (~5s) — classic STP delay signature
- Multiple devices drop simultaneously and correlated (when Pi A comes back,
  Pi B drops → same subnet-wide MAC flush)
- `cat /sys/class/net/eth0/carrier_changes` stays at 1 across apparent outages
  (physical link is stable; the Pi's IP layer drops but the carrier does not)

---

## Root Cause

### STP Topology Change Notifications (TCNs)

In IEEE 802.1D Spanning Tree Protocol, any time a port transitions state
(link-down → link-up), the switch sends a TCN towards the root bridge. The
root bridge then sets **MAC address aging** across all switches to
`ForwardDelay` (typically 15s) and flushes all learned MACs. This prevents
loops during topology changes, but has the side effect of making every device
on every VLAN temporarily unreachable while they re-ARP.

### EEE Link Micro-Sleeps on bcmgenet (RPi 4)

The RPi 4 uses the Broadcom BCM54213PE PHY via the `bcmgenet` driver. Its EEE
implementation negotiates Low Power Idle (LPI) mode with the switch, briefly
transitioning the PHY to a low-power state during idle periods. Some UniFi
switch firmware versions interpret the LPI exit as a link-down/up event and
generate a TCN, even though carrier has not truly changed.

### Flex 2.5G PoE Firmware Bug — Autonomous TCN Generation

**This was the confirmed primary root cause.** The UniFi Flex 2.5G PoE running
firmware 2.1.8 generates STP TCNs autonomously — independent of any connected
device behavior. This was proven by:

1. Factory resetting the Flex 2.5G PoE and connecting only its uplink (no other
   devices)
2. Observing TCNs continue at the same ~30–60s interval with the switch otherwise
   idle
3. Removing the switch from the UniFi UI → **blackouts stopped immediately**

Setting the STP bridge priority to `4096` on the Pro XG 48 PoE to lock it as
root bridge had **no effect** — the TCNs still fired at the same rate and the
same subnet-wide blackouts were observed. The firmware bug causes TCNs to be
generated regardless of root bridge status.

**Affected hardware**: UniFi Flex 2.5G PoE, firmware 2.1.8  
**Status**: Awaiting firmware fix from Ubiquiti before re-adding to the network.

### Chain of Events

```
RPi 4 eth0 enters EEE LPI (idle link)
        │
        ▼
UniFi switch sees apparent link down / LPI exit
        │
        ▼
Switch sends STP Topology Change Notification (TCN) to root
        │
        ▼
All switches in spanning tree flush MAC tables
& set aging timer to ForwardDelay (15s)
        │
        ▼
ARP cache on all devices expires, traffic black-holes
for ~15–40s while MAC tables are re-learned
        │
        ▼
Connectivity restored subnet-wide
```

---

## Diagnosis Steps Taken

### 1. Ruled out Pi reboots

```bash
ssh george@10.42.2.143 'uptime'
# uptime > several days — Pi did not reboot
```

### 2. Confirmed stable physical carrier

```bash
ssh george@10.42.2.143 'cat /sys/class/net/eth0/carrier_changes'
# Output: 1 (since last reboot — no physical flaps despite apparent outages)
```

### 3. Confirmed one-way packet loss

```bash
# From Mac: 50% loss to Pi
ping -c 60 -i 1 10.42.2.143
# 30 packets transmitted, 15 received, 50.0% packet loss

# From Pi: 0% loss to gateway
ssh george@10.42.2.143 'ping -c 10 10.42.2.1 | tail -2'
# 10 packets transmitted, 10 received, 0.0% packet loss
```

The Pi could reach the gateway but the Mac could not reach the Pi — confirming
the return path (switch MAC table) was the problem, not the Pi's stack.

### 4. Confirmed subnet-wide scope (anti-correlation)

Ran parallel pings to two Pis on the same switch. When one came back, the other
dropped — confirming the TCN was flushing the MAC table for the entire subnet,
not just one device:

```
[seq=10] .143: reply   .220: timeout  ← .143 visible, .220 not
[seq=11] .143: timeout .220: timeout  ← TCN flush, both gone
[seq=12] .143: timeout .220: reply    ← .220 visible, .143 not (re-learning)
```

### 5. Confirmed DHCP STP delay signature

```
NM log: dhcp4 (eth0): activation: beginning transaction (timeout in 45 seconds)
NM log: dhcp4 (eth0): request timed out                  ← 45s timeout
NM log: dhcp4 (eth0): state changed new lease, address=10.42.2.143   ← 5s later
```

Classic STP delay: first DHCP attempt sent while port is in STP Listening state,
packet dropped. Port transitions to Forwarding ~30s later; second DHCP broadcast
succeeds immediately.

### 6. Isolated source with cross-switch testing

Moving the test to a Pi on a different switch and observing synchronized drops
confirmed the TCN source was the uplink between the two switches, not the Pi
ports themselves.

---

## Fix

### Part 1: Disable EEE on each Raspberry Pi (Done)

Disabling EEE prevents the bcmgenet PHY from entering LPI mode, eliminating the
root cause of the TCN-triggering link events.

#### Immediately (temporary, until next reboot):

```bash
sudo ethtool --set-eee eth0 eee off
```

#### Verify:

```bash
ethtool --show-eee eth0 | grep 'EEE status'
# EEE status: disabled
```

#### Persistent (survives reboot) — Debian/Raspberry Pi OS with NetworkManager:

```bash
sudo mkdir -p /etc/NetworkManager/dispatcher.d
sudo tee /etc/NetworkManager/dispatcher.d/10-disable-eee << 'EOF'
#!/bin/bash
# Disable EEE on eth0 on link-up.
# Prevents bcmgenet PHY micro-sleeps from triggering STP TCNs, which cause
# subnet-wide MAC table flushes and ~40s connectivity blackouts.
[ "$1" = eth0 ] && [ "$2" = up ] && ethtool --set-eee eth0 eee off
EOF
sudo chmod +x /etc/NetworkManager/dispatcher.d/10-disable-eee
```

#### Persistent — Buildroot (HifiBerry OS, no NetworkManager):

```bash
# Option A: if-up.d exists
cat > /etc/network/if-up.d/disable-eee << 'EOF'
#!/bin/sh
[ "$IFACE" = eth0 ] && ethtool --set-eee eth0 eee off
EOF
chmod +x /etc/network/if-up.d/disable-eee

# Option B: rc.local fallback
sed -i 's/^exit 0/ethtool --set-eee eth0 eee off\nexit 0/' /etc/rc.local
```

#### Status per device:

| Hostname | IP | OS | EEE Fix Applied |
|---|---|---|---|
| kitchen | 10.42.2.143 | Raspberry Pi OS (Debian Trixie) | ✅ `/etc/NetworkManager/dispatcher.d/10-disable-eee` |
| snapcast-office | 10.42.2.220 | HifiBerry OS (Buildroot) | ✅ `/etc/rc.local` |
| snapcast-buildroot | 10.42.2.142 | HifiBerry OS (Buildroot) | ⚠️ Pending |

---

### Part 2 (Attempted — No Effect): Lock STP Root Bridge Priority

Setting the **STP Bridge Priority** on the Pro XG 48 PoE to `4096` (the lowest,
most-preferred value) was attempted to lock it as the permanent STP root bridge
and prevent any other switch from accidentally winning root election.

In UniFi Network: **Devices → [switch] → Settings → Services → Spanning Tree
Priority → 4096**

**Result**: TCNs continued at the same rate. The blackouts were identical.
Root bridge priority controls *which* switch is root; it does not prevent a
broken switch from sending TCNs. This was the observation that pointed to the
Flex 2.5G PoE firmware bug as the real cause.

---

### Part 3 (True Fix): Remove the Flex 2.5G PoE Switch

The Flex 2.5G PoE (firmware 2.1.8) was removed from the network via the UniFi
UI (**Devices → Flex 2.5G PoE → Forget Device**). The switch was also physically
disconnected.

**Result**: All subnet blackouts stopped immediately and completely.

The switch is awaiting a firmware fix from Ubiquiti before being reconnected. Do
not reconnect a Flex 2.5G PoE running firmware 2.1.8 to a production network
until the autonomous TCN issue has been addressed.

---

### Part 4 (Still Recommended): UniFi Switch — Enable STP Edge (PortFast) on Pi Ports

STP **Edge** mode (also called PortFast) tells the switch that a port connects
directly to an end device, never to another switch. The switch immediately
transitions the port to Forwarding state on link-up (no Listening/Learning
delay), and **does not send TCNs** when the port state changes.

In UniFi Network:

1. Go to **Devices** → select the switch
2. Go to **Ports** → click the port the Pi is connected to
3. Under **Port Profile** or **STP**, enable **"STP Edge"** (or **"PortFast"** depending on UI version)
4. Apply

> **Critical**: Only enable STP Edge on ports that connect to end devices (PCs,
> Pis, NAS, etc.). **Never** enable STP Edge on ports that connect to other
> switches — doing so on an uplink port while there is a redundant path will
> create a **broadcast storm loop** (we learned this the hard way during
> diagnosis).

#### What NOT to do

| Action | Result |
|---|---|
| Disable STP entirely on all ports including uplinks | **Broadcast storm loop** if any redundant uplink path exists. All devices on subnet go dark. |
| Disable STP on end-device ports only (keeping uplinks STP-enabled) | Safe, prevents TCNs, but less correct than Edge mode |
| Enable STP Edge on uplink ports | **Loop risk** — same as disabling STP if there is a redundant path |

---

## Verification

After applying both fixes, run a sustained ping test to confirm zero loss:

```bash
ping -c 300 -i 1 10.42.2.143 | tail -3
# 300 packets transmitted, 300 received, 0.0% packet loss
```

Also verify EEE is disabled on the Pi after a reboot:

```bash
ssh george@10.42.2.143 'ethtool --show-eee eth0 | grep "EEE status"'
# EEE status: disabled
```

---

## Related Issues Encountered During Diagnosis

### Broadcast storm loop (self-inflicted)

While investigating, STP was disabled on all ports of both switches including
the inter-switch uplink. This caused a broadcast storm loop when the two
switches had any redundant physical path, taking both switches and all devices
offline. Recovery required re-enabling STP on the uplink ports, followed by
~30s for STP convergence (Listening → Learning → Forwarding), then physically
unplugging/replugging the Pi ethernet cables to trigger DHCP renewal.

### VLAN reassignment after UniFi port profile changes

After modifying STP settings per-port in UniFi, one Pi received a `10.42.1.x`
address instead of `10.42.2.x`. The port profile had been reset to the default
network as a side effect of the STP configuration change. Fix: go to
**Devices → switch → Ports → Native Network** and set it back to the correct
network.

### DHCP reservation not taking effect

Even with a DHCP reservation configured in UniFi for the Pi's MAC address, if
the Pi is on the wrong VLAN/network, the reservation in the `10.42.2.0/24`
scope does not apply. Always confirm the port's Native Network matches the
intended VLAN before debugging why the IP is wrong.

---

## References

- [UniFi STP and PortFast/Edge documentation](https://help.ui.com/hc/en-us/articles/360006836773)
- [Raspberry Pi bcmgenet driver](https://github.com/raspberrypi/linux/tree/rpi-6.6.y/drivers/net/ethernet/broadcom/genet)
- `man ethtool` — `--set-eee` and `--show-eee` options
- IEEE 802.1D §8.3 — Topology Changes and MAC aging
