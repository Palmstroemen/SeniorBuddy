#!/usr/bin/env bash
# Tailscale-Einrichtung - bewusst getrennt von deploy/setup.sh und zuerst
# auszufuehren: dieser Schritt braucht eine aktive Anmeldung im Browser,
# der Rest (venv, Ollama, Modelle) laeuft danach komplett unbeaufsichtigt
# durch. Einmal hier eingerichtet, ist der Server ab sofort per Tailscale
# erreichbar - auch waehrend der langen Modell-Downloads in setup.sh,
# unabhaengig von lokalem WLAN/Router-Eigenheiten.
set -euo pipefail

echo "== Tailscale installieren (fuer Fernzugriff OHNE offene Ports) =="
if command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale ist bereits installiert."
else
  sudo -v
  curl -fsSL https://tailscale.com/install.sh | sudo sh
fi

echo ""
echo "== Anmeldung (oeffnet gleich einen Link zum Einloggen im Browser) =="
sudo tailscale up

echo ""
echo "== Fertig. =="
echo "Den Dienst selbst gibst du erst NACH dem App-Setup frei (deploy/setup.sh),"
echo "dann per:"
echo "  sudo tailscale serve --bg 8000"
echo "Jedes Tablet installiert ebenfalls die Tailscale-App und tritt"
echo "demselben Tailnet bei - siehe README.md, Abschnitt 'Fernzugriff'."
echo ""
echo "Weiter geht's mit: deploy/setup.sh"
