#!/usr/bin/env bash
# Anankos FobGuard installer for Debian / Ubuntu / Raspberry Pi OS (64-bit).
# Usage: sudo ./deploy/install.sh            (run from the fobguard/ directory)
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX=/opt/anankos-fobguard
ETC=/etc/anankos-fobguard

echo "==> system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip rtl-sdr librtlsdr-dev

echo "==> keep the kernel DVB-T driver off the dongles"
cat > /etc/modprobe.d/anankos-rtlsdr-blacklist.conf <<'BL'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
BL
modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
install -m 0644 "$SRC/deploy/99-anankos-rtlsdr.rules" /etc/udev/rules.d/
udevadm control --reload-rules && udevadm trigger

echo "==> service user"
id fobguard >/dev/null 2>&1 || useradd --system --home /var/lib/anankos-fobguard --shell /usr/sbin/nologin fobguard
usermod -aG plugdev fobguard
install -d -o fobguard -g fobguard -m 0750 /var/lib/anankos-fobguard

echo "==> application"
# --system-site-packages: lets FobGuard use apt-installed SoapySDR bindings and plugins
python3 -m venv --system-site-packages "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$SRC[rtlsdr]"
ln -sf "$PREFIX/venv/bin/fobguard" /usr/local/bin/fobguard

echo "==> configuration"
install -d -m 0755 "$ETC"
if [[ ! -f "$ETC/config.toml" ]]; then
  "$PREFIX/venv/bin/fobguard" init-config "$ETC/config.toml"
fi
if [[ ! -f "$ETC/env" ]]; then
  umask 077
  echo "FOBGUARD_DASHBOARD_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" > "$ETC/env"
  echo "# FOBGUARD_SLACK_WEBHOOK=https://hooks.slack.com/services/..." >> "$ETC/env"
  echo "# FOBGUARD_SMTP_PASSWORD=..." >> "$ETC/env"
  chown root:fobguard "$ETC/env"; chmod 0640 "$ETC/env"
fi

install -m 0644 "$SRC/deploy/anankos-fobguard.service" /etc/systemd/system/
install -m 0644 "$SRC/deploy/anankos-fobguard-fix.service" /etc/systemd/system/
install -m 0644 "$SRC/deploy/anankos-fobguard-fix.path" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now anankos-fobguard-fix.path

echo "==> drivers & plugins for the receivers in config / plugged in"
FOBGUARD_SERVICE_USER=fobguard "$PREFIX/venv/bin/fobguard" doctor --config "$ETC/config.toml" --fix --yes || true

cat <<MSG

Installed. Next steps:
  1. Plug in the receivers and run:   sudo fobguard doctor --fix   (installs any drivers they need)
  2. Edit $ETC/config.toml (site name, hours, timezone, one [[monitor]] per receiver)
  3. Calibrate for 30 min in normal conditions:
       sudo -u fobguard fobguard calibrate -c $ETC/config.toml --minutes 30
  4. Start it:   systemctl enable --now anankos-fobguard
  5. Dashboard:  http://<this-host>:8742/?token=\$(grep TOKEN $ETC/env | cut -d= -f2)
MSG
