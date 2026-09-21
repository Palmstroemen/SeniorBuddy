#!/usr/bin/env bash
# Richtet senior-companion als dauerhaften systemd-Dienst ein: passt die
# /opt/senior-companion-Pfade in den Unit-Dateien auf den tatsaechlichen
# Ort (/opt/SeniorBuddy/senior-companion) an, legt den dedizierten
# Systembenutzer an, installiert + startet die Service-Unit.
# Muss als root laufen (sudo ./setup_service.sh). Idempotent - kann
# gefahrlos mehrfach ausgefuehrt werden.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo ausfuehren: sudo $0" >&2
  exit 1
fi

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(dirname "$DEPLOY_DIR")"

echo "== Pfade in den Unit-Dateien sicherstellen (/opt/senior-companion -> $APP_DIR) =="
sed -i "s#/opt/senior-companion#${APP_DIR}#g" \
  "$DEPLOY_DIR/senior-companion.service" \
  "$DEPLOY_DIR/senior-companion-updater.service" \
  "$DEPLOY_DIR/senior-companion-updater.path"

echo ""
echo "== Dedizierten Systembenutzer sicherstellen =="
if id senior-companion >/dev/null 2>&1; then
  echo "Benutzer 'senior-companion' existiert bereits."
else
  useradd --system --no-create-home --shell /usr/sbin/nologin senior-companion
fi
chown -R senior-companion:senior-companion "$APP_DIR"

echo ""
echo "== Service-Unit installieren und starten =="
cp "$DEPLOY_DIR/senior-companion.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now senior-companion

echo ""
echo "== Fertig. Status: =="
systemctl status senior-companion --no-pager
