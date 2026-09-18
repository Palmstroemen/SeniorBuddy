#!/usr/bin/env bash
# Ersteinrichtung auf dem Mini-PC-Server (Linux).
# Setzt voraus: Python 3.11+, Ollama bereits installiert.
set -euo pipefail

cd "$(dirname "$0")/../server"

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "== Modelle in Ollama laden (kann je nach Groesse dauern) =="
ollama pull qwen2.5:7b-instruct
ollama pull qwen2.5:32b-instruct

echo ""
echo "== Tailscale installieren (fuer Fernzugriff OHNE offene Ports) =="
if command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale ist bereits installiert."
else
  curl -fsSL https://tailscale.com/install.sh | sh
fi
echo "Noch manuell noetig (einmalig, oeffnet einen Login-Link im Browser):"
echo "  sudo tailscale up"
echo "Danach den Dienst nur ueber den Tailnet freigeben (kein Port nach"
echo "aussen/LAN offen) - siehe README.md, Abschnitt 'Fernzugriff'."

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
