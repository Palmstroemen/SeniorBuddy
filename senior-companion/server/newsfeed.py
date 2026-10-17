"""
Naechtlicher Hintergrund-Job: fuer jede Persona mit zugewiesenen
RSS-Feeds (config.PersonaConfig.rss_feeds) werden die neuesten
Schlagzeilen abgerufen, per LLM durch die Linse von long_term_agenda
zu 2-4 Saetzen verdichtet und als daily_agenda gespeichert - main.py's
run_turn()/run_auto_turn() speisen das dann live in den Prompt-Aufbau
ein (Session-Notiz 2026-10-17).

Laeuft als Low-Priority-Task (priority.py), exakt wie
sentiment_job.classify_pending_messages(): eine Senior-Anfrage bricht
den Job jederzeit ab, der naechste naechtliche Lauf macht weiter - jede
Persona wird einzeln (nicht als grosse Transaktion) verarbeitet.

Bewusst GENERISCH: kein hartkodiertes Zuweisen von Zeitungen/Sendern im
Code - eine Persona ohne rss_feeds nimmt einfach nicht teil, kein
Sonderfall noetig. Die Gewichtung je Feed (0-100%) steuert MECHANISCH
(nicht nur per Prompt-Hinweis), wie viele Schlagzeilen dieses Feeds
ueberhaupt in den Auswahl-Pool gelangen - ein LLM haelt sich auf einen
reinen Text-Hinweis wie "80% Gewichtung" nicht zuverlaessig.
"""
import asyncio
import dataclasses
import logging

import feedparser
import httpx

import admin_settings
import config
import llm_client
import memory
import priority

log = logging.getLogger("newsfeed")

NEWSFEED_JOB_HOUR = 6
NEWSFEED_JOB_MINUTE = 0

MAX_ITEMS_PER_FEED = 8  # je Feed VOR Gewichtung abgerufen

NEWSFEED_SELECTION_PROMPT = (
    "Du bekommst eine Liste aktueller Schlagzeilen mit kurzer "
    "Beschreibung. Waehle 2-4 Themen aus, die am besten zu folgendem "
    "Interesse passen: {agenda} Fasse sie in 2-4 kurzen, gespraechigen "
    "Saetzen zusammen, so als haettest du das gerade selbst gelesen und "
    "wolltest gleich davon erzaehlen. Keine Aufzaehlung, keine "
    "Liste - ein natuerlicher kleiner Absatz auf Deutsch."
)
DEFAULT_AGENDA_FALLBACK = "aktuelle, fuer aeltere Menschen interessante Ereignisse"


async def fetch_feed(url: str) -> list[tuple[str, str]]:
    """Titel+Kurzbeschreibung der neuesten Eintraege - stiller Fallback
    auf leere Liste bei Netzwerkfehler/kaputtem Feed (gleiche Haltung
    wie speech_client.py/das Wetter-Plugin: ein einzelner fehlerhafter
    Feed darf nicht den gesamten Job abbrechen). feedparser statt
    selbstgebautem XML-Parsing - robust gegen die vielen
    Formatvarianten/kaputten Feeds echter Zeitungs-Feeds."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError:
        log.warning("Feed nicht erreichbar: %s", url)
        return []
    parsed = feedparser.parse(resp.text)
    return [
        (entry.get("title", ""), entry.get("summary", ""))
        for entry in parsed.entries[:MAX_ITEMS_PER_FEED]
        if entry.get("title")
    ]


def _weighted_item_count(weight: int) -> int:
    """Wie viele der abgerufenen Schlagzeilen dieses Feeds tatsaechlich
    in den Auswahl-Pool aufgenommen werden - linear zur Gewichtung,
    mindestens 1 bei weight > 0 (ein zu 20% gewichteter Feed soll noch
    spuerbar vorkommen, nicht praktisch verschwinden), hoechstens
    MAX_ITEMS_PER_FEED bei weight=100."""
    if weight <= 0:
        return 0
    return max(1, round(MAX_ITEMS_PER_FEED * weight / 100))


async def build_daily_agenda_for_persona(persona) -> str | None:
    """None, wenn keine Feeds zugewiesen sind oder alle Feeds leer/
    nicht erreichbar waren - main.py's run_turn()/run_auto_turn()
    injizieren dann einfach nichts Neues (die bisherige daily_agenda
    bleibt unveraendert, siehe build_daily_agendas())."""
    headlines = []
    for feed in persona.rss_feeds:
        items = await fetch_feed(feed["url"])
        count = _weighted_item_count(feed.get("weight", 100))
        headlines.extend(items[:count])
    if not headlines:
        return None
    headline_text = "\n".join(f"- {t}: {s}" for t, s in headlines)
    prompt = NEWSFEED_SELECTION_PROMPT.format(
        agenda=persona.long_term_agenda or DEFAULT_AGENDA_FALLBACK,
    )
    return await llm_client.generate(
        persona.model, prompt,
        [{"role": "user", "content": headline_text}], max_tokens=400,
    )


def _log_transparency(persona) -> None:
    """daily_agenda ist persona-/nicht nutzerspezifisch, aber jede(r)
    Nutzer:in, die mit dieser Persona spricht, bekommt davon etwas zu
    hoeren - fuer die Transparenz-Anzeige wird der Abruf daher gegen
    JEDEN bekannten Nutzer protokolliert (gleiches Iterations-Muster
    wie main.py's admin_feedback(): config.DATA_DIR.glob('*.sqlite3'))."""
    for db_path in config.DATA_DIR.glob("*.sqlite3"):
        memory.log_external_request(
            db_path.stem, f"newsfeed:{persona.id}",
            "Tagesagenda vorbereiten",
            {"feeds": [f["url"] for f in persona.rss_feeds]},
        )


async def build_daily_agendas() -> None:
    """Scheduler-Job (06:00, siehe scheduler.py) - fuer jede Persona MIT
    rss_feeds eine neue daily_agenda bauen. Persona ohne Feeds wird
    uebersprungen, ihre bestehende daily_agenda bleibt unveraendert.
    Low-Priority/Preemption exakt wie
    sentiment_job.classify_pending_messages()."""
    task = asyncio.current_task()
    priority.register_low_priority_task(task)
    try:
        changed = False
        for persona_id, persona in list(config.PERSONAS.items()):
            if not persona.rss_feeds:
                continue
            agenda = await build_daily_agenda_for_persona(persona)
            if agenda:
                # NICHT persona.daily_agenda direkt mutieren: PERSONAS-
                # Eintraege koennen (fuer unveraenderte Personas) dieselbe
                # Objekt-Referenz wie config._BUILTIN_PERSONAS sein (siehe
                # config.py: "_BUILTIN_PERSONAS = dict(PERSONAS)", eine
                # flache Kopie) - eine In-Place-Mutation wuerde dann auch
                # die "Zuruecksetzen auf Standard"-Vorlage dauerhaft
                # veraendern. Stattdessen, wie ueberall sonst im Projekt
                # (apply_persona_overrides()), ein NEUES Objekt einsetzen.
                updated = dataclasses.replace(persona, daily_agenda=agenda)
                config.PERSONAS[persona_id] = updated
                changed = True
                _log_transparency(updated)
        if changed:
            admin_settings.update(
                "personas", [p.to_dict() for p in config.PERSONAS.values()],
            )
    except asyncio.CancelledError:
        log.info("Tagesagenda-Job unterbrochen (Senior-Gespraech hat Vorrang)")
        raise  # wie sentiment_job.py: CancelledError sauber weiterreichen, nicht schlucken
    finally:
        priority.unregister_low_priority_task(task)
