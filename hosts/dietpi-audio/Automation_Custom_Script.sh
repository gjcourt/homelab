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

log() { echo "[dietpi-audio] $*"; }

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

# ---- 3. shared dmix so snapclient + go-librespot both reach the USB DAC ------
# hw:0 = the USB DAC (the only sound card on a headless audio node). If onboard
# audio ever appears, revisit (dmix would need to follow the USB card index).
cat > /etc/asound.conf <<'EOF'
pcm.!default { type plug; slave.pcm "dmixer" }
pcm.dmixer {
  type dmix
  ipc_key 2048
  ipc_perm 0666
  slave { pcm "hw:0,0"; rate 48000; channels 2; period_size 1024; buffer_size 8192 }
}
ctl.!default { type hw; card 0 }
EOF

# ---- 4. snapclient: auto-detect wrapper + robust systemd override ------------
cat > /usr/local/bin/snapclient-autodev <<EOF
#!/bin/bash
# Wait for a USB DAC, then run snapclient through the shared dmix 'default'
# device so it coexists with go-librespot. systemd retries until a DAC appears.
HOST="\${SNAPSERVER_HOST:-${SNAPSERVER_HOST}}"
card=\$(awk '/USB/ && \$1 ~ /^[0-9]+\$/ {print \$1; exit}' /proc/asound/cards)
[ -z "\$card" ] && { echo "snapclient-autodev: no USB audio card yet"; exit 1; }
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
ExecStart=/usr/local/bin/go-librespot --config_dir /root/.config/go-librespot
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

# ---- 6. udev: restart BOTH on DAC plug/unplug -------------------------------
cat > /etc/udev/rules.d/99-audio-node.rules <<'EOF'
ACTION=="add",    SUBSYSTEM=="sound", KERNEL=="card*", RUN+="/bin/systemctl --no-block restart snapclient go-librespot"
ACTION=="remove", SUBSYSTEM=="sound", KERNEL=="card*", RUN+="/bin/systemctl --no-block restart snapclient go-librespot"
EOF

# ---- 7. enable + (re)start --------------------------------------------------
systemctl daemon-reload
udevadm control --reload-rules || true
systemctl enable --now go-librespot
systemctl enable snapclient
systemctl restart snapclient || true

log "done — node '${NODE_NAME}': snapclient=$(systemctl is-active snapclient) go-librespot=$(systemctl is-active go-librespot)"
log "plug a USB DAC in and both bind automatically (dmix-shared)."
