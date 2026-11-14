#!/usr/bin/env bash
# Wird NUR durch senior-companion-updater.path ausgeloest (Aenderung an
# server/data/.update_requested), nie direkt aufgerufen und nie vom
# Netz erreichbar. Laeuft als root, siehe
# senior-companion-updater.service fuer die Begruendung.
#
# Root wird aber NUR fuer "systemctl restart" gebraucht. git pull und
# pip install laufen als Besitzer:in des Repos (z. B. oliver) - liefen
# sie als root, gehoerten alle dabei neu geschriebenen Dateien root, und
# der naechste manuelle "git pull" als oliver scheiterte mit
# "insufficient permission for adding an object to repository database".
set -euo pipefail

cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
REPO_DIR="$(git rev-parse --show-toplevel)"
LOG="server/data/.update_log"

# Besitzer:in des Repos = wem .git gehoert (nicht hart "oliver", damit
# das Skript auch auf einem anders eingerichteten Rechner stimmt).
OWNER="$(stat -c %U "$REPO_DIR/.git")"
OWNER_GROUP="$(stat -c %G "$REPO_DIR/.git")"
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"

as_owner() {
  # HOME explizit setzen: sonst sieht git/pip root's HOME (/root), das
  # fuer OWNER nicht lesbar ist.
  runuser -u "$OWNER" -- env HOME="$OWNER_HOME" "$@"
}

{
  echo "=== Update gestartet: $(date -Iseconds) (als $OWNER) ==="

  # Selbstheilung: Dateien, die frueher ein als root laufendes Update
  # angelegt hat, wieder OWNER zurueckgeben. Ausgenommen sind die
  # Verzeichnisse, die bewusst dem Dienst-User gehoeren (ReadWritePaths=
  # in senior-companion.service / speech-service.service).
  find "$REPO_DIR" \
    \( -path "$APP_DIR/server/data" -o -path "$APP_DIR/speech-service/voices" \) -prune \
    -o ! -user "$OWNER" -exec chown -h "$OWNER:$OWNER_GROUP" {} +

  as_owner git -C "$REPO_DIR" pull --ff-only

  as_owner server/.venv/bin/pip install -q -r server/requirements.txt

  if [ -d speech-service/.venv ]; then
    as_owner speech-service/.venv/bin/pip install -q -r speech-service/requirements.txt
  fi

  systemctl restart senior-companion
  systemctl restart speech-service 2>/dev/null || true

  echo "=== Update abgeschlossen: $(date -Iseconds) ==="
} >> "$LOG" 2>&1
