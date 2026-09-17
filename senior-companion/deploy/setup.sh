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
echo "== Fertig. Start zum Testen: =="
echo "  cd server && source .venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port 8000"
echo ""
echo "Fuer Dauerbetrieb: deploy/senior-companion.service nach /etc/systemd/system/ kopieren,"
echo "Pfade darin anpassen, dann: sudo systemctl enable --now senior-companion"
