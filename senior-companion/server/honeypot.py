"""
Honeypot: zwei Koeder, die kein legitimer Teil der App je beruehrt -
jede Beruehrung loest sofort einen Push-Alarm ueber ntfy.sh aus.

1. Eine Honeyfile (server/data/.honeypot/zugangsdaten.txt), ueberwacht
   per echtem inotify (nicht atime-Polling - viele Systeme mounten
   inzwischen mit noatime/relatime, wo ein Lesezugriff die Zugriffszeit
   gar nicht mehr veraendert; inotify hakt sich dagegen direkt in den
   read()-Syscall-Pfad ein und bemerkt den Zugriff auch dann).
2. Ein paar Koeder-HTTP-Routen (main.py), die nach typischen
   Angriffszielen klingen.
"""
import logging
import os
import threading
import time

import httpx
from inotify_simple import INotify, flags

from config import DATA_DIR

log = logging.getLogger("honeypot")

NTFY_TOPIC = os.environ.get("SENIOR_COMPANION_NTFY_TOPIC", "")

_trigger_count = 0
_last_triggered_at: float | None = None

HONEYPOT_DIR = DATA_DIR / ".honeypot"
HONEYFILE = HONEYPOT_DIR / "zugangsdaten.txt"
_HONEYFILE_CONTENT = (
    "Notfall-Zugang (nur fuer Wartungsfaelle):\n"
    "Benutzer: admin\n"
    "Passwort: siehe Passwort-Manager der Familie\n"
)

_WATCH_FLAGS = (
    flags.OPEN | flags.ACCESS | flags.MODIFY | flags.ATTRIB
    | flags.DELETE_SELF | flags.MOVE_SELF
)


def alert(message: str):
    global _trigger_count, _last_triggered_at
    _trigger_count += 1
    _last_triggered_at = time.time()
    log.critical("HONEYPOT AUSGELOEST: %s", message)
    if not NTFY_TOPIC:
        log.warning(
            "SENIOR_COMPANION_NTFY_TOPIC nicht gesetzt - Alarm bleibt nur im Log."
        )
        return
    try:
        httpx.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            content=message.encode("utf-8"),
            headers={
                "Title": "Honeypot ausgeloest",
                "Priority": "urgent",
                "Tags": "rotating_light",
            },
            timeout=5.0,
        )
    except httpx.HTTPError:
        log.exception("Honeypot-Alarm konnte nicht gesendet werden")


def trigger_count() -> int:
    """Wie oft der Honeypot ausgeloest hat - fuer die Statistik-Route
    in main.py."""
    return _trigger_count


def last_triggered_at() -> float | None:
    return _last_triggered_at


def ensure_honeyfile():
    HONEYPOT_DIR.mkdir(parents=True, exist_ok=True)
    if not HONEYFILE.exists():
        HONEYFILE.write_text(_HONEYFILE_CONTENT, encoding="utf-8")


def _watch_loop(stop_event: threading.Event):
    inotify = INotify()
    inotify.add_watch(str(HONEYFILE), _WATCH_FLAGS)
    try:
        while not stop_event.is_set():
            if inotify.read(timeout=1000):  # ms; Timeout nur zum stop_event-Poll
                alert(f"Honeyfile beruehrt: {HONEYFILE}")
    finally:
        inotify.close()


def start_watching(stop_event: threading.Event | None = None) -> threading.Thread:
    ensure_honeyfile()  # ERST anlegen, DANN Watch registrieren - sonst
                        # triggert das eigene Anlegen den Alarm
    thread = threading.Thread(
        target=_watch_loop, args=(stop_event or threading.Event(),), daemon=True
    )
    thread.start()
    return thread
