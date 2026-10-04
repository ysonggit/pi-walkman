#!/usr/bin/env bash
# Install the Walkman: go-librespot (Spotify Connect) + cassette screen.
# Runs as the desktop user, no sudo. Safe to re-run.
#   ./install.sh                # audio to the 3.5mm headphone jack
#   SINK=<pipewire node.name> ./install.sh   # some other output
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
CONF="$HOME/.config/go-librespot"
UNIT="$HOME/.config/systemd/user"

step() { printf '\n==> %s\n' "$*"; }

step "go-librespot binary"
case "$(uname -m)" in
  aarch64) ARCH=arm64 ;;
  armv6l|armv7l) ARCH=armv6_rpi ;;
  x86_64) ARCH=x86_64 ;;
  *) echo "unsupported arch $(uname -m)"; exit 1 ;;
esac
mkdir -p "$BIN"
curl -fsSL "https://github.com/devgianlu/go-librespot/releases/latest/download/go-librespot_linux_${ARCH}.tar.gz" \
  | tar xz -C "$BIN" go-librespot
"$BIN/go-librespot" --help >/dev/null 2>&1 || true
echo "installed $BIN/go-librespot"

step "audio output"
if [[ -z "${SINK:-}" ]]; then
  for id in $(wpctl status | sed -n '/Sinks:/,/Sources:/p' | grep -oE '[0-9]+\.' | tr -d .); do
    if wpctl inspect "$id" | grep -q 'Headphones'; then
      SINK=$(wpctl inspect "$id" | sed -n 's/.*node\.name = "\(.*\)"/\1/p')
      break
    fi
  done
fi
[[ -n "${SINK:-}" ]] || { echo "headphone sink not found; set SINK=... (see: wpctl status)"; exit 1; }
echo "sink: $SINK"

step "go-librespot config ($CONF/config.yml)"
mkdir -p "$CONF"
[[ -f $CONF/config.yml ]] && cp "$CONF/config.yml" "$CONF/config.yml.bak"
cat > "$CONF/config.yml" <<EOF
device_name: Walkman
device_type: speaker
audio_backend: pulseaudio
audio_device: $SINK
bitrate: 320
zeroconf_enabled: true
credentials:
  type: interactive
  interactive:
    callback_port: 36842
server:
  enabled: true
  address: localhost
  port: 3678
EOF

step "go-librespot user service"
mkdir -p "$UNIT"
cat > "$UNIT/go-librespot.service" <<EOF
[Unit]
Description=go-librespot (Spotify Connect: Walkman)
After=network-online.target pipewire-pulse.service
Wants=pipewire-pulse.service

[Service]
ExecStart=$BIN/go-librespot
Restart=always
RestartSec=5
StandardOutput=append:/tmp/go-librespot.log
StandardError=append:/tmp/go-librespot.log

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable go-librespot.service >/dev/null
systemctl --user restart go-librespot.service

step "cassette screen autostart"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/walkman.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Walkman
Exec=python3 $HERE/walkman.py${WALKMAN_FLIP:+ --flip}
X-GNOME-Autostart-enabled=true
EOF

step "done"
echo "First time only: sign in from your computer's browser. On the computer run"
echo "  ssh -N -L 36842:127.0.0.1:36842 $USER@$(hostname).local"
echo "then open the accounts.spotify.com link from: grep -o 'https://accounts[^ \"]*' /tmp/go-librespot.log"
echo "Then Spotify (app or open.spotify.com) -> Devices -> 'Walkman'. The screen starts on next login/reboot,"
echo "or now with: DISPLAY=:1 python3 $HERE/walkman.py &"
