"""
Naechtlicher Hintergrund-Job: klassifiziert noch unklassifizierte
Nutzer-Nachrichten per lokalem Modell (Stimmung + Zustimmung/
Widerspruch) und schreibt das Ergebnis in messages.sentiment/.stance.

Bewusst per LLM statt per Stichwort-Heuristik (anders als
security.py/analysis.py) - genauer bei natuerlicher Sprache, dafuer
als Hintergrund-Job statt im Live-Chat-Pfad, damit die zusaetzliche
Latenz niemanden beim eigentlichen Gespraech stoert. Laeuft als
Low-Priority-Task (priority.py): eine Senior-Anfrage bricht ihn
jederzeit ab, der naechste Lauf macht weiter, weil jede Nachricht
einzeln (nicht als grosse Transaktion) klassifiziert wird.
"""
import asyncio
import logging

import config
import llm_client
import memory
import priority

log = logging.getLogger("sentiment_job")

# Ein kleines, ohnehin dauerhaft geladenes Modell reicht fuer diese
# Klassifikationsaufgabe - das grosse Professor-Modell waere hier
# unnoetig langsam.
CLASSIFICATION_MODEL = config.PERSONAS["freundin"].model

# Bounded pro Lauf, damit ein sehr grosser Nachschub nicht einen
# einzelnen naechtlichen Lauf unbegrenzt lange blockiert - der Rest
# wird beim naechsten Lauf weiterbearbeitet.
MAX_MESSAGES_PER_RUN = 200

CLASSIFY_PROMPT = (
    "Du bist ein Klassifikations-Werkzeug, keine Gespraechsperson. "
    "Lies die folgende Nachricht einer aelteren Person an ihre "
    "Gespraechsperson und antworte AUSSCHLIESSLICH in genau diesem "
    "Format, ohne weitere Worte:\n"
    "STIMMUNG: positiv|neutral|negativ\n"
    "HALTUNG: zustimmung|widerspruch|keine\n\n"
    "Nachricht: {text}"
)

_SENTIMENT_VALUES = {"positiv", "neutral", "negativ"}
_STANCE_VALUES = {"zustimmung", "widerspruch"}


def _parse_classification(raw: str) -> tuple[str, str | None]:
    """Robust gegen Fehlformat: faellt auf sentiment='neutral',
    stance=None zurueck, statt zu crashen - ein kleines lokales Modell
    haelt sich nicht immer exakt ans vorgegebene Format."""
    sentiment = "neutral"
    stance = None
    for line in raw.splitlines():
        line = line.strip().lower()
        if line.startswith("stimmung:"):
            value = line.split(":", 1)[1].strip()
            if value in _SENTIMENT_VALUES:
                sentiment = value
        elif line.startswith("haltung:"):
            value = line.split(":", 1)[1].strip()
            if value in _STANCE_VALUES:
                stance = value
    return sentiment, stance


async def _classify_one(user_id: str, message_id: int, text: str):
    raw = await llm_client.generate(
        CLASSIFICATION_MODEL, CLASSIFY_PROMPT.format(text=text), [], max_tokens=20,
    )
    sentiment, stance = _parse_classification(raw)
    memory.set_message_classification(user_id, message_id, sentiment, stance)


async def _run():
    for db_path in sorted(config.DATA_DIR.glob("*.sqlite3")):
        user_id = db_path.stem
        for row in memory.unclassified_messages(user_id, limit=MAX_MESSAGES_PER_RUN):
            await _classify_one(user_id, row["id"], row["content"])


async def classify_pending_messages():
    task = asyncio.current_task()
    priority.register_low_priority_task(task)
    try:
        await _run()
    except asyncio.CancelledError:
        log.info(
            "Sentiment-Klassifikation abgebrochen (Senior-Anfrage hat "
            "Vorrang) - naechster Lauf macht weiter."
        )
        raise
    finally:
        priority.unregister_low_priority_task(task)
