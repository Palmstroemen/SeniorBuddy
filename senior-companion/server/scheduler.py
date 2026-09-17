"""
Terminplanung fuer den Professor + Hintergrund-Recherche-Jobs.

In Phase 1 (2 Nutzer, ein Professor-Modell, das ohnehin dauerhaft
geladen bleibt) tut dieser Scheduler wenig - er ist bewusst als
Grundgeruest angelegt, damit spaeter (mehr Nutzer, Vorwaerm-Logik,
Prioritaets-Warteschlange) nicht neu gebaut werden muss, sondern nur
Jobs ergaenzt werden.
"""
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import logging

import llm_client
from config import PERSONAS

log = logging.getLogger("scheduler")
scheduler = AsyncIOScheduler()

# Beispiel: taeglicher Besuchstermin. In Phase 2 kommt hier pro
# Nutzer:in ein eigener, konfigurierbarer Termin dazu.
DAILY_VISIT_HOUR = 16
DAILY_VISIT_MINUTE = 0


async def prewarm_professor():
    """Wird kurz vor dem taeglichen Termin ausgefuehrt, damit das Modell
    beim tatsaechlichen Gespraechsbeginn schon 'wach' ist. Bei
    always_loaded=True (Standard in Phase 1) ist das ein No-Op, aber
    die Funktion existiert schon, wenn spaeter Modelle bei Bedarf
    nachgeladen werden."""
    professor = PERSONAS["professor"]
    if professor.always_loaded:
        log.info("Professor ist dauerhaft geladen, kein Vorwaermen noetig.")
        return
    available = await llm_client.is_model_available(professor.model)
    if not available:
        log.info("Lade Professor-Modell vor: %s", professor.model)
        # Ein leerer Prompt "waermt" die Runtime auf (Modell in
        # den Speicher laden), ohne dass eine sichtbare Antwort noetig ist.
        async for _ in llm_client.stream(professor.model, "Antworte mit OK.",
                                          [{"role": "user", "content": "OK?"}],
                                          max_tokens=5):
            pass


def _prewarm_time() -> tuple[int, int]:
    """Berechnet Stunde/Minute 10 Minuten vor dem taeglichen Termin,
    korrekt auch bei Stundenuebergang (z.B. Termin um 16:05 -> 15:55)."""
    reference = datetime(2000, 1, 1, DAILY_VISIT_HOUR, DAILY_VISIT_MINUTE)
    prewarm = reference - timedelta(minutes=10)
    return prewarm.hour, prewarm.minute


def setup_scheduler():
    hour, minute = _prewarm_time()
    scheduler.add_job(
        prewarm_professor,
        CronTrigger(hour=hour, minute=minute),
        id="prewarm_professor",
        replace_existing=True,
    )
    if not scheduler.running:
        scheduler.start()
    log.info("Scheduler gestartet (Vorwaermen taeglich um %02d:%02d).", hour, minute)


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
