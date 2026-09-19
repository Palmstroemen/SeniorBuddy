"""
Anwesenheits-Tracking fuer den Gruppenchat: welche Personas sind gerade
"im Raum" einer Person, und wird jemand ausdruecklich angesprochen?

Anwesenheit ist reiner Prozessspeicher (kein SQLite) - analog zu
priority.py's modulglobalem Zustand fuer sitzungsgebundene Dinge. Ein
Serverneustart setzt den Raum einfach zurueck; er fuellt sich mit den
naechsten paar Gespraechsschritten von selbst wieder. Ein Raum startet
leer - eine Persona wird anwesend, sobald sie angesprochen oder vom
Regisseur ausgewaehlt wird (kein "beim Start alle laden"-Automatismus).
"""
import re
import time

PRESENCE_TIMEOUT_SECONDS = 900  # 15 Minuten

# user_id -> {persona_id: letzter Aktivitaets-Zeitstempel}
_present: dict[str, dict[str, float]] = {}


def touch(user_id: str, persona_id: str, now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    _present.setdefault(user_id, {})[persona_id] = ts


def present_personas(
    user_id: str, timeout_seconds: float = PRESENCE_TIMEOUT_SECONDS, now: float | None = None
) -> list[str]:
    now = now if now is not None else time.time()
    entries = _present.get(user_id, {})
    return [p for p, ts in entries.items() if now - ts <= timeout_seconds]


def is_present(
    user_id: str, persona_id: str,
    timeout_seconds: float = PRESENCE_TIMEOUT_SECONDS, now: float | None = None,
) -> bool:
    return persona_id in present_personas(user_id, timeout_seconds, now)


def last_active_ts(user_id: str, persona_id: str) -> float | None:
    return _present.get(user_id, {}).get(persona_id)


# --- Ansprache-Erkennung ---------------------------------------------------
#
# Nur EIN kurzer, bekannter Vorspann wird toleriert ("Aber Marianne...",
# "Und Wallner..."), nicht ein beliebiges Wort davor - sonst wuerde z.B.
# "Ich glaube, Wallner hat recht" faelschlich als Ansprache gelten.

_LEADING_INTERJECTIONS = r"(?:aber|und|na|moment|hey|sag mal)"


def _addressing_pattern(name: str) -> re.Pattern:
    return re.compile(
        rf"^\s*(?:{_LEADING_INTERJECTIONS}[,\s]+)?{re.escape(name)}\b", re.I
    )


def detect_addressed_persona(user_text: str, candidates: dict[str, str]) -> str | None:
    """candidates: persona_id -> Anzeigename. Gibt die persona_id der
    Person zurueck, die am Satzanfang (mit hoechstens einem kurzen
    Vorspann) angesprochen wird, sonst None."""
    for persona_id, name in candidates.items():
        if _addressing_pattern(name).match(user_text):
            return persona_id
    return None
