#!/usr/bin/env bash
# Wird NUR durch senior-companion-updater.path ausgeloest (Aenderung an
# server/data/.update_requested), nie direkt aufgerufen und nie vom
# Netz erreichbar. Laeuft als root, siehe
# senior-companion-updater.service fuer die Begruendung.
set -euo pipefail

cd "$(dirname "$0")/.."
LOG="server/data/.update_log"

{
  echo "=== Update gestartet: $(date -Iseconds) ==="

  git pull

  (
    cd server
    source .venv/bin/activate
    pip install -q -r requirements.txt
  )

  if [ -d speech-service/.venv ]; then
    (
      cd speech-service
      source .venv/bin/activate
      pip install -q -r requirements.txt
    )
  fi

  systemctl restart senior-companion
  systemctl restart speech-service 2>/dev/null || true

  echo "=== Update abgeschlossen: $(date -Iseconds) ==="
} >> "$LOG" 2>&1
