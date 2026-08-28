#!/bin/bash
# =============================================================================
# DietPi Automation_Custom_Script — provision a Snapcast + Spotify Connect
# audio node.
#
# Drop this on the DietPi boot partition as /boot/Automation_Custom_Script.sh
# (see README). It runs once at the end of first-boot setup, as root.
#
# Result (matches the validated snap-test node):
#   * snapclient joins the Snapcast group, output through a shared ALSA dmix.
#   * go-librespot advertises this box as a Spotify Connect target (same DAC).
#   * A wrapper auto-detects whatever USB DAC is plugged in (robust to swaps);
#     a udev rule restarts both services the instant a DAC is added/removed.
#   * Device name = the box's hostname (set it per-node in dietpi.txt).
#
# Idempotent — safe to re-run (e.g. `bash Automation_Custom_Script.sh`).
# =============================================================================
set -euo pipefail

# ---- config (edit per site) -------------------------------------------------
SNAPSERVER_HOST="${SNAPSERVER_HOST:-10.42.2.37}"   # snapcast LB VIP
GLR_VERSION="${GLR_VERSION:-v0.7.4}"               # go-librespot release
ZEROCONF_PORT="${ZEROCONF_PORT:-4070}"             # pinned (cross-VLAN friendly)
NODE_NAME="$(hostname)"                            # Spotify/Snapcast device name
# Key installed for root so a node is reachable without a password from first
# boot. Kitchen and living-room were both briefly UNREACHABLE because they were
# provisioned with a password nobody had and no key -- recovery would have been
# pulling the SD card. Override with SSH_PUBKEY=..., or drop extra keys in
# /boot/authorized_keys and they are merged too.
TOPPINGCTL_REF="${TOPPINGCTL_REF:-main}"           # toppingctl branch/tag to install
SSH_PUBKEY="${SSH_PUBKEY:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB2HrdkLmvVbCxAvhKfMVX2raH2WZYN/1iEq75zhIEWL gjcourt@gmail.com}"

log() { echo "[dietpi-audio] $*"; }

# ---- 0. dual-home: bring up BOTH wired + WiFi -------------------------------
# DietPi configures only the "primary" adapter and comments out the other in
# /etc/network/interfaces — on a WiFi-provisioned node that leaves eth0 as
# `#allow-hotplug eth0`, so wired stays dead. Uncomment it (and wlan0 if WiFi is
# set up) so a node works in any room, cabled or not; the unused NIC just has no
# carrier and doesn't block boot (allow-hotplug, not auto). Idempotent; the
# change takes effect on DietPi's end-of-first-run reboot.
IFACES=/etc/network/interfaces
if [ -f "$IFACES" ]; then
  grep -q '^allow-hotplug eth0' "$IFACES" || \
    { sed -i 's/^#[[:space:]]*allow-hotplug eth0/allow-hotplug eth0/' "$IFACES"; log "enabled eth0 (dual-homed with wlan0)"; }
  if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ]; then
    grep -q '^allow-hotplug wlan0' "$IFACES" || \
      { sed -i 's/^#[[:space:]]*allow-hotplug wlan0/allow-hotplug wlan0/' "$IFACES"; log "enabled wlan0 (dual-homed with eth0)"; }
  fi
fi

# ---- 1. snapclient (DietPi software id 192) ---------------------------------
if ! command -v snapclient >/dev/null 2>&1; then
  log "installing Snapcast Client (dietpi-software 192)"
  /boot/dietpi/dietpi-software install 192
fi

# ---- 2. go-librespot (arch-matched release binary) --------------------------
if ! command -v go-librespot >/dev/null 2>&1; then
  case "$(dpkg --print-architecture)" in
    arm64) A=arm64 ;; armhf) A=armv6 ;; amd64) A=amd64 ;; *) A="$(dpkg --print-architecture)" ;;
  esac
  log "installing go-librespot ${GLR_VERSION} (${A})"
  curl -fsSL "https://github.com/devgianlu/go-librespot/releases/download/${GLR_VERSION}/go-librespot_linux_${A}.tar.gz" -o /tmp/glr.tgz
  tar -xzf /tmp/glr.tgz -C /tmp
  install -m 755 /tmp/go-librespot /usr/local/bin/go-librespot
fi

# ---- 3. shared dmix, following the ACTUAL USB card index --------------------
# This used to hardcode hw:0 on the assumption that the USB DAC is the only
# sound card. That assumption is already false on at least one node: with
# onboard/HDMI audio enabled, bcm2835 takes card 0 and the USB DAC lands on
# card 1. The old config then sent every stream to the headphone jack while
# both services reported healthy -- silence with no error anywhere.
#
# So generate asound.conf from the detected index instead, and regenerate it
# whenever a card appears or disappears (udev, below) rather than once at
# provision time.
# Detection lives in ONE place, used by the refresh script (root, writes
# asound.conf) and by the snapclient wrapper (unprivileged, gate only).
cat > /usr/local/bin/audio-dmix-detect <<'EOF'
#!/bin/bash
# Print the ALSA card index of the first PLAYBACK-capable USB audio card.
# Exit 1 if there is none.
#
# Playback-aware on purpose. "First USB card" is wrong on any node that also has
# a USB capture interface -- an optical input for TV audio enumerates as a sound
# card too and can come first. Observed on the living-room node 2026-08-28:
#   card 0  SA9227 USB Audio   pcm0c   <- capture only
#   card 1  D30 Pro            pcm0p   <- the DAC
set -uo pipefail
for n in $(awk '/USB/ && $1 ~ /^[0-9]+$/ {print $1}' /proc/asound/cards 2>/dev/null); do
  for pcm in /proc/asound/card"$n"/pcm*p; do
    [ -e "$pcm" ] || continue
    echo "$n"; exit 0
  done
done
exit 1
EOF
chmod 755 /usr/local/bin/audio-dmix-detect

cat > /usr/local/bin/audio-capture-detect <<'EOF'
#!/bin/bash
# Print the ALSA card index of the first CAPTURE-capable USB audio card
# (e.g. an optical input feeding TV audio in). Exit 1 if there is none.
#
# Mirror of audio-dmix-detect, which finds the playback card. A node can have
# both: the living room has a capture interface on card 0 and the DAC on card 1,
# which is exactly why neither detector may assume "the first USB card".
set -uo pipefail
for n in $(awk '/USB/ && $1 ~ /^[0-9]+$/ {print $1}' /proc/asound/cards 2>/dev/null); do
  for pcm in /proc/asound/card"$n"/pcm*c; do
    [ -e "$pcm" ] || continue
    echo "$n"; exit 0
  done
done
exit 1
EOF
chmod 755 /usr/local/bin/audio-capture-detect

cat > /usr/local/bin/audio-dmix-refresh <<'EOF'
#!/bin/bash
# Write /etc/asound.conf pointing dmix at the USB DAC's current card index.
# Exits non-zero when no USB audio card is present, so callers can gate on it.
set -euo pipefail
# `|| true`: awk exits 2 if /proc/asound/cards does not exist, which under
# `set -e` would abort before the emptiness check below ever runs.
# Pick the first USB card that can actually PLAY. Matching "the first USB card"
# is not enough: a node with a USB capture interface (e.g. an optical input for
# TV audio) enumerates that as a sound card too, and it can come first. Observed
# on the living-room node, 2026-08-28:
#   card 0  SA9227 USB Audio   pcm0c   <- capture only, was selected
#   card 1  D30 Pro            pcm0p   <- the actual DAC
# dmix was pointed at the TV input and no audio could play.
card=$(/usr/local/bin/audio-dmix-detect || true)
if [ -z "${card:-}" ]; then
  echo "audio-dmix-refresh: no USB audio card present" >&2
  exit 1
fi
# Temp file in /etc, on purpose: same filesystem as the target, so the mv below
# is a real atomic rename rather than copy+unlink (which a udev timeout could
# interrupt, leaving a truncated config).
#
# This briefly regressed to plain mktemp because an earlier revision had the
# UNPRIVILEGED snapclient wrapper calling this script, and it could not write
# /etc. That is no longer true -- refresh now runs only from root contexts
# (provision time, ExecStartPre=+, udev), and the wrapper calls the read-only
# audio-dmix-detect instead. So the /etc target is available again, and there is
# no reason to trade away atomicity.
tmp=$(mktemp /etc/asound.conf.XXXXXX)
# Clean up on ANY exit path. The temp now lives in /etc next to the real config,
# so a failure between here and the mv would leave a confusing
# asound.conf.XXXXXX sitting beside it. Harmless after a successful mv (rm -f on
# a moved file is a no-op).
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<CONF
# GENERATED by audio-dmix-refresh -- do not edit. USB DAC detected as card ${card}.
pcm.!default { type plug; slave.pcm "dmixer" }
# Uniquely-named plug-wrapped alias for snapclient. It must be BOTH:
#   * uniquely named -- snapclient matches -s against its device list by
#     DESCRIPTION as well as name, and "default" collides with sysdefault's
#     "Default Audio Device" description, silently selecting the wrong PCM;
#   * plug-wrapped -- pointing snapclient straight at "dmixer" skips format
#     conversion, and a 32-bit-only DAC then rejects snapclient's 16-bit
#     stream with "Can't set format: Invalid argument, supported: S32_LE".
pcm.snapdmix { type plug; slave.pcm "dmixer" }
pcm.dmixer {
  type dmix
  ipc_key 2048
  ipc_perm 0666
  slave { pcm "hw:${card},0"; rate 48000; channels 2; period_size 1024; buffer_size 8192 }
}
ctl.!default { type hw; card ${card} }
# A named PCM used with snapclient's --mixer hardware needs a CTL of the SAME
# name: snapclient calls snd_ctl_open("<-s name>") to reach the mixer. Without
# this it dies with "Invalid CTL snapdmix".
ctl.snapdmix { type hw; card ${card} }
CONF
# Only replace when it actually changed, so we do not churn the file (and the
# dmix ipc segment) on every service restart.
if ! cmp -s "$tmp" /etc/asound.conf 2>/dev/null; then
  # 0644 EXPLICITLY. mktemp creates 0600 and mv preserves the mode, which makes
  # asound.conf root-only. ALSA then silently ignores it for every unprivileged
  # client and falls back to its built-in default (card 0) -- which on a node
  # whose card 0 is a capture device fails with a completely misleading
  # "unable to open slave ... No such file or directory". Cost hours; the strace
  # showed openat("/dev/snd/pcmC0D0p") long after asound.conf said card 1.
  chmod 0644 "$tmp"
  mv "$tmp" /etc/asound.conf
  echo "audio-dmix-refresh: asound.conf now targets card ${card}"
else
  rm -f "$tmp"
fi
EOF
chmod 755 /usr/local/bin/audio-dmix-refresh

# Best-effort at provision time: a node with no DAC yet simply has no
# asound.conf until one is plugged in, which udev then handles.
/usr/local/bin/audio-dmix-refresh || log "no USB DAC yet — asound.conf will be written on plug-in"

# ---- 4. snapclient: auto-detect wrapper + robust systemd override ------------
cat > /usr/local/bin/snapclient-autodev <<EOF
#!/bin/bash
# Wait for a USB DAC, then run snapclient through the shared dmix 'default'
# device so it coexists with go-librespot. systemd retries until a DAC appears.
HOST="\${SNAPSERVER_HOST:-${SNAPSERVER_HOST}}"
# Point dmix at whatever card the DAC is on RIGHT NOW, then start. This also
# gates startup: refresh exits non-zero when no USB DAC is present, and systemd
# retries. The old version detected the card index and then never used it,
# playing to a hardcoded hw:0 regardless.
# Gate only -- do not write /etc from here. This runs as the unprivileged
# snapclient user; regeneration is done by root via ExecStartPre=+ below and by
# the udev rule.
card=\$(/usr/local/bin/audio-dmix-detect) || { echo "snapclient-autodev: no playback-capable USB card yet"; exit 1; }
# -s snapdmix, NOT "default". snapclient matches -s against its own enumerated
# device list by DESCRIPTION as well as name, and the entry described "Default
# Audio Device" is 'sysdefault' (idx 1), not the PCM named 'default' (idx 2).
# So '-s default' silently selects sysdefault, which asound.conf does not
# define, so ALSA falls back to its built-in default (card 0) and fails with a
# misleading "unable to open slave". Naming our dmix PCM directly is
# unambiguous. Stream format is 48000:16:2, matching the dmix slave exactly, so
# no plug conversion is needed.
# Prefer the DAC's OWN mixer over snapclient's software mixer.
#
# --mixer software attenuates ONLY the Snapcast stream. go-librespot is a
# separate process writing to the same dmix, so whenever Spotify Connect is what
# is playing, a software mixer changes nothing audible -- which presents as "the
# Home Assistant volume slider does nothing". Measured on living-room
# 2026-08-28: HA moved the client 70% -> 30% server-side with no change in
# output, because go-librespot held the DAC.
#
# The DAC's hardware control sits UNDER every source on this node (Snapcast,
# Spotify, and later the TV capture -- all funnel through the same dmix), giving
# one knob over all of them. That is what the retired HiFiBerry DSP bridge used
# to provide, and it is what kitchen already does with its D50s.
#
# Detected rather than hardcoded, so a DAC swap does not need a config edit.
# Note the DAC's own front-panel volume remains a SEPARATE stage in series --
# set that once as a ceiling and drive day-to-day level from here.
# Prefer a PLAYBACK-capable control. Taking "the first control" would bind the
# hardware mixer to a CAPTURE control on any DAC that exposes one (ADC or
# loopback), which makes the Home Assistant slider a silent no-op -- it moves,
# and nothing gets quieter. Measured 2026-08-28: the D30 Pro and the D50s each
# expose exactly one control and no capture control, so this is defensive rather
# than a fix for observed breakage. It is worth the two extra greps because the
# failure mode is silent and looks like a broken integration, not a wrong mixer.
ctl=\$(amixer -c "\$card" scontents 2>/dev/null | grep -A1 '^Simple mixer control' | grep -B1 'Capabilities:.*pvolume' | sed -n "s/^Simple mixer control '\(.*\)',0\$/\1/p" | head -1)
if [ -n "\$ctl" ]; then
  exec /usr/bin/snapclient --logsink=system --host "\$HOST" -s snapdmix --mixer "hardware:\$ctl"
fi
exec /usr/bin/snapclient --logsink=system --host "\$HOST" -s snapdmix --mixer software
EOF
chmod 755 /usr/local/bin/snapclient-autodev

mkdir -p /etc/systemd/system/snapclient.service.d
cat > /etc/systemd/system/snapclient.service.d/override.conf <<'EOF'
[Unit]
# retry indefinitely while no DAC is present (default burst limit gives up)
StartLimitIntervalSec=0
[Service]
# '+' runs this as root regardless of the unit's User=, which is required
# because it writes /etc/asound.conf and snapclient itself is unprivileged.
ExecStartPre=+/usr/local/bin/audio-dmix-refresh
ExecStart=
ExecStart=/usr/local/bin/snapclient-autodev
Restart=always
RestartSec=5
EOF

# ---- 5. go-librespot: config + service --------------------------------------
mkdir -p /root/.config/go-librespot
cat > /root/.config/go-librespot/config.yml <<EOF
device_name: ${NODE_NAME}
device_type: speaker
audio_backend: alsa
audio_device: default
bitrate: 320
zeroconf_enabled: true
zeroconf_port: ${ZEROCONF_PORT}
zeroconf_backend: builtin
EOF

cat > /etc/systemd/system/go-librespot.service <<'EOF'
[Unit]
Description=go-librespot (Spotify Connect)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
Environment=HOME=/root
# Best-effort: if no DAC is attached there is nothing to play to anyway, so do
# not block startup on it (leading '-').
ExecStartPre=-/usr/local/bin/audio-dmix-refresh
ExecStart=/usr/local/bin/go-librespot --config_dir /root/.config/go-librespot
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

# ---- 6. udev: restart BOTH on DAC plug/unplug -------------------------------
cat > /etc/udev/rules.d/99-audio-node.rules <<'EOF'
# Regenerate asound.conf FIRST so the restarted services bind to the DAC's new
# card index, then restart them. Order matters: restarting first would re-read
# a stale config.
ACTION=="add",    SUBSYSTEM=="sound", KERNEL=="card*", RUN+="/usr/local/bin/audio-dmix-refresh"
ACTION=="add",    SUBSYSTEM=="sound", KERNEL=="card*", RUN+="/bin/systemctl --no-block restart snapclient go-librespot"
ACTION=="remove", SUBSYSTEM=="sound", KERNEL=="card*", RUN+="/usr/local/bin/audio-dmix-refresh"
ACTION=="remove", SUBSYSTEM=="sound", KERNEL=="card*", RUN+="/bin/systemctl --no-block restart snapclient go-librespot"
EOF

# ---- 7. root SSH key so the node is never password-only ---------------------
# Both kitchen and living-room were, for a while, reachable by nobody: password
# auth with a password that had been changed, and no key. The recovery path was
# physically pulling the SD card. A node should arrive reachable.
mkdir -p /root/.ssh && chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
add_key() {
  local k="$1"
  [ -z "$k" ] && return 0
  case "$k" in ssh-*|ecdsa-*) : ;; *) return 0 ;; esac      # ignore junk/comments
  if grep -qF "$k" /root/.ssh/authorized_keys 2>/dev/null; then
    log "ssh key already present"
  else
    printf '%s\n' "$k" >> /root/.ssh/authorized_keys
    log "ssh key installed"
  fi
}
add_key "$SSH_PUBKEY"
# Merge any extra keys dropped on the boot partition (one per line).
if [ -f /boot/authorized_keys ]; then
  while IFS= read -r line; do add_key "$line"; done < /boot/authorized_keys
fi

# ---- 8. toppingctl: local control for the attached DAC ----------------------
# Installed on EVERY audio node, because every node has a DAC by definition and
# the multi-DAC control-plane design (lab 01-022) puts an agent on the machine
# paired with each one. It is inert until used: it refuses to drive any device
# whose USB product string is not a confirmed model, which is not theoretical --
# five Topping models share USB product ID 0x152a:8750 with register maps that
# collide, so PID cannot identify hardware and the guard is what prevents
# writing one model's registers to another.
#
# ⚠️ Do NOT `apt install python3-hid`. Debian ships cython-hidapi under that
# name, which exposes hid.device() -- toppingctl needs hid.Device(path=...) from
# the PyPI `hid` package. The apt one installs cleanly, imports fine, and then
# fails on an attribute that does not exist. Hence a venv.
if [ ! -x /opt/toppingctl-venv/bin/python ]; then
  log "installing toppingctl (venv + PyPI hid)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv libhidapi-hidraw0 >/dev/null
  python3 -m venv /opt/toppingctl-venv
  /opt/toppingctl-venv/bin/pip install -q --upgrade pip
  /opt/toppingctl-venv/bin/pip install -q hid
fi

# Source from a tarball rather than a clone, so git is not a dependency on a
# minimal image.
mkdir -p /opt/toppingctl
if curl -fsSL "https://github.com/gjcourt/toppingctl/archive/refs/heads/${TOPPINGCTL_REF}.tar.gz" -o /tmp/tctl.tgz; then
  tar -xzf /tmp/tctl.tgz -C /tmp
  cp -f /tmp/toppingctl-"${TOPPINGCTL_REF}"/*.py /opt/toppingctl/ 2>/dev/null || true
  rm -rf /tmp/tctl.tgz /tmp/toppingctl-"${TOPPINGCTL_REF}"
  log "toppingctl source installed (${TOPPINGCTL_REF})"
else
  log "WARNING: could not fetch toppingctl source — venv is in place, source is not"
fi

# Convenience wrapper so it is on PATH without activating the venv.
cat > /usr/local/bin/toppingctl <<'EOF'
#!/bin/sh
exec /opt/toppingctl-venv/bin/python /opt/toppingctl/toppingctl.py "$@"
EOF
chmod 755 /usr/local/bin/toppingctl

# ---- 9. TV capture loop: optical input -> the same shared dmix ---------------
# Bridges a USB capture interface (optical from the TV) into the dmix that
# snapclient and go-librespot already share, so TV audio mixes with music
# instead of fighting for the device -- and inherits the same Home Assistant
# volume, because that drives the DAC's own mixer underneath all of them.
#
# Installed on every node; it simply idles where no capture device exists.
cat > /usr/local/bin/tvloop-autodev <<'EOF'
#!/bin/bash
# Bridge the TV's optical input into the shared dmix, and survive the TV being
# off -- which is most of the day.
#
# WHY THE POLL LOOP EXISTS. With no optical carrier the receiver has nothing to
# clock off, so the capture device opens and then errors ("Poll FD
# initialization failed" / arecord: "Input/output error"). alsaloop treats that
# as fatal and exits. Left to systemd's Restart=, that is a crash loop for every
# hour the TV is off: measured 7,650 journal lines/hour on a node whose
# /var/log is a 50 MB tmpfs. Polling quietly here keeps the node silent while
# idle and still reconnects within ~5s of the TV coming on.
set -uo pipefail

ccard=$(/usr/local/bin/audio-capture-detect) || { echo "tvloop: no capture device"; exit 1; }
/usr/local/bin/audio-dmix-detect >/dev/null 2>&1 || { echo "tvloop: no playback device"; exit 1; }
dev="hw:${ccard},0"
echo "tvloop: capture $dev -> snapdmix"

# Is there actually a signal? This is the same open alsaloop performs, so it
# fails in exactly the cases alsaloop would fail -- no carrier, or the device
# held by something else.
has_signal() { timeout 3 arecord -D "$dev" -f S16_LE -c 2 -r 48000 -d 1 -q /dev/null >/dev/null 2>&1; }

state=init
while :; do
  if has_signal; then
    [ "$state" = running ] || echo "tvloop: signal present, starting loop"
    state=running
    # -t 20000 chosen EMPIRICALLY, not derived. 5000 produced 22 underruns and
    # an explicit -B 1024 -E 256 produced 9; 20000 runs clean. Measured A/V
    # offset is ~75 ms and does NOT respond to this value or to the dmix buffer
    # size -- the delay is upstream (TV and/or capture hardware). Do NOT tune
    # this to chase lip-sync; it costs underruns and buys nothing.
    alsaloop -C "$dev" -P snapdmix -r 48000 -f S16_LE -c 2 -t 20000
    # alsaloop returning means the format changed or the signal dropped. Say so
    # once, then fall back to quiet polling rather than spinning.
    [ "$state" = waiting ] || echo "tvloop: capture ended, waiting for signal"
    state=waiting
  else
    [ "$state" = waiting ] || echo "tvloop: no signal, waiting"
    state=waiting
  fi
  sleep 5
done
EOF
chmod 755 /usr/local/bin/tvloop-autodev

cat > /etc/systemd/system/tvloop.service <<'EOF'
[Unit]
Description=TV optical capture -> shared dmix -> DAC
After=sound.target snapclient.service
Wants=sound.target
# Restart is NOT optional: alsaloop opens the capture with fixed parameters and
# dies ("Poll FD initialization failed") whenever the TV changes audio format or
# drops the optical signal -- which happens on every app or source change.
# Without this, TV audio stops silently and never comes back.
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=/usr/local/bin/tvloop-autodev
# Backstop only: the wrapper handles signal loss and format changes internally
# by polling, so a restart here means it died outright. 30s because the only
# reasons left are "no capture hardware on this node" (idle forever) and a real
# crash, neither of which benefits from a fast retry.
Restart=always
RestartSec=30
Nice=-10

[Install]
WantedBy=multi-user.target
EOF

# ---- 10. enable + (re)start --------------------------------------------------
systemctl daemon-reload
udevadm control --reload-rules || true
systemctl enable --now go-librespot
systemctl enable snapclient
systemctl enable tvloop
systemctl restart snapclient || true
systemctl restart tvloop || true

log "done — node '${NODE_NAME}': snapclient=$(systemctl is-active snapclient) go-librespot=$(systemctl is-active go-librespot)"
log "plug a USB DAC in and both bind automatically (dmix-shared)."
