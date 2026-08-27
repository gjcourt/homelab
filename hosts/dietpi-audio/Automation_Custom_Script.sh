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
cat > /usr/local/bin/audio-dmix-refresh <<'EOF'
#!/bin/bash
# Write /etc/asound.conf pointing dmix at the USB DAC's current card index.
# Exits non-zero when no USB audio card is present, so callers can gate on it.
set -euo pipefail
# `|| true`: awk exits 2 if /proc/asound/cards does not exist, which under
# `set -e` would abort before the emptiness check below ever runs.
card=$(awk '/USB/ && $1 ~ /^[0-9]+$/ {print $1; exit}' /proc/asound/cards 2>/dev/null || true)
if [ -z "${card:-}" ]; then
  echo "audio-dmix-refresh: no USB audio card present" >&2
  exit 1
fi
# Same filesystem as the target, so the mv below is a real atomic rename.
# mktemp's default /tmp may be a different mount, where mv degrades to
# copy+unlink and a udev timeout could leave a truncated asound.conf.
tmp=$(mktemp /etc/asound.conf.XXXXXX)
cat > "$tmp" <<CONF
# GENERATED by audio-dmix-refresh -- do not edit. USB DAC detected as card ${card}.
pcm.!default { type plug; slave.pcm "dmixer" }
pcm.dmixer {
  type dmix
  ipc_key 2048
  ipc_perm 0666
  slave { pcm "hw:${card},0"; rate 48000; channels 2; period_size 1024; buffer_size 8192 }
}
ctl.!default { type hw; card ${card} }
CONF
# Only replace when it actually changed, so we do not churn the file (and the
# dmix ipc segment) on every service restart.
if ! cmp -s "$tmp" /etc/asound.conf 2>/dev/null; then
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
/usr/local/bin/audio-dmix-refresh || { echo "snapclient-autodev: no USB audio card yet"; exit 1; }
exec /usr/bin/snapclient --logsink=system --host "\$HOST" -s default --mixer software
EOF
chmod 755 /usr/local/bin/snapclient-autodev

mkdir -p /etc/systemd/system/snapclient.service.d
cat > /etc/systemd/system/snapclient.service.d/override.conf <<'EOF'
[Unit]
# retry indefinitely while no DAC is present (default burst limit gives up)
StartLimitIntervalSec=0
[Service]
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

# ---- 8. enable + (re)start --------------------------------------------------
systemctl daemon-reload
udevadm control --reload-rules || true
systemctl enable --now go-librespot
systemctl enable snapclient
systemctl restart snapclient || true

log "done — node '${NODE_NAME}': snapclient=$(systemctl is-active snapclient) go-librespot=$(systemctl is-active go-librespot)"
log "plug a USB DAC in and both bind automatically (dmix-shared)."
