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
# Nur die Verzeichnisse, in die die Dienste wirklich schreiben
# (ReadWritePaths= in den Units) - der Rest des Repos bleibt bei der
# Person, die per git pull aktualisiert.
mkdir -p "$APP_DIR/server/data" "$APP_DIR/speech-service/voices"
chown -R senior-companion:senior-companion "$APP_DIR/server/data" "$APP_DIR/speech-service/voices"

# Der Dienst-User liest per git rev-parse den laufenden Commit
# (/api/version) - ohne das scheitert das an gits "dubious ownership".
REPO_DIR="$(dirname "$APP_DIR")"
if ! git config --system --get-all safe.directory 2>/dev/null | grep -qxF "$REPO_DIR"; then
  git config --system --add safe.directory "$REPO_DIR"
fi

echo ""
echo "== Service-Unit installieren und starten =="
cp "$DEPLOY_DIR/senior-companion.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now senior-companion

echo ""
echo "== Fertig. Status: =="
systemctl status senior-companion --no-pager
