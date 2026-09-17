"""
Prioritaets-Verwaltung: Senior-Gespraeche (hohe Prioritaet) duerfen nie
hinter einer Anfrage des Ausserordentlichen Nutzers (niedrige Prioritaet,
server/main.py's /ws/raw) warten. Ollama selbst kennt keine
Prioritaeten und verarbeitet Generierungsanfragen an ein Modell faktisch
seriell - deshalb bricht ein neuer Senior-Stream eine laufende
Niedrig-Prioritaets-Generierung aktiv ab, statt nur neue abzuweisen.
"""
import asyncio

_active_senior_streams = 0
_low_priority_tasks: set = set()
_idle_event = asyncio.Event()
_idle_event.set()


def senior_stream_started():
    global _active_senior_streams
    _active_senior_streams += 1
    _idle_event.clear()
    for task in list(_low_priority_tasks):
        task.cancel()


def senior_stream_finished():
    global _active_senior_streams
    _active_senior_streams = max(0, _active_senior_streams - 1)
    if _active_senior_streams == 0:
        _idle_event.set()


def senior_stream_active() -> bool:
    return _active_senior_streams > 0


async def wait_until_idle():
    await _idle_event.wait()


def register_low_priority_task(task):
    """Registriert eine laufende Low-Priority-Generierung. Bricht sie
    SOFORT ab, falls zwischen Idle-Check und Registrierung bereits ein
    Senior-Stream gestartet ist - schliesst die Race Condition, die eine
    unregistrierte Aufgabe sonst fuer ihre GESAMTE Laufzeit ungeschuetzt
    liesse."""
    _low_priority_tasks.add(task)
    if senior_stream_active():
        task.cancel()


def unregister_low_priority_task(task):
    _low_priority_tasks.discard(task)
