"""
Persistierte Admin-Einstellungen, die zur Laufzeit per authentifizierter
API (main.py, /admin/config/*) geaendert werden koennen - ueberleben
einen Neustart, anders als eine reine In-Memory-Aenderung an config.py's
Modul-globalen Variablen.
"""
import json

from config import DATA_DIR

SETTINGS_FILE = DATA_DIR / "admin_settings.json"

# Update-Marker: main.py schreibt nur diese Datei, ein separates
# systemd-.path-Unit fuehrt den eigentlichen Update aus - siehe
# deploy/run_update.sh. Der FastAPI-Prozess selbst bekommt dadurch nie
# Rechte fuer git pull/systemctl restart.
UPDATE_MARKER_FILE = DATA_DIR / ".update_requested"
UPDATE_LOG_FILE = DATA_DIR / ".update_log"


def load() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def update(key: str, value):
    settings = load()
    settings[key] = value
    SETTINGS_FILE.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8"
    )
