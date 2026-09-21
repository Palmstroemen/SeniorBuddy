#!/usr/bin/env bash
# Ersteinrichtung auf dem Mini-PC-Server (Linux).
# Setzt voraus: Python 3.11+ (Debian/Ubuntu-Paket, Ollama und das venv-Modul
# werden hier automatisch nachinstalliert, falls sie fehlen).
# Empfehlung: vorher deploy/setup_tailscale.sh ausfuehren - dann ist der
# Server schon waehrend der folgenden, langen Ollama-Downloads per
# Tailscale erreichbar, unabhaengig von lokalem WLAN/Router.
set -euo pipefail

echo "== sudo-Berechtigung einmal vorab einholen =="
echo "(bleibt fuer den kompletten Skriptlauf gueltig, auch ueber lange"
echo "Ollama-Modell-Downloads hinweg - kein erneutes Passwort mittendrin)"
sudo -v
( while true; do sudo -n true; sleep 60; kill -0 "$$" 2>/dev/null || exit; done ) &
SUDO_KEEPALIVE_PID=$!
trap 'kill "$SUDO_KEEPALIVE_PID" 2>/dev/null' EXIT

cd "$(dirname "$0")/../server"

echo "== python3-venv sicherstellen (auf frischen Debian/Ubuntu-Systemen oft nicht vorinstalliert) =="
sudo apt-get update -qq
sudo apt-get install -y python3-venv

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "== Ollama installieren (falls noch nicht vorhanden) =="
if command -v ollama >/dev/null 2>&1; then
  echo "Ollama ist bereits installiert."
else
  curl -fsSL https://ollama.com/install.sh | sh
fi

echo ""
echo "== Modelle in Ollama laden (kann je nach Groesse dauern) =="
ollama pull qwen2.5:7b-instruct
ollama pull qwen2.5:32b-instruct

echo ""
if command -v tailscale >/dev/null 2>&1; then
  echo "== Tailscale ist installiert - Dienst jetzt im Tailnet freigeben =="
  echo "  sudo tailscale serve --bg 8000"
else
  echo "== Tailscale fehlt noch - siehe deploy/setup_tailscale.sh =="
fi
echo "(kein Port nach aussen/LAN offen - siehe README.md, Abschnitt 'Fernzugriff')"

echo ""
echo "== Fertig. Start zum Testen (nur lokal auf diesem Rechner erreichbar): =="
echo "  cd server && source .venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 8000"
echo ""
echo "Fuer Dauerbetrieb: deploy/senior-companion.service nach /etc/systemd/system/ kopieren,"
echo "Pfade darin anpassen, dann: sudo systemctl enable --now senior-companion"
echo "Der Dienst bindet dort bereits gehaertet an 127.0.0.1 - Freigabe laeuft"
echo "ausschliesslich ueber 'tailscale serve', siehe README.md."
echo ""
echo "== Fernwartungs-API (optional) =="
echo "Token erzeugen und als SENIOR_COMPANION_ADMIN_TOKEN setzen (siehe README,"
echo "Abschnitt 'Fernwartungs-API'). Fuer 'Updates einspielen' zusaetzlich:"
echo "  sudo cp deploy/senior-companion-updater.{path,service} /etc/systemd/system/"
echo "  sudo systemctl enable --now senior-companion-updater.path"
